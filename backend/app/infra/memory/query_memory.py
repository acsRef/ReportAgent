from __future__ import annotations

import json
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
                    "UPDATE memory.query_template SET success_count=success_count+1, last_used_at=NOW() WHERE id=$1",
                    existing["id"],
                )
                return existing["id"]

            embedder = get_embedder()
            embedding = await embedder.embed_or_none(question)

            row = await conn.fetchrow(
                """INSERT INTO memory.query_template
                   (intent_embedding, question, sql_text, schema_context, target_metric)
                   VALUES ($1, $2, $3, $4, $5)
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

        async with pool.acquire() as conn:
            if embedding is not None:
                rows = await conn.fetch(
                    """SELECT id, question, sql_text, target_metric, success_count
                       FROM memory.query_template
                       WHERE intent_embedding IS NOT NULL
                       ORDER BY intent_embedding <=> $1
                       LIMIT $2""",
                    embedding,
                    top_k,
                )
                if rows:
                    return [
                        {
                            "id": r["id"],
                            "question": r["question"],
                            "sql": r["sql_text"],
                            "target_metric": r["target_metric"],
                            "success_count": r["success_count"],
                        }
                        for r in rows
                    ]

            keywords = [
                w for w in question.lower().replace(",", " ").split() if len(w) > 1
            ]
            if not keywords:
                return []

            like_clauses = " OR ".join(
                f"question LIKE '%{k}%'" for k in keywords
            )
            sql_clauses = " OR ".join(
                f"sql_text LIKE '%{k}%'" for k in keywords
            )

            rows = await conn.fetch(
                f"""SELECT id, question, sql_text, target_metric, success_count
                    FROM memory.query_template
                    WHERE ({like_clauses}) OR ({sql_clauses})
                    ORDER BY success_count DESC, last_used_at DESC
                    LIMIT $1""",
                top_k,
            )

            return [
                {
                    "id": r["id"],
                    "question": r["question"],
                    "sql": r["sql_text"],
                    "target_metric": r["target_metric"],
                    "success_count": r["success_count"],
                }
                for r in rows
            ]

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
                   (user_id, content, entry_type, source)
                   VALUES ($1, $2, $3, $4)
                   RETURNING id""",
                user_id,
                content,
                entry_type,
                source,
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
                    like_clauses = " OR ".join(
                        f"content LIKE '%{k}%'" for k in keywords
                    )
                    rows = await conn.fetch(
                        f"""SELECT content FROM memory.semantic_entry
                            WHERE user_id=$1 AND ({like_clauses})
                            ORDER BY created_at DESC LIMIT $2""",
                        user_id,
                        top_k,
                    )
                else:
                    rows = await conn.fetch(
                        """SELECT content FROM memory.semantic_entry
                           WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2""",
                        user_id,
                        top_k,
                    )
            else:
                rows = await conn.fetch(
                    """SELECT content FROM memory.semantic_entry
                       WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2""",
                    user_id,
                    top_k,
                )
            return [r["content"] for r in rows]
