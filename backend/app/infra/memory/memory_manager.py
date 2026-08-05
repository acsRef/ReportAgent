from __future__ import annotations

from app.infra.memory.query_memory import QueryMemory
from app.infra.memory.user_memory import UserMemory


class MemoryManager:
    def __init__(self):
        self._query_memory = QueryMemory()
        self._user_memory = UserMemory()

    async def recall(
        self,
        query: str,
        user_id: str,
        top_k_queries: int = 2,
        top_k_preferences: int = 3,
    ) -> str:
        lines = []

        # A-4：历史查询召回按 user_id 隔离（透传给 query_template 查询）。
        queries = await self._query_memory.search_similar(
            query, top_k=top_k_queries, user_id=int(user_id),
        )
        for q in queries:
            lines.append(
                f"[历史查询] {q['question']} → {q['sql'][:80]} "
                f"(匹配度{q.get('score', 0):.2f})"
            )

        prefs = await self._user_memory.search(user_id, query, top_k=top_k_preferences)
        for p in prefs:
            lines.append(
                f"[{p.memory_type}] {p.content[:120]} "
                f"(相关度{p.score:.2f})"
            )

        return "\n".join(lines) if lines else ""

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

    async def remember_preference(
        self,
        user_id: str,
        content: str,
        memory_type: str = "insight",
        importance: float = 0.3,
        source: str = "",
    ) -> int:
        return await self._user_memory.save(
            user_id=user_id,
            content=content,
            memory_type=memory_type,
            importance_score=importance,
            source=source,
        )

    async def record_query_failure(self, query_id: int) -> None:
        await self._query_memory.record_failure(query_id)
