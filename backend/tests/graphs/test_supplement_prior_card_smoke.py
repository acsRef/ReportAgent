"""P15 e2e T2 smoke：mode channel 在 _requirement_parse 真可见并转发 prior_card。

P0-2 修正目标：不靠「initial state 塞字段 LangGraph 就传」的假设——直接驱动
_requirement_parse 节点（state 带 mode），patch 掉 DB/LLM 依赖，断言：
  - mode=supplement + 上一轮卡 complete → parse_requirement 收到该 prior_card
  - mode=new → 不加载、prior_card=None（不跨轮污染）
"""
from __future__ import annotations

import pytest

from app.models.requirement import RequirementCard

pytestmark = pytest.mark.graphs


class _FakeContextRuntime:
    """ContextRuntime.build 存根：跳过 conversation/memory DB 路径。"""

    async def build(self, **kwargs):
        return {"conversation_context": "", "assembled_context": ""}


@pytest.mark.asyncio
async def test_supplement_mode_forwards_prior_card(monkeypatch):
    from app.agent import requirement_analysis_graph as g

    prior = RequirementCard(
        id="draft-1", version=1, status="complete", summary="2024年华东销售额",
        target_metrics=["销售额"], time_range="2024年", scope=["华东"],
        missing_fields=[], assumptions=[],
    )

    async def fake_get_latest_card(*, session_id, user_id):
        return prior

    seen: dict = {}

    def fake_parse_requirement(**kwargs):
        seen["prior_card"] = kwargs.get("prior_card")
        return RequirementCard(
            id="draft-2", version=2, status="missing", summary=kwargs.get("user_query", ""),
            missing_fields=[], assumptions=[],
        )

    monkeypatch.setattr("app.context.runtime.ContextRuntime", _FakeContextRuntime)
    monkeypatch.setattr("app.services.requirement_service.get_latest_card", fake_get_latest_card)
    monkeypatch.setattr(g, "parse_requirement", fake_parse_requirement)

    state = {
        "session_id": "s1", "user_id": 1, "user_query": "再看月度趋势",
        "mode": "supplement", "schema_context": None, "dict_context": "",
    }
    await g._requirement_parse(dict(state))
    assert seen.get("prior_card") is prior, "supplement 轮应把上一轮 complete 卡传给 parse_requirement"


@pytest.mark.asyncio
async def test_new_mode_does_not_load_prior(monkeypatch):
    from app.agent import requirement_analysis_graph as g

    seen: dict = {}

    def fake_parse_requirement(**kwargs):
        seen["prior_card"] = kwargs.get("prior_card")
        return RequirementCard(
            id="draft-2", version=1, status="missing", summary=kwargs.get("user_query", ""),
            missing_fields=[], assumptions=[],
        )

    monkeypatch.setattr("app.context.runtime.ContextRuntime", _FakeContextRuntime)
    monkeypatch.setattr(g, "parse_requirement", fake_parse_requirement)

    state = {
        "session_id": "s1", "user_id": 1, "user_query": "2024年华东销售额",
        "mode": "new", "schema_context": None, "dict_context": "",
    }
    await g._requirement_parse(dict(state))
    assert seen.get("prior_card") is None, "mode=new 不应加载上一轮卡（避免跨轮污染）"
