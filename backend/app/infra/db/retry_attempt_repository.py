"""重试尝试的持久记录（plan 2026-11-11-failure-recovery-durability P1）。

## 为什么不能只存在 state 里

`retry_counters` 原先只活在图的 State（dev 用 MemorySaver）里。进程一重启就归零，
于是**预算可以被无限绕过**——同一份需求反复失败，每次都是「第 1 次重试」。

这里的表同时承担两件事：

1. **审计**：每次重试一行（哪一轮、哪个预算、什么错误、为什么这么决策）；
2. **播种**：进 SQL 子图前按 `(session_id, requirement_draft_id)` 数一遍，
   把计数灌回 State，重启后预算继续有效。

写入是 best-effort（失败只告警）：审计丢一行不影响结论，但不能让它拖垮主链。
"""
from __future__ import annotations

import logging
from typing import Any

from app.infra.db.postgres import get_pool

logger = logging.getLogger(__name__)

# 预算键 → State 里的 retry_counters 键。DiagnosePolicy 消费的是后者。
_BUDGET_TO_COUNTER = {
    "sql_generation": "sql_generation",
    "plan": "plan",
    "mcp_schema": "mcp_schema",
}


async def record(
    *,
    session_id: str,
    user_id: int,
    requirement_draft_id: int | None,
    budget_key: str,
    action: str,
    error_kind: str = "",
    reason: str = "",
) -> None:
    """记一次重试。`attempt` 由本函数按已有序号 +1 计算（同 session+draft+预算维度）。

    空 session_id（legacy / 工具直调场景）直接 no-op——不写无主行。
    """
    if not session_id:
        return
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            attempt = await conn.fetchval(
                "SELECT COALESCE(MAX(attempt), 0) + 1 FROM agent.retry_attempt "
                "WHERE session_id = $1 AND COALESCE(requirement_draft_id, -1) = "
                "COALESCE($2, -1) AND budget_key = $3",
                session_id, requirement_draft_id, budget_key,
            )
            await conn.execute(
                """INSERT INTO agent.retry_attempt
                       (session_id, user_id, requirement_draft_id, attempt,
                        budget_key, action, error_kind, reason)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                session_id, user_id, requirement_draft_id, attempt,
                budget_key, action, error_kind or None, (reason or "")[:500] or None,
            )
    except Exception as exc:  # noqa: BLE001 - 审计失败不拖垮主链
        logger.warning(
            "retry_attempt record failed session=%s budget=%s: %s",
            session_id, budget_key, exc,
        )


async def counters_for(session_id: str, requirement_draft_id: int | None) -> dict[str, int]:
    """按 (session, draft) 数出各预算已用次数——进子图前播种用。"""
    if not session_id:
        return {}
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT budget_key, COUNT(*) AS n FROM agent.retry_attempt
                   WHERE session_id = $1 AND COALESCE(requirement_draft_id, -1) = COALESCE($2, -1)
                   GROUP BY budget_key""",
                session_id, requirement_draft_id,
            )
    except Exception as exc:  # noqa: BLE001 - 读不到就按「没重试过」起步，不阻塞执行
        logger.warning("retry_attempt counters failed session=%s: %s", session_id, exc)
        return {}
    counters: dict[str, int] = {}
    for row in rows:
        key = _BUDGET_TO_COUNTER.get(row["budget_key"])
        if key:
            counters[key] = int(row["n"])
    return counters


async def count_auto(session_id: str, requirement_draft_id: int | None) -> int:
    """该 draft 已经被自动重试过几次——P2 的预算判定。"""
    if not session_id:
        return 0
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                """SELECT COUNT(*) FROM agent.retry_attempt
                   WHERE session_id = $1 AND COALESCE(requirement_draft_id, -1) = COALESCE($2, -1)
                     AND budget_key = 'auto'""",
                session_id, requirement_draft_id,
            )
        return int(value or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("retry_attempt count_auto failed session=%s: %s", session_id, exc)
        return 0


async def list_attempts(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """排查用：这条会话重试过什么（按时间倒序）。"""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT attempt, budget_key, action, error_kind, reason,
                      requirement_draft_id, created_at
               FROM agent.retry_attempt WHERE session_id = $1
               ORDER BY created_at DESC LIMIT $2""",
            session_id, limit,
        )
    return [dict(row) for row in rows]
