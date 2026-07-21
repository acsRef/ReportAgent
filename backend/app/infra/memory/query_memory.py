from __future__ import annotations

import datetime
import json
import math
from typing import Optional

from app.infra.db.postgres import get_pool
from app.embedding.service import get_embedder


class QueryMemory:
    async def save_query(
        self,
        question: str,
        sql: str,
        schema: Optional[dict] = None,
        target_metric: str = "",
    ) -> int:
        pool = get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, success_count FROM memory.query_template WHERE sql_text=$1",
                sql,
            )
            if existing:
                await conn.execute(
                    """UPDATE memory.query_template
                       SET success_count=success_count+1, last_used_at=NOW() WHERE id=$1""",
                    existing["id"],
                )
                return existing["id"]

            embedder = get_embedder()
            embedding = await embedder.embed_or_none(question)

            row = await conn.fetchrow(
                """INSERT INTO memory.query_template
                   (intent_embedding, question, sql_text, schema_context, target_metric,
                    success_count, failure_count, access_count, verified)
                   VALUES ($1, $2, $3, $4, $5, 1, 0, 1, FALSE)
                   RETURNING id""",
                embedding,
                question,
                sql,
                json.dumps(schema, ensure_ascii=False, default=str) if schema else None,
                target_metric,
            )
            return row["id"]

    async def search_similar(self, question: str, top_k: int = 3) -> list[dict]:
        pool = get_pool()
        embedder = get_embedder()
        embedding = await embedder.embed_or_none(question)
        now = datetime.datetime.now()

        async with pool.acquire() as conn:
            if embedding is not None:
                rows = await conn.fetch(
                    """SELECT id, question, sql_text, target_metric,
                              success_count, failure_count, access_count, last_used_at,
                              GREATEST(0, 1 - (intent_embedding <=> $1::vector)) AS sem_sim
                       FROM memory.query_template
                       WHERE intent_embedding IS NOT NULL
                       ORDER BY sem_sim DESC
                       LIMIT $2""",
                    embedding, top_k * 3,
                )
            else:
                keywords = [w for w in question.lower().replace(",", " ").split() if len(w) > 1]
                if not keywords:
                    return []
                patterns = [f"%{kw}%" for kw in keywords]
                rows = await conn.fetch(
                    """SELECT id, question, sql_text, target_metric,
                              success_count, failure_count, access_count, last_used_at,
                              0.0 AS sem_sim
                       FROM memory.query_template
                       WHERE question ILIKE ANY($1::text[]) OR sql_text ILIKE ANY($1::text[])
                       ORDER BY success_count DESC, last_used_at DESC
                       LIMIT $2""",
                    patterns, top_k * 3,
                )

        ranked = []
        for r in rows:
            success_rate = r["success_count"] / max(r["success_count"] + (r.get("failure_count") or 0), 1)
            freq = min(math.log1p(r.get("access_count") or 1) / 10.0, 1.0)
            recency = self._recency_score(r.get("last_used_at"), now)
            score = (r.get("sem_sim", 0.0) or 0.0) * 0.5 + success_rate * 0.3 + freq * 0.1 + recency * 0.1

            ranked.append({
                "id": r["id"],
                "question": r["question"],
                "sql": r["sql_text"],
                "target_metric": r["target_metric"],
                "success_count": r["success_count"],
                "failure_count": r.get("failure_count") or 0,
                "access_count": r.get("access_count") or 0,
                "score": round(score, 4),
            })

            # record access async (fire & forget within same method for simplicity)
            await self.record_access(r["id"])

        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked[:top_k]

    async def record_access(self, entry_id: int) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE memory.query_template SET access_count=access_count+1, "
                "last_used_at=NOW() WHERE id=$1",
                entry_id,
            )

    async def record_failure(self, entry_id: int) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE memory.query_template SET failure_count=failure_count+1 WHERE id=$1",
                entry_id,
            )

    # --- Deprecated: delegate to UserMemory via MemoryManager ---

    async def save_semantic(
        self,
        user_id: str,
        content: str,
        source: str = "",
        entry_type: str = "semantic",
    ) -> int:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO memory.semantic_entry
                   (user_id, content, memory_type, source, access_count, last_access_time)
                   VALUES ($1, $2, $3, $4, 1, NOW())
                   RETURNING id""",
                user_id, content, entry_type, source,
            )
            return row["id"]

    async def search_semantic(
        self, user_id: str, query: str = "", top_k: int = 5
    ) -> list[str]:
        pool = get_pool()
        async with pool.acquire() as conn:
            if query:
                keywords = [w for w in query.lower().split() if len(w) > 1]
                if keywords:
                    patterns = [f"%{kw}%" for kw in keywords]
                    rows = await conn.fetch(
                        """SELECT content FROM memory.semantic_entry
                            WHERE user_id=$1 AND content ILIKE ANY($2::text[])
                            ORDER BY last_access_time DESC LIMIT $3""",
                        user_id, patterns, top_k,
                    )
                else:
                    rows = await conn.fetch(
                        """SELECT content FROM memory.semantic_entry
                           WHERE user_id=$1 ORDER BY last_access_time DESC LIMIT $2""",
                        user_id, top_k,
                    )
            else:
                rows = await conn.fetch(
                    """SELECT content FROM memory.semantic_entry
                       WHERE user_id=$1 ORDER BY last_access_time DESC LIMIT $2""",
                    user_id, top_k,
                )
            return [r["content"] for r in rows]

    @staticmethod
    def _recency_score(last_used_at, now):
        if last_used_at is None:
            return 0.0
        hours = (now - last_used_at).total_seconds() / 3600
        return 2.0 ** (-hours / 72)
