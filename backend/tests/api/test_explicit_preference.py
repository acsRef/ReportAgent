"""P16.5：explicit preference 图前短路 API 契约（offline）。

§五 explicit statement → active 偏好的主链接线（_chat_requirement_analysis）：
- 偏好句：不进 requirement 图（_Boom graph 不炸即证明短路）、复用 chitchat 终态
  （phase idle + report.answer.text）、写入失败仍轻响应（best-effort 不阻塞）；
- 非偏好句：图正常 invoke（既有行为不破）。
"""
import json

import pytest

from app.main import _chat_requirement_analysis, ChatRequest

pytestmark = pytest.mark.api


class _FakeTracer:
    async def flush(self) -> None:
        pass


def _patch_graph(monkeypatch, graph_result: dict, *, boom: bool = False) -> list:
    """patch 图；boom=True 时 ainvoke 必抛——短路正确则永不触达。"""
    called: list = []

    class _G:
        async def ainvoke(self, initial, config):
            called.append(True)
            if boom:
                raise RuntimeError("graph should not be invoked on preference statement")
            return graph_result

    monkeypatch.setattr("app.main.build_requirement_analysis_graph", lambda: _G())
    monkeypatch.setattr("app.main.get_tracer", lambda *a, **kw: _FakeTracer())
    return called


async def test_preference_statement_short_circuits_before_graph(monkeypatch):
    called = _patch_graph(monkeypatch, {}, boom=True)
    req = ChatRequest(user_query="以后都用柱状图", session_id="s", mode="new")
    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s")

    events = [e async for e in resp.body_iterator]
    evt_map = [(e["event"], json.loads(e["data"] or "{}")) for e in events]

    assert called == [], "偏好句不应 invoke requirement 图"
    assert [e for e, _ in evt_map] == ["phase", "phase", "report", "done"]
    assert evt_map[0] == ("phase", {"phase": "parsing"})
    assert evt_map[1] == ("phase", {"phase": "idle"})
    assert evt_map[2][0] == "report"
    assert "已记住" in evt_map[2][1]["answer"]["text"]
    assert evt_map[3] == ("done", {"final_phase": "idle"})


async def test_non_preference_query_still_invokes_graph(monkeypatch):
    called = _patch_graph(
        monkeypatch, {"intent": "chitchat", "casual_reply": "你好！"},
    )
    req = ChatRequest(user_query="统计2024年各区域销售额", session_id="s", mode="new")
    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s")
    events = [e async for e in resp.body_iterator]

    assert called, "非偏好句应正常 invoke 图"
    evt_map = [(e["event"], json.loads(e["data"] or "{}")) for e in events]
    assert evt_map[-2][1]["answer"]["text"] == "你好！"  # 原 chitchat 渲染形态不破


async def test_remember_failure_falls_back_to_light_reply(monkeypatch):
    _patch_graph(monkeypatch, {}, boom=True)

    async def _boom(*a, **k):
        raise RuntimeError("pg down")

    monkeypatch.setattr(
        "app.memory.manager.remember_explicit_preference", _boom,
    )
    req = ChatRequest(user_query="以后都用柱状图", session_id="s", mode="new")
    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s")
    events = [e async for e in resp.body_iterator]

    evt_map = [(e["event"], json.loads(e["data"] or "{}")) for e in events]
    assert evt_map[2][0] == "report"
    assert "已记住" in evt_map[2][1]["answer"]["text"], "写入失败不得阻塞响应"
    assert evt_map[3] == ("done", {"final_phase": "idle"})
