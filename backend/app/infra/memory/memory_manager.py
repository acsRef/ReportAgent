from __future__ import annotations

from app.infra.memory.query_memory import QueryMemory
from app.infra.memory.long_term import LongTermMemory
from app.infra.memory.user_memory import UserMemory

# memory_type → RecallItem.kind 映射（业务定义/事实 vs 偏好）
_PREFERENCE_TYPES = frozenset({"stable_preference", "temporary_preference"})


class MemoryManager:
    def __init__(self):
        self._query_memory = QueryMemory()
        self._user_memory = UserMemory()
        self._long_term = LongTermMemory()

    # --- 长期记忆库（plan 2026-11-09-long-term-memory-table）--------------------

    async def remember_long_term(self, **kwargs) -> dict:
        """写/覆盖一个意图槽位（`memory_key` 唯一）。"""
        return await self._long_term.upsert(**kwargs)

    async def list_long_term(self, user_id) -> list[dict]:
        return await self._long_term.list_all(user_id)

    async def expire_long_term(self, user_id, *, days: int = 180) -> int:
        return await self._long_term.expire_stale(user_id, days=days)

    async def recall_structured(
        self,
        query: str,
        user_id: str,
        *,
        top_k_queries: int = 2,
        top_k_preferences: int = 3,
        session_id: str | None = None,
    ) -> list[dict]:
        """P4b：structured 召回（memory-architecture §六）。

        返回 list[dict]，字段与 `app.context.assembler.RecallItem` 结构同构
        （raw_text/source/kind/score/ref_id）。**不** import context 类型——
        persistence 层不反向依赖 domain；调用方（ContextRuntime）按 RecallItem
        消费（TypedDict 运行时即 dict）。

        QueryMemory + UserMemory 底层本就返回结构化行，这里映射为结构化条目，
        **不**拍平。candidate/expired 已在 UserMemory.search SQL 层排除（T3）。
        """
        items: list[dict] = []

        queries = await self._query_memory.search_similar(
            query, top_k=top_k_queries, user_id=int(user_id),
        )
        for q in queries:
            items.append({
                "raw_text": (
                    f"[历史查询] {q['question']} → {q['sql'][:80]} "
                    f"(匹配度{q.get('score', 0):.2f})"
                ),
                "source": "memory_query", "kind": "query",
                "score": float(q.get("score", 0.0)), "ref_id": q.get("id"),
            })

        prefs = await self._user_memory.search(
            user_id, query, top_k=top_k_preferences, session_id=session_id,
        )
        for p in prefs:
            kind = "preference" if p.memory_type in _PREFERENCE_TYPES else "semantic"
            source = "memory_preference" if kind == "preference" else "memory_semantic"
            items.append({
                "raw_text": f"[{p.memory_type}] {p.content[:120]} (相关度{p.score:.2f})",
                "source": source, "kind": kind,
                "score": float(p.score), "ref_id": p.id,
            })

        return items

    async def recall(
        self,
        query: str,
        user_id: str,
        top_k_queries: int = 2,
        top_k_preferences: int = 3,
    ) -> str:
        """legacy string API（P4b 起委托 recall_structured，单点格式化，无逻辑双写）。

        保留原因：legacy parent_graph 仍用 `mm.recall()->str`（CLAUDE.md §13 不动）。
        """
        items = await self.recall_structured(
            query, user_id,
            top_k_queries=top_k_queries, top_k_preferences=top_k_preferences,
        )
        return "\n".join(i["raw_text"] for i in items) if items else ""

    async def remember_query(
        self,
        question: str,
        sql: str,
        schema: dict | None = None,
        target_metric: str = "",
        *,
        user_id: int,
    ) -> int:
        # A-4：user_id 必传——query_template 按用户隔离，落库即带归属。
        return await self._query_memory.save_query(
            question, sql, schema, target_metric, user_id=user_id,
        )

    async def remember_query_failure(
        self,
        *,
        user_id: int,
        question: str,
        failed_sql: str | None,
        failure_category: str = "other",
        error_message: str = "",
        retry_count: int = 0,
        target_metric: str = "",
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> int:
        """§四：失败明细落 `memory.query_failure`（宪法 §6：memory 写一律经本网关）。"""
        return await self._query_memory.record_failure_detail(
            user_id=user_id,
            question=question,
            failed_sql=failed_sql,
            failure_category=failure_category,
            error_message=error_message,
            retry_count=retry_count,
            target_metric=target_metric,
            session_id=session_id,
            trace_id=trace_id,
        )

    async def list_active_preference_contents(self, user_id: str) -> list[str]:
        """当前 active 偏好正文（domain 层做「同维度冲突」判定用）。"""
        return await self._user_memory.list_active_preference_contents(user_id)

    async def supersede_active_by_contents(
        self, user_id: str, contents: list[str]
    ) -> int:
        """把指定旧偏好置 superseded（§六；判定在 domain，这里只执行）。"""
        return await self._user_memory.supersede_active_by_contents(user_id, contents)

    # --- 离线固化（plan 2026-11-09-memory-admission-and-consolidation）---------
    # domain 层（app/memory/consolidation.py）只通过这些窄接口操作，不碰下面的 UserMemory。

    async def list_candidates(self, user_id, limit: int = 200) -> list[dict]:
        return await self._user_memory.list_candidates(user_id, limit=limit)

    async def promote_candidate(self, user_id, entry_id: int, **kwargs) -> None:
        await self._user_memory.promote_candidate(user_id, entry_id, **kwargs)

    async def drop_candidate(self, user_id, entry_id: int) -> None:
        await self._user_memory.drop_candidate(user_id, entry_id)

    async def expire_due_preferences(self, user_id, *, now=None) -> int:
        return await self._user_memory.expire_due(user_id, now=now)

    async def list_active_preference_rows(self, user_id) -> list[dict]:
        return await self._user_memory.list_active_preference_rows(user_id)

    async def supersede_ids(self, user_id, entry_ids: list[int]) -> int:
        return await self._user_memory.supersede_ids(user_id, entry_ids)

    async def get_last_consolidation(self, user_id):
        return await self._user_memory.get_last_consolidation(user_id)

    async def set_last_consolidation(self, user_id, when) -> None:
        await self._user_memory.set_last_consolidation(user_id, when)

    async def record_consolidation_run(self, **kwargs) -> int:
        return await self._user_memory.record_consolidation_run(**kwargs)

    async def remember_preference(
        self,
        user_id: str,
        content: str,
        memory_type: str = "insight",
        importance: float = 0.3,
        source: str = "",
        *,
        scope: str = "user",
        status: str = "active",
        confidence: str = "medium",
        session_id: str | None = None,
        expires_at=None,
    ) -> int:
        return await self._user_memory.save(
            user_id=user_id,
            content=content,
            memory_type=memory_type,
            importance_score=importance,
            source=source,
            scope=scope, status=status, confidence=confidence,
            session_id=session_id, expires_at=expires_at,
        )

    async def record_query_failure(self, query_id: int) -> None:
        await self._query_memory.record_failure(query_id)

    async def supersede_stable_preference(self, user_id: str, content: str) -> int:
        """§六：新 active stable 写入前，把同 user 的其他 active stable_preference
        置 superseded（新旧偏好冲突时旧 → superseded，避免多 active 互矛盾）。

        V1 按 content 精确匹配（同一条偏好被显式重申时不产生双 active）；
        语义相似度 supersede 属后期（P4b 不做）。返回受影响行数。
        """
        return await self._user_memory.supersede_stable_preferences(user_id, content)