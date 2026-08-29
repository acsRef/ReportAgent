"""P4b T4 structured recall 契约钉子。

- MemoryManager.recall_structured() -> list[RecallItem]，带 kind/score/ref_id/source
- 旧 recall() -> str 保留，且 == "\\n".join(item.raw_text)（单点格式化，无逻辑双写）
- RecallItem.source 扩 memory_query/memory_semantic/memory_preference（破 P3 单 source）
- ContextRuntime Step4 用 recall_structured（RecallItem 不再 1:1 包 string）

底层 QueryMemory/UserMemory 用 patch 喂 structured 行，不触 DB。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contracts


class _Rank:
    def __init__(self, id, content, memory_type, score):
        self.id = id; self.content = content; self.memory_type = memory_type
        self.score = score; self.status = "active"; self.scope = "user"
        self.confidence = "high"; self.importance_score = 0.8
        self.access_count = 1; self.last_access_time = None


@pytest.mark.asyncio
async def test_recall_structured_returns_typed_items(monkeypatch):
    from app.infra.memory import memory_manager as mm

    async def fake_search_similar(self, question, top_k=3, *, user_id):
        return [{"id": 7, "question": "华东销售", "sql": "SELECT 1",
                 "target_metric": "amt", "success_count": 3, "failure_count": 0,
                 "access_count": 3, "score": 0.9}]

    async def fake_user_search(self, user_id, query="", top_k=None):
        return [_Rank(11, "用户偏好柱状图", "stable_preference", 0.8)]

    monkeypatch.setattr(
        "app.infra.memory.query_memory.QueryMemory.search_similar", fake_search_similar)
    monkeypatch.setattr(
        "app.infra.memory.user_memory.UserMemory.search", fake_user_search)

    items = await mm.MemoryManager().recall_structured("华东", "42")
    assert isinstance(items, list)
    assert all(isinstance(i, dict) and "raw_text" in i and "kind" in i
               and "source" in i and "score" in i and "ref_id" in i for i in items)
    kinds = {i["kind"] for i in items}
    assert "query" in kinds
    sources = {i["source"] for i in items}
    assert "memory_query" in sources
    # stable_preference → preference kind
    pref = [i for i in items if i["kind"] == "preference"]
    assert pref and pref[0]["source"] == "memory_preference"
    assert pref[0]["ref_id"] == 11


@pytest.mark.asyncio
async def test_legacy_recall_delegates_to_structured_and_joins(monkeypatch):
    from app.infra.memory import memory_manager as mm

    async def fake_search_similar(self, question, top_k=3, *, user_id):
        return [{"id": 7, "question": "Q", "sql": "SELECT 1",
                 "target_metric": "", "success_count": 1, "failure_count": 0,
                 "access_count": 1, "score": 0.5}]

    async def fake_user_search(self, user_id, query="", top_k=None):
        return [_Rank(11, "P", "stable_preference", 0.6)]

    monkeypatch.setattr(
        "app.infra.memory.query_memory.QueryMemory.search_similar", fake_search_similar)
    monkeypatch.setattr(
        "app.infra.memory.user_memory.UserMemory.search", fake_user_search)

    s = await mm.MemoryManager().recall("q", "1")
    assert isinstance(s, str)
    structured = await mm.MemoryManager().recall_structured("q", "1")
    assert s == "\n".join(i["raw_text"] for i in structured)


@pytest.mark.asyncio
async def test_context_runtime_step4_uses_structured(monkeypatch):
    from app.context.runtime import ContextRuntime
    from app.infra.memory import memory_manager as mm

    async def fake_prepare(session_id, user_id):
        return "CTX"
    async def fake_recall_structured(self, query, user_id, *, top_k_queries=2,
                                     top_k_preferences=3):
        return [{"raw_text": "结构化召回", "kind": "query",
                 "source": "memory_query", "score": 0.9, "ref_id": 3}]

    monkeypatch.setattr(
        "app.context.runtime.prepare_conversation_context", fake_prepare)
    monkeypatch.setattr(
        mm.MemoryManager, "recall_structured", fake_recall_structured)

    bundle = await ContextRuntime().build(
        session_id="s", user_id=1, query="q", agent="sql_plan")
    assert bundle["recall_items"][0]["kind"] == "query"
    assert bundle["recall_items"][0]["source"] == "memory_query"
    assert bundle["recall_items"][0]["ref_id"] == 3


def test_recall_item_source_literal_extended():
    import typing
    from app.context.assembler import RecallItem
    ann = typing.get_type_hints(RecallItem, include_extras=True)
    # source Literal 至少含新增三种 + legacy
    args = set(typing.get_args(ann["source"]))
    assert {
        "legacy_memory_manager", "memory_query", "memory_semantic", "memory_preference",
    } <= args


@pytest.mark.asyncio
async def test_recall_structured_semantic_item_uses_memory_semantic_source(monkeypatch):
    """F12 钉子：UserMemory 召回的 memory_type='insight' 行 → kind='semantic' 且
    source='memory_semantic'（区别于 memory_preference），与 assembler RecallItem Literal 一致。"""
    from app.infra.memory import memory_manager as mm

    async def fake_search_similar(self, question, top_k=3, *, user_id):
        return []  # 不混入 query 类 item，单独钉 semantic 路径

    async def fake_user_search(self, user_id, query="", top_k=None):
        # memory_type='insight'（不在 _PREFERENCE_TYPES）→ kind='semantic' / source='memory_semantic'
        return [_Rank(22, "华东=region_east", "insight", 0.7)]

    monkeypatch.setattr(
        "app.infra.memory.query_memory.QueryMemory.search_similar", fake_search_similar)
    monkeypatch.setattr(
        "app.infra.memory.user_memory.UserMemory.search", fake_user_search)

    items = await mm.MemoryManager().recall_structured("华东", "42")
    assert len(items) == 1
    item = items[0]
    assert item["kind"] == "semantic"
    assert item["source"] == "memory_semantic"
    assert item["ref_id"] == 22
    # raw_text 含 memory_type + content + score，结构同 memory_manager.py:54
    assert "[insight]" in item["raw_text"]
    assert "华东=region_east" in item["raw_text"]
