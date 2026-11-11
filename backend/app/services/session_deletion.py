"""会话硬删除：按外键顺序级联清理一个 session 的全部痕迹。

为什么单独一个模块而不是塞进 `SessionManager`：`SessionManager` 是 `agent.session`
的仓储，而一次删除横跨 4 个 schema（`agent` / `app` / `memory` / `observability`）
外加 LangGraph 的 checkpoint 表——把这些表的知识放进 session 仓储等于让它越界。
这里把「删哪些表、什么顺序」收敛成一份清单，端点和 CLI 共用，不允许第二份实现。

顺序不是随便排的，两条依赖来自 `information_schema` 实查：

1. `agent.report_version.requirement_draft_id → agent.requirement_draft.id`
   （`report_version_requirement_draft_id_fkey`）——report_version 必须先删；
2. `agent.session.latest_requirement_draft_id → agent.requirement_draft.id`
   （`fk_session_latest_requirement`）——删 draft 前要把 session 的这个指针置空。

还有一条不看 schema 就会漏的：**LangGraph 的 checkpoint**（`public.checkpoints` /
`checkpoint_blobs` / `checkpoint_writes`，键是 `thread_id`）。dev 环境用 MemorySaver
所以是空表，非 dev 用 `AsyncPostgresSaver`——不删的话，用户删掉会话后用同一个
thread_id 重建（前端确实会复用 id 恢复），会读到上一轮遗留的图状态。
"""
from __future__ import annotations

import logging

from app.infra.db.postgres import get_pool

logger = logging.getLogger(__name__)


class SessionNotFound(Exception):
    """会话不存在或不属于该用户。HTTP 层映射 404 SESSION_NOT_FOUND。"""


# ── 级联清单：(标签, SQL) ────────────────────────────────────────────────────
# 所有语句都以 `$1 = session_id`（thread_id）为参数；顺序即执行顺序，不要重排。
# 第 0 步是 UPDATE：清掉指向 draft 的指针，它的作用见模块 docstring 第 2 条。
_CASCADE_STEPS: tuple[tuple[str, str], ...] = (
    (
        "session.unlink_draft",
        "UPDATE agent.session SET latest_requirement_draft_id = NULL, "
        "latest_report_version = NULL WHERE thread_id = $1",
    ),
    (
        "observability.agent_trace_span",
        "DELETE FROM observability.agent_trace_span WHERE trace_id IN "
        "(SELECT trace_id FROM observability.agent_trace WHERE session_id = $1)",
    ),
    (
        "observability.agent_trace",
        "DELETE FROM observability.agent_trace WHERE session_id = $1",
    ),
    # LangGraph checkpoint 三件套：dev 下是空表，非 dev 才是真身
    ("public.checkpoint_writes", "DELETE FROM public.checkpoint_writes WHERE thread_id = $1"),
    ("public.checkpoint_blobs", "DELETE FROM public.checkpoint_blobs WHERE thread_id = $1"),
    ("public.checkpoints", "DELETE FROM public.checkpoints WHERE thread_id = $1"),
    (
        "agent.analysis_task_revision",
        "DELETE FROM agent.analysis_task_revision WHERE session_id = $1",
    ),
    (
        "agent.report_version",
        "DELETE FROM agent.report_version WHERE session_id = $1",
    ),
    (
        "agent.requirement_draft",
        "DELETE FROM agent.requirement_draft WHERE session_id = $1",
    ),
    ("memory.semantic_entry", "DELETE FROM memory.semantic_entry WHERE session_id = $1"),
    ("memory.query_failure", "DELETE FROM memory.query_failure WHERE session_id = $1"),
    ("app.conversations", "DELETE FROM app.conversations WHERE session_id = $1"),
    ("agent.session", "DELETE FROM agent.session WHERE thread_id = $1"),
)


async def delete_session(session_id: str, user_id: int) -> dict[str, int]:
    """删掉该用户的这条会话及其全部派生数据，返回每张表的删除行数。

    - 单事务：中途失败整体回滚，不会出现「对话没了但报告还在」；
    - owner 校验与删除在同一事务里（`SELECT ... FOR UPDATE`），避免校验后被并发改写；
    - 会话不存在或不属于该用户 → `SessionNotFound`（调用方翻 404，不区分两者，
      否则等于告诉攻击者「这个 id 存在但不是你的」）。
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            owner = await conn.fetchval(
                "SELECT user_id FROM agent.session WHERE thread_id = $1 FOR UPDATE",
                session_id,
            )
            if owner is None or owner != user_id:
                raise SessionNotFound(session_id)
            counts: dict[str, int] = {}
            for label, sql in _CASCADE_STEPS:
                status = await conn.execute(sql, session_id)
                # asyncpg 的 execute 返回命令标签，如 "DELETE 3"；取尾部行数
                counts[label] = int(status.rsplit(" ", 1)[-1]) if status else 0
    logger.info(
        "session deleted session_id=%s user_id=%s rows=%s",
        session_id, user_id, counts,
    )
    return counts


async def list_session_ids(prefix: str = "", user_id: int | None = None) -> list[str]:
    """按前缀 / 用户列 thread_id——CLI 批量清理用，不参与请求路径。"""
    sql = "SELECT thread_id FROM agent.session WHERE 1 = 1"
    args: list[object] = []
    if prefix:
        args.append(f"{prefix}%")
        sql += f" AND thread_id LIKE ${len(args)}"
    if user_id is not None:
        args.append(user_id)
        sql += f" AND user_id = ${len(args)}"
    sql += " ORDER BY updated_at DESC"
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [row["thread_id"] for row in rows]
