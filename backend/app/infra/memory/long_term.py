"""长期记忆库（`memory.long_term_memory`）——与 `semantic_entry` 的工作区分开。

plan `2026-11-09-long-term-memory-table`：

- `semantic_entry` = **工作区**：候选队列（`status='candidate'` 等固化）+ 会话级临时口径
  （`scope='session'` + 24h）。会不断增删，属于“过程数据”。
- `long_term_memory` = **长期库**：`UNIQUE (user_id, memory_key)`。
  `memory_key` 是意图槽位（`pref:chart` / `pref:granularity` / `fact:<指纹>`），
  所以「同一维度两条矛盾偏好同时 active」在结构上就不可能发生——
  新值直接 UPSERT 覆盖旧值，旧正文留在 `previous_content`、`revision` 递增。

为什么不用 supersede 链：链式历史需要额外扫表 + 状态机，而且“谁生效”要每次过滤；
唯一键 + UPSERT 把“谁生效”变成“查询即唯一行”，读侧零成本。
"""
from __future__ import annotations

import datetime
from typing import Optional

from app.infra.db.postgres import get_pool
from app.embedding.service import get_embedder


class LongTermMemory:
    """长期记忆的读写（infra 层；domain 只经 `MemoryManager` 网关使用）。"""

    async def upsert(
        self,
        *,
        user_id: int | str,
        memory_key: str,
        content: str,
        memory_type: str = "stable_preference",
        importance: float = 0.8,
        source: str = "user_turn",
        confidence: str = "high",
        origin_id: int | None = None,
        evidence_count: int | None = None,
    ) -> dict:
        """写入/覆盖一个槽位。返回 `{id, revision, replaced, memory_key}`。

        - 同槽位、正文相同 → 只累加 `evidence_count`（不涨 revision，不算“改口径”）；
        - 同槽位、正文不同 → 覆盖正文、`revision+1`、旧正文进 `previous_content`；
        - `origin_id` 记来源（候选行 id 等），便于回溯。
        """
        embedder = get_embedder()
        embedding = await embedder.embed_or_none(content)
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO memory.long_term_memory
                   (user_id, memory_key, content, memory_type, importance_score,
                    intent_embedding, source, confidence, evidence_count, origin_id,
                    access_count, last_access_time)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, 0, NULL)
                   ON CONFLICT (user_id, memory_key) DO UPDATE SET
                     previous_content = CASE
                         WHEN memory.long_term_memory.content IS DISTINCT FROM EXCLUDED.content
                         THEN memory.long_term_memory.content
                         ELSE memory.long_term_memory.previous_content END,
                     revision = memory.long_term_memory.revision
                         + CASE WHEN memory.long_term_memory.content IS DISTINCT FROM EXCLUDED.content
                                THEN 1 ELSE 0 END,
                     content = EXCLUDED.content,
                     memory_type = EXCLUDED.memory_type,
                     confidence = EXCLUDED.confidence,
                     importance_score = GREATEST(memory.long_term_memory.importance_score,
                                                 EXCLUDED.importance_score),
                     intent_embedding = COALESCE(EXCLUDED.intent_embedding,
                                                 memory.long_term_memory.intent_embedding),
                     evidence_count = GREATEST(memory.long_term_memory.evidence_count, 1)
                         + COALESCE($9, 1),
                     updated_at = NOW()
                   RETURNING id, revision, content, previous_content, evidence_count""",
                int(user_id), memory_key, content, memory_type, importance,
                embedding, source, confidence,
                evidence_count if evidence_count is not None else 1,
                origin_id,
            )
        return {
            "id": row["id"],
            "revision": row["revision"],
            "content": row["content"],
            "previous_content": row["previous_content"],
            "evidence_count": row["evidence_count"],
            "memory_key": memory_key,
            "replaced": row["revision"] > 1,
        }

    async def search(
        self,
        user_id: int | str,
        query: str = "",
        top_k: int = 5,
    ) -> list[dict]:
        """语义召回（与 `semantic_entry` 同构的返回形态，便于两库合并）。"""
        if top_k <= 0:
            return []
        user_id = int(user_id)
        embedder = get_embedder()
        embedding = await embedder.embed_or_none(query) if query else None
        pool = get_pool()
        async with pool.acquire() as conn:
            if embedding is not None:
                rows = await conn.fetch(
                    """SELECT id, content, memory_type, importance_score,
                              access_count, last_access_time,
                              'active'::text AS status, 'user'::text AS scope,
                              confidence, memory_key,
                              GREATEST(0, 1 - (intent_embedding <=> $1::vector)) AS sem_sim
                       FROM memory.long_term_memory
                       WHERE user_id=$2 AND intent_embedding IS NOT NULL
                       ORDER BY sem_sim DESC LIMIT $3""",
                    embedding, user_id, top_k * 2,
                )
            else:
                keywords = [w for w in query.lower().split() if len(w) > 1] if query else []
                if keywords:
                    rows = await conn.fetch(
                        """SELECT id, content, memory_type, importance_score,
                                  access_count, last_access_time,
                                  'active'::text AS status, 'user'::text AS scope,
                                  confidence, memory_key, 0.0::float AS sem_sim
                           FROM memory.long_term_memory
                           WHERE user_id=$1 AND content ILIKE ANY($2::text[])
                           ORDER BY importance_score DESC LIMIT $3""",
                        user_id, [f"%{kw}%" for kw in keywords], top_k * 2,
                    )
                else:
                    rows = await conn.fetch(
                        """SELECT id, content, memory_type, importance_score,
                                  access_count, last_access_time,
                                  'active'::text AS status, 'user'::text AS scope,
                                  confidence, memory_key, 0.0::float AS sem_sim
                           FROM memory.long_term_memory
                           WHERE user_id=$1
                           ORDER BY importance_score DESC LIMIT $2""",
                        user_id, top_k * 2,
                    )
        return [dict(r) for r in rows]

    async def record_access(self, entry_id: int) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE memory.long_term_memory SET access_count=access_count+1, "
                "last_access_time=NOW() WHERE id=$1",
                entry_id,
            )

    async def list_all(self, user_id: int | str) -> list[dict]:
        """该用户的全部长期记忆（青春版“记忆管理”视图/排障用）。"""
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, memory_key, content, memory_type, confidence, revision, "
                "previous_content, evidence_count, access_count, source, updated_at "
                "FROM memory.long_term_memory WHERE user_id=$1 ORDER BY memory_key",
                int(user_id),
            )
        return [dict(r) for r in rows]

    async def delete_key(self, user_id: int | str, memory_key: str) -> int:
        pool = get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute(
                "DELETE FROM memory.long_term_memory WHERE user_id=$1 AND memory_key=$2",
                int(user_id), memory_key,
            )
        return int(str(res).split()[-1]) if res else 0

    async def expire_stale(self, user_id: int | str, *, days: int = 180) -> int:
        """长期库的“防腐”：太久没被召回也没被更新的条目清掉（默认 180 天）。

        长期不等于永远：一条偏好两年没被用过、也没被覆盖，留着只会稀释召回。
        这个动作只在固化批处理里跑，不在热路径。
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute(
                "DELETE FROM memory.long_term_memory WHERE user_id=$1 "
                "AND COALESCE(last_access_time, updated_at) < NOW() - ($2 || ' days')::interval",
                int(user_id), str(int(days)),
            )
        return int(str(res).split()[-1]) if res else 0
