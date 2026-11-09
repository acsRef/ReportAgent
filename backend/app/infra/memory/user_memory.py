from __future__ import annotations

import datetime
import logging
import math
from typing import Optional

from app.infra.db.postgres import get_pool
from app.infra.memory.scoring import lfu, lru, rank_key
from app.embedding.service import get_embedder

logger = logging.getLogger(__name__)

# 每用户语义记忆条数上限。超出后按 LFU/LRU+重要性混合分淘汰最冷的，防止
# memory.semantic_entry 无限增长。淘汰是排序因子之外的「硬上限」补充——召回仍语义主导。
USER_MEMORY_CAP = 200

# P4b §五/§六：召回只取 active 且未过期（candidate/superseded/expired 一律排除）。
# 字面片段（无 bind 参数）→ 不影响各查询 $n 占位编号。
_ACTIVE_RECALL_FILTER = "status = 'active' AND (expires_at IS NULL OR expires_at > NOW())"


def _hours_since(ts: Optional[datetime.datetime], now=None) -> float | None:
    """距今天数（小时）；None 表示「从未访问」→ 热度 0。"""
    if ts is None:
        return None
    ref = now or datetime.datetime.now()
    return max((ref - ts).total_seconds() / 3600.0, 0.0)


def _scope_filter(session_param: int) -> str:
    """§五 session 作用域：session 级记忆只在**原会话**可见。

    2026-11-09（plan memory-system-conformance D4）：此前召回只看 status/expires_at，
    session 级的 temporary_preference 会在**任何**会话被召回——A 会话的临时口径
    （「这次先按周看」）串到 B 会话是实打实的串味。

    没有会话上下文（`None`，如离线评测 / 模板中心）时**一律不召回** session 级：
    契约说它「绑定 session_id」，没有 session 就不该有它。
    """
    return (
        f"(scope <> 'session' OR (${session_param}::text IS NOT NULL "
        f"AND session_id = ${session_param}::text))"
    )


class RankedMemory:
    def __init__(self, id: int, content: str, memory_type: str,
                 importance_score: float, access_count: int,
                 last_access_time: Optional[datetime.datetime],
                 score: float,
                 status: str = "active", scope: str = "user",
                 confidence: str = "medium"):
        self.id = id
        self.content = content
        self.memory_type = memory_type
        self.importance_score = importance_score
        self.access_count = access_count
        self.last_access_time = last_access_time
        self.score = score
        # P4b lifecycle (memory-architecture §六)
        self.status = status
        self.scope = scope
        self.confidence = confidence


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
        *,
        scope: str = "user",
        status: str = "active",
        confidence: str = "medium",
        session_id: Optional[str] = None,
        expires_at: Optional[datetime.datetime] = None,
    ) -> int:
        # user_id 归一 int：列是 integer；生产经 semantic_memory 传 str(user_id)（P15 e2e bug ③ 连带——
        # codec 修好 embedding 绑定后，str 直绑 int 列会 DataError）。
        user_id = int(user_id)
        pool = get_pool()
        is_new = False
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, access_count, status FROM memory.semantic_entry "
                "WHERE user_id=$1 AND content=$2",
                user_id, content,
            )
            if existing:
                # §五 收口：同内容若本次以 active（explicit statement）写入，
                # 而既存为 candidate（LLM-inferred），promote 之（去重 + 状态提升合一）。
                # F1 修：promote 同步更新 memory_type——否则 LLM-inferred "insight" 行
                # 被 explicit stable_preference 重申后仍 memory_type='insight'，永远进不去
                # get_user_preferences() 过滤的 (stable_preference, temporary_preference) 子集。
                # P16.5：superseded 行被用户重申（explicit 重写）→ 恢复 active——
                # 否则 supersede→save 去重顺序使「重申后偏好消失」（active=0）。
                promote = (
                    existing["status"] in ("candidate", "superseded") and status == "active"
                )
                await conn.execute(
                    "UPDATE memory.semantic_entry SET access_count=access_count+1, "
                    "last_access_time=NOW()"
                    + (", memory_type=$5, status=$2, confidence=$3, scope=$4, updated_at=NOW()" if promote else "")
                    + " WHERE id=$1",
                    *( (existing["id"], status, confidence, scope, memory_type) if promote else (existing["id"],) ),
                )
                entry_id = existing["id"]
            else:
                is_new = True
                embedder = get_embedder()
                embedding = await embedder.embed_or_none(content)
                row = await conn.fetchrow(
                    """INSERT INTO memory.semantic_entry
                       (user_id, content, memory_type, importance_score, intent_embedding, source,
                        access_count, last_access_time,
                        scope, status, confidence, session_id, expires_at)
                       VALUES ($1, $2, $3, $4, $5, $6, 1, NOW(), $7, $8, $9, $10, $11)
                       RETURNING id""",
                    user_id, content, memory_type, importance_score, embedding, source,
                    scope, status, confidence, session_id, expires_at,
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
        self,
        user_id: str,
        query: str = "",
        top_k: Optional[int] = None,
        *,
        session_id: str | None = None,
    ) -> list[RankedMemory]:
        # user_id 归一 int：列是 integer；生产经 semantic_memory 传 str(user_id)。
        user_id = int(user_id)
        # post-review fix：top_k=0 表达「禁止召回」语义（Selective Recall decision 短路）
        # ——原 `k = top_k or self._top_k` 把 0 当 falsy 兜底为默认 5，会让
        # semantic=False 的决策仍然走 SQL 查 + record_access 副作用，污染 LRU/LFU 排序。
        k = self._top_k if top_k is None else top_k
        if k <= 0:
            return []
        pool = get_pool()
        embedder = get_embedder()
        embedding = await embedder.embed_or_none(query) if query else None
        now = datetime.datetime.now()

        async with pool.acquire() as conn:
            if embedding is not None:
                rows = await conn.fetch(
                    f"""SELECT id, content, memory_type, importance_score,
                              access_count, last_access_time, status, scope, confidence,
                              GREATEST(0, 1 - (intent_embedding <=> $1::vector)) AS sem_sim
                       FROM memory.semantic_entry
                       WHERE user_id=$2 AND intent_embedding IS NOT NULL
                         AND {_ACTIVE_RECALL_FILTER}
                         AND {_scope_filter(4)}
                       ORDER BY sem_sim DESC
                       LIMIT $3""",
                    embedding, user_id, k * 3, session_id,
                )
                scored = [self._score_entry(r, r["sem_sim"], now=now) for r in rows]
            else:
                keywords = [w for w in query.lower().replace(",", " ").split() if len(w) > 1] if query else []
                if not keywords:
                    rows = await conn.fetch(
                        f"""SELECT id, content, memory_type, importance_score,
                                  access_count, last_access_time, status, scope, confidence
                           FROM memory.semantic_entry
                           WHERE user_id=$1 AND {_ACTIVE_RECALL_FILTER}
                             AND {_scope_filter(3)}
                           ORDER BY last_access_time DESC LIMIT $2""",
                        user_id, k * 3, session_id,
                    )
                else:
                    patterns = [f"%{kw}%" for kw in keywords]
                    rows = await conn.fetch(
                        f"""SELECT id, content, memory_type, importance_score,
                                  access_count, last_access_time, status, scope, confidence
                           FROM memory.semantic_entry
                           WHERE user_id=$1 AND content ILIKE ANY($2::text[])
                             AND {_ACTIVE_RECALL_FILTER}
                             AND {_scope_filter(4)}
                           ORDER BY last_access_time DESC LIMIT $3""",
                        user_id, patterns, k * 3, session_id,
                    )
                scored = [self._score_entry(r, 0.0, now=now) for r in rows]

        # 排序按 (语义带, 带内热度)——热度只能裁决平局（理由见 infra/memory/scoring.py）
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = [mem for _, mem in scored[:k]]

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

    async def supersede_stable_preferences(self, user_id: str, content: str) -> int:
        """§六：把该 user 其他 active stable_preference（同 content）置 superseded。

        V1 仅同 content 精确冲突处理（显式重申同一偏好时避免双 active）；
        语义冲突 supersede 属后期。
        """
        # P16.5：user_id 归一 int（列是 integer；str 直绑 int 列会 DataError——
        # remember_explicit_preference 主链接入后 real 路径必调此函数，前此无 caller 潜伏）。
        user_id = int(user_id)
        pool = get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute(
                "UPDATE memory.semantic_entry SET status='superseded', updated_at=NOW() "
                "WHERE user_id=$1 AND memory_type='stable_preference' "
                "AND status='active' AND content=$2",
                user_id, content,
            )
        return int(str(res).split()[-1]) if res else 0

    async def supersede_active_by_contents(
        self, user_id: str, contents: list[str]
    ) -> int:
        """§六：把指定的若干条 active stable_preference 置 superseded。

        与 `supersede_stable_preferences`（同内容精确冲突）的分工：这里由**调用方**
        决定「哪些旧偏好已经过时」——判定逻辑属于 domain（`app/memory/preferences.py`
        的维度分类），infra 只执行写入，不反向依赖 domain。
        """
        if not contents:
            return 0
        user_id = int(user_id)
        pool = get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute(
                "UPDATE memory.semantic_entry SET status='superseded', updated_at=NOW() "
                "WHERE user_id=$1 AND memory_type='stable_preference' "
                "AND status='active' AND content = ANY($2::text[])",
                user_id, list(dict.fromkeys(contents)),
            )
        return int(str(res).split()[-1]) if res else 0

    async def list_active_preference_contents(self, user_id: str) -> list[str]:
        """当前 active 的偏好正文（维度冲突判定用；不下发向量、不算分）。"""
        user_id = int(user_id)
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT content FROM memory.semantic_entry "
                "WHERE user_id=$1 AND memory_type='stable_preference' AND status='active'",
                user_id,
            )
        return [r["content"] for r in rows]

    async def get_user_preferences(
        self,
        user_id: str,
        top_k: Optional[int] = None,
        *,
        session_id: str | None = None,
    ) -> list[RankedMemory]:
        # post-review fix：同 search()——top_k=0 表达「禁止召回」短路返回 []
        k = self._top_k if top_k is None else top_k
        if k <= 0:
            return []
        pool = get_pool()
        now = datetime.datetime.now()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT id, content, memory_type, importance_score,
                          access_count, last_access_time, status, scope, confidence
                   FROM memory.semantic_entry
                   WHERE user_id=$1 AND memory_type IN ('stable_preference', 'temporary_preference')
                     AND {_ACTIVE_RECALL_FILTER}
                     AND {_scope_filter(3)}
                   ORDER BY last_access_time DESC LIMIT $2""",
                user_id, k * 3, session_id,
            )
            scored = [self._score_entry(r, 0.0, now=now) for r in rows]

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [mem for _, mem in scored[:k]]

    def _score_entry(self, row, semantic_similarity: float, *, now=None):
        """一行 → (排序键, RankedMemory)。

        排序键是 `(语义带, 带内热度)`：热度（LFU/LRU）只能在语义打平时裁决，
        不能跨带反超（2026-11-09，plan memory-recall-scoreboard 追加）。
        `score` 字段仍是原来的加权和，前端/模型看到的「相关度」不变。
        """
        importance = row["importance_score"] or 0.0
        access_count = row["access_count"] or 0
        last_access = row.get("last_access_time")
        primary = max(semantic_similarity, 0.0) * 0.6 + min(importance, 1.0) * 0.2
        aux = (
            lfu(access_count) * 0.1
            + lru(_hours_since(last_access, now)) * 0.1
        )
        score = primary + aux
        return rank_key(primary, aux), self._row_to_memory(row, score)

    def _row_to_memory(self, row, score: float) -> RankedMemory:
        return RankedMemory(
            id=row["id"],
            content=row["content"],
            memory_type=row["memory_type"] or "insight",
            importance_score=row["importance_score"] or 0.0,
            access_count=row["access_count"] or 0,
            last_access_time=row.get("last_access_time"),
            score=score,
            status=row.get("status", "active"),
            scope=row.get("scope", "user"),
            confidence=row.get("confidence", "medium"),
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
