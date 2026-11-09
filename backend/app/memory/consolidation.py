"""记忆离线固化（plan `2026-11-09-memory-admission-and-consolidation` D1/D3）。

分工：热路径（`app/memory/manager.remember_explicit_preference`）只做**确定且不能等**的写；
需要证据的判断放这里，一天跑一次：

1. **淘汰过期**：`expires_at <= now` 的 active（session 级临时偏好）→ expired；
2. **候选晋级**：`status='candidate' AND promoted_at IS NULL` 的队列逐条过准入闸，
   有作用域标记的晋级（user 无期限 / session 24h），没有标记的**继续留队**（`skipped`）——
   「攒跨会话证据再升级」是 D6 的事，要改 §五 冻结条款，本版不做；
3. **同维度冲突收敛**：同一维度（`app/memory/preferences.preference_dimension`）里只留最新
   的一条 active，旧的置 superseded（热路径已经做实时收敛，这里兜住历史脏数据）。

为什么不是「时间窗口」选活：候选池本身就是工作队列（`promoted_at IS NULL`），
天然可重跑、断点续跑不漏行；`consolidation_cursor` 只用于记录「上次成功跑到什么时候」，
供运维看「批处理落后了多久」，不参与选活。
"""
from __future__ import annotations

import datetime
import logging

from app.infra.memory.memory_manager import MemoryManager
from app.memory.admission import classify_scope
from app.memory.lifecycle import CONFIDENCE_EXPLICIT_STATEMENT
from app.memory.preferences import preference_dimension

logger = logging.getLogger(__name__)


async def consolidate(
    user_id: int | str,
    *,
    dry_run: bool = False,
    max_items: int = 200,
    now: datetime.datetime | None = None,
) -> dict:
    """对单个用户跑一次固化，返回记账字典（也写进 `memory.consolidation_run`）。

    `dry_run=True` 时**不写任何行**（不晋级、不过期、不 supersede），只返回「会发生什么」，
    用于上线前在真实库上先看一眼。
    """
    started = now or datetime.datetime.now()
    mm = MemoryManager()
    summary = {
        "user_id": int(user_id),
        "considered": 0,
        "promoted": 0,
        "expired": 0,
        "superseded": 0,
        "skipped": 0,
        "errors": 0,
        "dry_run": dry_run,
        "actions": [],
    }

    # --- 1. 到期淘汰 -----------------------------------------------------------
    try:
        if dry_run:
            rows = await mm.list_active_preference_rows(int(user_id))
            summary["expired"] = 0
            summary["note_expired_dry_run"] = len(rows)
        else:
            summary["expired"] = await mm.expire_due_preferences(int(user_id), now=started)
    except Exception as exc:  # noqa: BLE001 - 单个动作失败不该毁掉整轮
        summary["errors"] += 1
        logger.warning("consolidate: expire_due failed user=%s: %s", user_id, exc)

    # --- 2. 候选晋级 -----------------------------------------------------------
    try:
        candidates = await mm.list_candidates(int(user_id), limit=max_items)
    except Exception as exc:  # noqa: BLE001
        summary["errors"] += 1
        logger.warning("consolidate: list_candidates failed user=%s: %s", user_id, exc)
        candidates = []

    for row in candidates:
        summary["considered"] += 1
        adm = classify_scope(row["content"] or "")
        if not adm.writes_active:
            # 没有作用域标记：继续留队等证据（§五 不允许推断升级）
            summary["skipped"] += 1
            continue
        expires_at = (
            started + datetime.timedelta(hours=adm.ttl_hours) if adm.ttl_hours else None
        )
        summary["actions"].append({
            "entry_id": row["id"], "content": row["content"][:60],
            "scope": adm.scope, "memory_type": adm.memory_type,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "reason": adm.reason,
        })
        if dry_run:
            summary["promoted"] += 1
            continue
        try:
            await mm.promote_candidate(
                int(user_id), row["id"],
                memory_type=adm.memory_type, scope=adm.scope,
                confidence=CONFIDENCE_EXPLICIT_STATEMENT.value,
                expires_at=expires_at,
            )
            summary["promoted"] += 1
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            logger.warning("consolidate: promote failed id=%s: %s", row["id"], exc)

    # --- 3. 同维度冲突收敛 -----------------------------------------------------
    try:
        actives = await mm.list_active_preference_rows(int(user_id))
        by_dim: dict[str, list[dict]] = {}
        for row in actives:
            dim = preference_dimension(row["content"] or "")
            if dim:
                by_dim.setdefault(dim, []).append(row)
        stale_ids: list[int] = []
        for dim, rows in by_dim.items():
            if len(rows) < 2:
                continue
            rows.sort(key=lambda r: (r.get("created_at") or started))
            # 最新的一条留 active，其余 supersede（同维度只能有一条生效）
            stale = rows[:-1]
            stale_ids.extend(r["id"] for r in stale)
            summary["actions"].append({
                "dimension": dim,
                "supersede_ids": [r["id"] for r in stale],
                "keep_id": rows[-1]["id"],
                "reason": "same-dimension-conflict",
            })
        if stale_ids and not dry_run:
            summary["superseded"] = await mm.supersede_ids(
                int(user_id), stale_ids,
            )
        elif stale_ids:
            summary["superseded"] = len(stale_ids)
    except Exception as exc:  # noqa: BLE001
        summary["errors"] += 1
        logger.warning("consolidate: conflict sweep failed user=%s: %s", user_id, exc)

    # --- 4. 记账 ---------------------------------------------------------------
    note = f"actions={len(summary['actions'])}"
    if not dry_run:
        try:
            await mm.set_last_consolidation(int(user_id), started)
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            logger.warning("consolidate: cursor update failed user=%s: %s", user_id, exc)
    try:
        summary["run_id"] = await mm.record_consolidation_run(
            user_id=int(user_id),
            considered=summary["considered"],
            promoted=summary["promoted"],
            expired=summary["expired"],
            superseded=summary["superseded"],
            skipped=summary["skipped"],
            errors=summary["errors"],
            dry_run=dry_run,
            note=note,
            started_at=started,
        )
    except Exception as exc:  # noqa: BLE001
        summary["errors"] += 1
        logger.warning("consolidate: run record failed user=%s: %s", user_id, exc)

    return summary
