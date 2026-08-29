"""P4b T5 write pipeline + SelectiveRecallPolicy 契约钉子。

write pipeline（§五 固定规则，无 LLM）：
- remember_explicit_preference：MemoryPolicy.extract_preference 命中 → active
  stable_preference / confidence=high；未命中 → None（§四 Discard）
- remember_inferred_facts：LLM-inferred → status=candidate / confidence=low（§五 line 49）
- remember_conversation_facts：现路由到 inferred → candidate（修 §五 违规）
- supersede：新 active stable 写前，同 (user,key) 旧 active → superseded

SelectiveRecallPolicy（§二四触发 + §三 agent 表）：
- 纯规则，返回 RecallDecision
- 四触发各正反例；Report agent 不召回 query；闲聊不召回
"""
from __future__ import annotations

import pytest

from app.context.decision import RecallDecision, SelectiveRecallPolicy
from app.context.policy import AgentContextPolicy

pytestmark = pytest.mark.contracts


# --- write pipeline ---------------------------------------------------------

class _SavedCall:
    def __init__(self):
        self.calls: list[dict] = []


@pytest.fixture
def mm_spy(monkeypatch):
    saved = []

    async def fake_remember(self, user_id, content, memory_type="insight",
                            importance=0.3, source="", *, scope="user",
                            status="active", confidence="medium",
                            session_id=None, expires_at=None, **extra):
        saved.append({
            "content": content, "memory_type": memory_type, "source": source,
            "scope": scope, "status": status, "confidence": confidence,
            "session_id": session_id,
        })
        return len(saved)

    superseded: list = []

    async def fake_supersede(self, user_id, key):
        superseded.append((user_id, key))
        return 1

    monkeypatch.setattr(
        "app.infra.memory.memory_manager.MemoryManager.remember_preference",
        fake_remember,
    )
    monkeypatch.setattr(
        "app.infra.memory.memory_manager.MemoryManager.supersede_stable_preference",
        fake_supersede,
        raising=False,
    )
    return saved, superseded


@pytest.mark.asyncio
async def test_explicit_preference_writes_active_high(mm_spy):
    from app.memory.manager import remember_explicit_preference
    saved, superseded = mm_spy
    rid = await remember_explicit_preference(1, "以后都用柱状图显示", source="user_turn")
    assert rid is not None
    assert saved and saved[0]["status"] == "active"
    assert saved[0]["confidence"] == "high"
    assert saved[0]["memory_type"] == "stable_preference"
    assert saved[0]["scope"] == "user"
    # supersede 旧 active 同键被调用
    assert superseded, "写新 active stable 前应先 supersede 旧 active"


@pytest.mark.asyncio
async def test_non_explicit_preference_is_discarded(mm_spy):
    from app.memory.manager import remember_explicit_preference
    saved, _ = mm_spy
    rid = await remember_explicit_preference(1, "帮我查下华东销售", source="user_turn")
    assert rid is None          # §四 Discard：非 explicit 不写 active
    assert saved == []


@pytest.mark.asyncio
async def test_inferred_facts_write_as_candidate(mm_spy):
    from app.memory.manager import remember_inferred_facts
    saved, _ = mm_spy
    await remember_inferred_facts(1, ["字段映射销售额=total_amount", "用户偏好柱状图"])
    assert saved and all(s["status"] == "candidate" for s in saved)  # §五 line 49
    assert all(s["confidence"] == "low" for s in saved)


@pytest.mark.asyncio
async def test_conversation_facts_route_to_candidate(mm_spy):
    # 修 §五 违规：compress 抽的 LLM-inferred fact 现落 candidate（非 active）
    from app.memory.manager import remember_conversation_facts
    saved, _ = mm_spy
    updates = {
        "extracted_schemas": [{"db_field": "total_amount"}],
        "extracted_preferences": ["偏好柱状图"],
    }
    await remember_conversation_facts(1, updates, compressed_batch=[])
    assert saved and all(s["status"] == "candidate" for s in saved)


# --- SelectiveRecallPolicy --------------------------------------------------

def test_history_reference_triggers_conversation():
    d = SelectiveRecallPolicy().decide(
        query="再按产品细分一下", agent_policy=AgentContextPolicy.REQUIREMENT,
        session_state={},
    )
    assert isinstance(d, RecallDecision)
    assert d.conversation is True   # 触发1 历史引用


def test_plain_complete_query_skips_semantic():
    # 「与历史无关 / query 已完整」→ 保守：不强制召 semantic（触发2/3/4 不命中）
    d = SelectiveRecallPolicy().decide(
        query="2024年各区域销售额排名前10", agent_policy=AgentContextPolicy.REPORT,
        session_state={"memory_candidates_present": False},
    )
    assert d.query is False  # §三 Report 永不召回 Query Experience


def test_report_agent_never_recalls_query():
    d = SelectiveRecallPolicy().decide(
        query="继续刚才的销售分析并生成报告", agent_policy=AgentContextPolicy.REPORT,
        session_state={},
    )
    assert d.query is False
    assert d.conversation is True  # 历史引用「刚才」


def test_execution_agent_recalls_semantic_and_query():
    d = SelectiveRecallPolicy().decide(
        query="GMV 按季度统计销售额", agent_policy=AgentContextPolicy.EXECUTION,
        session_state={},
    )
    # 业务定义「GMV」→ semantic；Execution 允许 query experience
    assert d.semantic is True
    assert d.query is True


def test_chitchat_recalls_nothing():
    d = SelectiveRecallPolicy().decide(
        query="你好啊", agent_policy=AgentContextPolicy.REQUIREMENT,
        session_state={},
    )
    assert d.conversation is False
    assert d.semantic is False
    assert d.query is False


# --- F5：四触发条件负例（plan §F5 钉子） -----------------------------------


def test_no_history_ref_keeps_conversation_off():
    # 触发1（历史引用）负例：query 无历史引用词 → conversation=False
    d = SelectiveRecallPolicy().decide(
        query="2024 销售排名", agent_policy=AgentContextPolicy.REQUIREMENT,
        session_state={},
    )
    assert d.conversation is False


def test_pref_keyword_triggers_semantic():
    # 触发2（长期偏好影响当前任务）正例：query 含 _PREF_TASK 词「图表」 → semantic=True
    d = SelectiveRecallPolicy().decide(
        query="帮我做个图表", agent_policy=AgentContextPolicy.REQUIREMENT,
        session_state={},
    )
    assert d.semantic is True


def test_no_biz_def_skips_semantic():
    # 触发3（业务定义）负例：query 无 _BIZ_DEF 词且无 _PREF_TASK 词 → semantic=False
    d = SelectiveRecallPolicy().decide(
        query="查看用户列表", agent_policy=AgentContextPolicy.REQUIREMENT,
        session_state={},
    )
    assert d.semantic is False


def test_execution_no_data_verb_skips_query():
    # 触发4（query experience）负例：query 无 _DATA_VERB 词 → query=False
    d = SelectiveRecallPolicy().decide(
        query="休息一下", agent_policy=AgentContextPolicy.EXECUTION,
        session_state={},
    )
    assert d.query is False


# --- F6：§三 agent 表分流（plan §F6 钉子） -----------------------------------


def test_requirement_query_with_history_and_data_recalls_query():
    # §三 agent 表 REQUIREMENT 分流：query 含 _HISTORY_REF 词 + _DATA_VERB 词
    # → query=True / top_k_queries=1（少量）
    d = SelectiveRecallPolicy().decide(
        query="再按区域统计一下", agent_policy=AgentContextPolicy.REQUIREMENT,
        session_state={},
    )
    assert d.query is True
    assert d.top_k_queries == 1


def test_requirement_query_data_only_skips_query():
    # §三 agent 表 REQUIREMENT 分流：query 仅 _DATA_VERB（无 _HISTORY_REF）
    # → query=False / top_k_queries=0（少量策略）
    d = SelectiveRecallPolicy().decide(
        query="统计各区域销售", agent_policy=AgentContextPolicy.REQUIREMENT,
        session_state={},
    )
    assert d.query is False
    assert d.top_k_queries == 0
