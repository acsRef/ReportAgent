from __future__ import annotations

from app.infra.memory.query_memory import QueryMemory
from app.infra.memory.user_memory import UserMemory

# memory_type → RecallItem.kind 映射（业务定义/事实 vs 偏好）
_PREFERENCE_TYPES = frozenset({"stable_preference", "temporary_preference"})


class MemoryManager:
    def __init__(self):
        self._query_memory = QueryMemory()
        self._user_memory = UserMemory()

    async def recall_structured(
        self,
        query: str,
        user_id: str,
        *,
        top_k_queries: int = 2,
        top_k_preferences: int = 3,
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

        prefs = await self._user_memory.search(user_id, query, top_k=top_k_preferences)
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
