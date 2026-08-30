"""requirement-analysis SSE 流契约：chitchat 终态 idle（非 error）。

P11 F4：chitchat 分支此前 `phase if "phase" in dir() else "error"` 把
final_phase 写成 error，且 casual reply 前不复位 phase——现契约：先
`phase(idle)` 复位再发 report(闲聊回复)，`done.final_phase = idle`。
"""
import json

import pytest

from app.main import _chat_requirement_analysis, ChatRequest

pytestmark = pytest.mark.api


class _FakeTracer:
    async def flush(self) -> None:
        pass


def _patch_stream(monkeypatch, graph_result: dict) -> None:
    class _G:
        async def ainvoke(self, initial, config):
            return graph_result

    monkeypatch.setattr("app.main.build_requirement_analysis_graph", lambda: _G())
    monkeypatch.setattr("app.main.get_tracer", lambda *a, **kw: _FakeTracer())


async def test_chitchat_done_phase_idle(monkeypatch):
    _patch_stream(monkeypatch, {"intent": "chitchat", "casual_reply": "你好！"})
    req = ChatRequest(user_query="你好", session_id="s", mode="new")
    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s")

    events = [e async for e in resp.body_iterator]
    evt_map = [(e["event"], json.loads(e["data"] or "{}")) for e in events]

    assert [e for e, _ in evt_map] == ["phase", "phase", "report", "done"]
    # 先是 parsing，再复位 idle，闲聊回复，done 终态 idle
    assert evt_map[0] == ("phase", {"phase": "parsing"})
    assert evt_map[1] == ("phase", {"phase": "idle"})
    assert evt_map[2][0] == "report"
    assert evt_map[2][1]["answer"]["text"] == "你好！"
    assert evt_map[3] == ("done", {"final_phase": "idle"})


async def test_requirement_generic_exception_uses_user_copy(monkeypatch):
    class _Boom:
        async def ainvoke(self, initial, config):
            raise RuntimeError("openai raw provider leak")

    monkeypatch.setattr("app.main.build_requirement_analysis_graph", lambda: _Boom())
    monkeypatch.setattr("app.main.get_tracer", lambda *a, **kw: _FakeTracer())
    req = ChatRequest(user_query="q", session_id="s", mode="new")
    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s")

    events = [e async for e in resp.body_iterator]
    err = next(
        json.loads(e["data"]) for e in events if e["event"] == "error"
    )
    from app.reliability.errors import user_message

    assert err["message"] == user_message("other")
    assert "openai" not in err["message"]