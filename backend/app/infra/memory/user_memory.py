from __future__ import annotations

import datetime
import logging
import math
from typing import Optional

from app.infra.db.postgres import get_pool
from app.embedding.service import get_embedder

logger = logging.getLogger(__name__)

# 每用户语义记忆条数上限。超出后按 LFU/LRU+重要性混合分淘汰最冷的，防止
# memory.semantic_entry 无限增长。淘汰是排序因子之外的「硬上限」补充——召回仍语义主导。
USER_MEMORY_CAP = 200


class RankedMemory:
    def __init__(self, id: int, content: str, memory_type: str,
                 importance_score: float, access_count: int,
                 last_access_time: Optional[datetime.datetime],
                 score: float):
        self.id = id
        self.content = content
        self.memory_type = memory_type
        self.importance_score = importance_score
        self.access_count = access_count
        self.last_access_time = last_access_time
        self.score = score


class UserMemory:
    def __init__(self, top_k: int = 5, capacity: int = USER_MEMORY_CAP):
        self._top_k = top_k
        self._capacity = capacity

    async def save(
        self,
        user_id: str,
        content: str,
        memory_type: str = "insight",
        importance_score: float = 0.3,
        source: str = "",
    ) -> int:
        pool = get_pool()
        is_new = False
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, access_count FROM memory.semantic_entry "
                "WHERE user_id=$1 AND content=$2",
                user_id, content,
            )
            if existing:
                await conn.execute(
                    "UPDATE memory.semantic_entry SET access_count=access_count+1, "
                    "last_access_time=NOW() WHERE id=$1",
                    existing["id"],
                )
                entry_id = existing["id"]
            else:
                is_new = True
                embedder = get_embedder()
                embedding = await embedder.embed_or_none(content)
                row = await conn.fetchrow(
                    """INSERT INTO memory.semantic_entry
                       (user_id, content, memory_type, importance_score, intent_embedding, source, access_count, last_access_time)
                       VALUES ($1, $2, $3, $4, $5, $6, 1, NOW())
                       RETURNING id""",
                    user_id, content, memory_type, importance_score, embedding, source,
                )
                entry_id = row["id"]

        # 只在新增时触发淘汰（去重更新不增加条数）。淘汰失败不拖垮写入主路径。
        if is_new:
            try:
                await self.evict_over_capacity(user_id)
            except Exception as exc:
                logger.warning("evict_over_capacity failed for user_id=%s: %s", user_id, exc)
        return entry_id

    async def evict_over_capacity(self, user_id: str) -> int:
        """容量上限淘汰：超过 capacity 时，按混合分升序删除最冷的若干条。

        淘汰分 = LFU(log1p(access_count)/10, 截顶)×0.4 + LRU(2^(-h/72))×0.4
                 + 重要性×0.2。**不含语义**（淘汰时无查询）；重要性项保护稳定
        偏好不被误删。返回实际删除条数。
        """
        if self._capacity <= 0:
            return 0
        pool = get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM memory.semantic_entry WHERE user_id=$1", user_id,
            )
            overflow = (count or 0) - self._capacity
            if overflow <= 0:
                return 0
            deleted = await conn.execute(
                """WITH ranked AS (
                     SELECT id, (
                       LEAST(LN(1+access_count)/10.0, 1.0) * 0.4
                       + (CASE WHEN last_access_time IS NULL THEN 0.0
                               ELSE POWER(2.0, -(EXTRACT(EPOCH FROM (NOW()-last_access_time))/3600.0)/72.0)
                          END) * 0.4
                       + LEAST(COALESCE(importance_score,0.0), 1.0) * 0.2
                     ) AS evict_score
                     FROM memory.semantic_entry WHERE user_id=$1
                   )
                   DELETE FROM memory.semantic_entry WHERE id IN (
                     SELECT id FROM ranked ORDER BY evict_score ASC LIMIT $2
                   )""",
                user_id, overflow,
            )
            return int(str(deleted).split()[-1]) if deleted else 0

    async def search(
        self, user_id: str, query: str = "", top_k: Optional[int] = None
    ) -> list[RankedMemory]:
        k = top_k or self._top_k
        pool = get_pool()
        embedder = get_embedder()
        embedding = await embedder.embed_or_none(query) if query else None
        now = datetime.datetime.now()

        async with pool.acquire() as conn:
            if embedding is not None:
                rows = await conn.fetch(
                    """SELECT id, content, memory_type, importance_score,
                              access_count, last_access_time,
                              GREATEST(0, 1 - (intent_embedding <=> $1::vector)) AS sem_sim
                       FROM memory.semantic_entry
                       WHERE user_id=$2 AND intent_embedding IS NOT NULL
                       ORDER BY sem_sim DESC
                       LIMIT $3""",
                    embedding, user_id, k * 3,
                )
                results = [
                    self._row_to_memory(r, self._compute_score(
                        semantic_similarity=r["sem_sim"],
                        importance_score=r["importance_score"] or 0.0,
                        access_count=r["access_count"] or 0,
                        last_access_time=r.get("last_access_time"),
                        now=now,
                    ))
                    for r in rows
                ]
            else:
                keywords = [w for w in query.lower().replace(",", " ").split() if len(w) > 1] if query else []
                if not keywords:
                    rows = await conn.fetch(
                        """SELECT id, content, memory_type, importance_score,
                                  access_count, last_access_time
                           FROM memory.semantic_entry WHERE user_id=$1
                           ORDER BY last_access_time DESC LIMIT $2""",
                        user_id, k * 3,
                    )
                else:
                    patterns = [f"%{kw}%" for kw in keywords]
                    rows = await conn.fetch(
                        """SELECT id, content, memory_type, importance_score,
                                  access_count, last_access_time
                           FROM memory.semantic_entry
                           WHERE user_id=$1 AND content ILIKE ANY($2::text[])
                           ORDER BY last_access_time DESC LIMIT $3""",
                        user_id, patterns, k * 3,
                    )
                results = [
                    self._row_to_memory(r, self._compute_score(
                        semantic_similarity=0.0,
                        importance_score=r["importance_score"] or 0.0,
                        access_count=r["access_count"] or 0,
                        last_access_time=r.get("last_access_time"),
                        now=now,
                    ))
                    for r in rows
                ]

        results.sort(key=lambda x: x.score, reverse=True)
        top = results[:k]

        for m in top:
            await self.record_access(m.id)

        return top

    async def record_access(self, entry_id: int) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE memory.semantic_entry SET access_count=access_count+1, "
                "last_access_time=NOW() WHERE id=$1",
                entry_id,
            )

    async def get_user_preferences(
        self, user_id: str, top_k: Optional[int] = None
    ) -> list[RankedMemory]:
        k = top_k or self._top_k
        pool = get_pool()
        now = datetime.datetime.now()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, content, memory_type, importance_score,
                          access_count, last_access_time
                   FROM memory.semantic_entry
                   WHERE user_id=$1 AND memory_type IN ('stable_preference', 'temporary_preference')
                   ORDER BY last_access_time DESC LIMIT $2""",
                user_id, k * 3,
            )
            results = [
                self._row_to_memory(r, self._compute_score(
                    semantic_similarity=0.0,
                    importance_score=r["importance_score"] or 0.0,
                    access_count=r["access_count"] or 0,
                    last_access_time=r.get("last_access_time"),
                    now=now,
                ))
                for r in rows
            ]

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:k]

    def _row_to_memory(self, row, score: float) -> RankedMemory:
        return RankedMemory(
            id=row["id"],
            content=row["content"],
            memory_type=row["memory_type"] or "insight",
            importance_score=row["importance_score"] or 0.0,
            access_count=row["access_count"] or 0,
            last_access_time=row.get("last_access_time"),
            score=score,
        )

    def _compute_score(
        self,
        semantic_similarity: float,
        importance_score: float,
        access_count: int,
        last_access_time: Optional[datetime.datetime],
        now: Optional[datetime.datetime] = None,
    ) -> float:
        freq = min(math.log1p(access_count) / 10.0, 1.0)
        recency = self._recency_score(last_access_time, now)
        return (
            max(semantic_similarity, 0.0) * 0.6
            + min(importance_score, 1.0) * 0.2
            + freq * 0.1
            + recency * 0.1
        )

    def _recency_score(
        self,
        last_access_time: Optional[datetime.datetime],
        now: Optional[datetime.datetime] = None,
    ) -> float:
        if last_access_time is None:
            return 0.0
        ref = now or datetime.datetime.now()
        hours = (ref - last_access_time).total_seconds() / 3600
        return 2.0 ** (-hours / 72)
