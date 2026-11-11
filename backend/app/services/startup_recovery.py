"""启动时的孤儿任务收敛（plan 2026-11-11-failure-recovery-durability P0）。

## 为什么需要

后台执行任务活在**进程内**（`infra/execution/registry` 的一个字典）。进程被杀，
任务就凭空消失，而库里只留下 `session.current_phase = 'generating'`：

- `last_failed_action` 是 NULL → 前端不认为这是失败，不会显示重试按钮；
- `agent.report_version` 没有行 → 版本历史里看不到「这次跑过、但没跑成」；
- 前端轮询只看 phase 是否离开「忙」相位 → **永远轮询，永远「生成中」**。

也就是说：这套设计原来默认进程不会死。本模块补上「死了也要留下证据」。

## 判定不是一刀切

扫描 `current_phase ∈ {parsing, generating, adjusting}` 的会话，逐条二分：

| 情况 | 动作 | 为什么 |
|---|---|---|
| 该 draft 已有 `status='done'` 的版本 | 只把 phase 修成 `report_ready` | 任务其实跑完了、只是 phase 没来得及更新（写完版本行与更新 phase 之间被杀） |
| 没有 | 落一行 `status='error'` + `error_kind='interrupted'` + `phase=error` + `last_failed_action=confirm` | 确实没跑成；前端据此显示 ErrorCard 与重试按钮 |

## 边界（重要）

**单实例假设。** 启动时本进程没有任何在跑的任务，所以「忙相位 = 孤儿」成立。若将来多实例
部署，这段扫描会误杀另一个进程正在跑的任务——那时需要先落「任务归属 / 租约」
（owner + lease_expires_at），再按租约过期判定。这里刻意不做模糊猜测。
"""
from __future__ import annotations

import logging

from app.infra.db import report_version_repository
from app.infra.db.postgres import get_pool
from app.services import report_version_service

logger = logging.getLogger(__name__)

# 只扫「后台任务正在跑」的三个相位；idle / awaiting_* / report_ready / error 都不动。
_BUSY_PHASES = ("parsing", "generating", "adjusting")

_SELECT_STUCK = """
    SELECT thread_id, user_id, current_phase, latest_requirement_draft_id,
           latest_report_version, title
    FROM agent.session
    WHERE current_phase = ANY($1::text[])
"""


async def reconcile_orphan_sessions(*, dry_run: bool = False) -> dict[str, int]:
    """把上个进程留下的「忙相位」会话收敛成可重试状态，返回各分支计数。

    `dry_run=True` 只统计不落库——CLI / 排查时用。
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SELECT_STUCK, list(_BUSY_PHASES))

    stats = {"scanned": 0, "fixed_completed": 0, "marked_interrupted": 0, "skipped": 0}
    for row in rows:
        stats["scanned"] += 1
        session_id = row["thread_id"]
        draft_id = row["latest_requirement_draft_id"]
        try:
            done = None
            if draft_id is not None:
                async with pool.acquire() as conn:
                    done = await report_version_repository.find_done_by_draft(
                        conn,
                        session_id=session_id,
                        user_id=row["user_id"],
                        requirement_draft_id=draft_id,
                    )
        except Exception as exc:  # noqa: BLE001 - 单条失败不拖垮整批
            logger.warning("orphan recovery: lookup failed for %s: %s", session_id, exc)
            stats["skipped"] += 1
            continue

        if done is not None:
            # 版本行已经落好了 → 只是 phase 没更新，别再加一条失败记录
            if not dry_run:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE agent.session SET current_phase = 'report_ready', "
                        "last_failed_action = NULL, latest_report_version = $2 "
                        "WHERE thread_id = $1",
                        session_id, done.get("version"),
                    )
            stats["fixed_completed"] += 1
            logger.info(
                "orphan recovery: %s actually completed as v%s, phase repaired",
                session_id, done.get("version"),
            )
            continue

        # 真的没跑成 → 落一条 error 版本，并把会话置成可重试
        if not dry_run:
            try:
                await report_version_service.persist_error_run(
                    session_id=session_id,
                    user_id=row["user_id"],
                    requirement_draft_id=draft_id,
                    title=(row["title"] or "任务中断"),
                    error_detail={
                        "code": "QUERY_FAILED",
                        "kind": "interrupted",
                        "message": "任务在执行中被中断（服务重启），可以直接重试",
                    },
                    query_snapshot=None,
                    trace_id=None,
                    failed_action="confirm",
                )
            except Exception as exc:  # noqa: BLE001 - 落库失败只告警，不阻塞启动
                logger.warning("orphan recovery: persist failed for %s: %s", session_id, exc)
                stats["skipped"] += 1
                continue
        stats["marked_interrupted"] += 1
        logger.warning(
            "orphan recovery: %s was stuck in %s, marked interrupted + retryable",
            session_id, row["current_phase"],
        )

    if stats["scanned"]:
        logger.info("orphan recovery done: %s (dry_run=%s)", stats, dry_run)
    return stats
