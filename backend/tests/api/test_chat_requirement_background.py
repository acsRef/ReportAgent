"""需求解析轮后台化（plan 2026-11-03-supplement-turn-durability）契约测试。

覆盖 hand-off P0 的真因：解析轮此前在 SSE 响应任务里跑 `graph.ainvoke`，
客户端刷新/断网一断连，图被取消 → `persist_draft` 回滚 → 新卡既不落库也不送达，
重进会话只能看到旧卡。后台化后：断连只取消订阅，任务跑完照常产出 requirement。

不依赖真实 DB / LLM：graph 用 FakeGraph，`session_manager.update_phase` 与 tracer 均 monkeypatch。
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable

import pytest

from app.infra.execution import registry
from app.main import ChatRequest, _chat_requirement_analysis
from app.models.requirement import RequirementCard, RequirementMissingField, RequirementOption

pytestmark = pytest.mark.api


# --- 公共替身 --------------------------------------------------------------


class FakeTracer:
    def __init__(self) -> None:
        self.end_calls: list[str] = []

    async def flush(self) -> None:
        pass

    def end(self, status: str) -> None:
        self.end_calls.append(status)


def _card(*, missing: bool = False, summary: str = "华东区域销售分析") -> RequirementCard:
    fields = (
        [
            RequirementMissingField(
                key="analysis_intent",
                label="你想看哪种视角？",
                kind="single",
                options=[
                    RequirementOption(label="总览", value="intent:overview"),
                    RequirementOption(label="区域对比", value="intent:compare_region"),
                ],
            )
        ]
        if missing
        else []
    )
    return RequirementCard(
        id="draft-1",
        version=2,
        status="missing" if missing else "complete",
        summary=summary,
        scope=["华东"],
        missing_fields=fields,
    )


class FakeGraph:
    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    async def ainvoke(self, initial: dict, config: dict) -> dict:
        if self._error:
            raise self._error
        return self._result


class GatedGraph:
    """挂起到 gate 被放行——用于模拟「解析仍在进行中」与断连。"""

    def __init__(self, result: dict) -> None:
        self._result = result
        self.started = asyncio.Event()
        self.gate = asyncio.Event()

    async def ainvoke(self, initial: dict, config: dict) -> dict:
        self.started.set()
        await self.gate.wait()
        return self._result


def _patch(monkeypatch, graph, calls: list) -> None:
    monkeypatch.setattr("app.main.build_requirement_analysis_graph", lambda: graph)
    monkeypatch.setattr("app.main.get_tracer", lambda *a, **kw: FakeTracer())

    async def fake_update(session_id, phase, failed_action=None):
        calls.append((session_id, phase, failed_action))

    monkeypatch.setattr("app.main.session_manager.update_phase", fake_update)


async def _wait_until(pred: Callable[[], bool], timeout: float = 2.0) -> None:
    async def poll() -> None:
        while not pred():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=timeout)


def _events_of(task: registry.ConfirmedTask) -> list[tuple[str, dict]]:
    return [(e["event"], json.loads(e["data"] or "{}")) for e in (task.result or [])]


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    registry._tasks.clear()


# --- 正常路径 --------------------------------------------------------------


async def test_card_turn_emits_requirement_and_writes_parsing_phase(monkeypatch):
    calls: list = []
    _patch(monkeypatch, FakeGraph({"requirement_card": _card(), "execution_status": "SUCCESS"}), calls)
    req = ChatRequest(user_query="只看华东", session_id="s1", mode="supplement")

    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s1")
    events = [e async for e in resp.body_iterator]

    assert [e["event"] for e in events] == ["phase", "phase", "requirement", "done"]
    assert json.loads(events[0]["data"]) == {"phase": "parsing"}
    assert json.loads(events[1]["data"]) == {"phase": "awaiting_confirm"}
    card = json.loads(events[2]["data"])
    assert card["scope"] == ["华东"]
    assert json.loads(events[3]["data"]) == {"final_phase": "awaiting_confirm"}
    # 起手先把 parsing 落库：刷新页面后快照能看到「解析中」，前端据此接后台轮询
    assert calls[0] == ("s1", "parsing", None)


async def test_missing_field_card_lands_awaiting_missing(monkeypatch):
    calls: list = []
    _patch(
        monkeypatch,
        FakeGraph({"requirement_card": _card(missing=True), "execution_status": "SUCCESS"}),
        calls,
    )
    req = ChatRequest(user_query="只看华东", session_id="s2", mode="supplement")
    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s2")
    events = [e async for e in resp.body_iterator]

    assert json.loads(events[1]["data"]) == {"phase": "awaiting_missing"}
    assert json.loads(events[-1]["data"]) == {"final_phase": "awaiting_missing"}


# --- 断连不丢结果（本 plan 的核心） ----------------------------------------


async def test_disconnect_keeps_running_and_still_produces_the_card(monkeypatch):
    calls: list = []
    graph = GatedGraph({"requirement_card": _card(), "execution_status": "SUCCESS"})
    _patch(monkeypatch, graph, calls)
    req = ChatRequest(user_query="只看华东", session_id="s3", mode="supplement")

    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s3")
    it = resp.body_iterator.__aiter__()
    first = await it.__anext__()
    assert json.loads(first["data"]) == {"phase": "parsing"}

    # 客户端断连（刷新页面 / 断网）：只关掉订阅
    await it.aclose()
    await graph.started.wait()
    graph.gate.set()

    task = registry.get_confirmed_task("s3")
    assert task is not None
    await _wait_until(lambda: task.finished)

    # 结果仍在：新卡 + 终态 done，全部可被迟到订阅者重放
    assert [name for name, _ in _events_of(task)] == ["phase", "requirement", "done"]
    assert _events_of(task)[0][1] == {"phase": "awaiting_confirm"}
    card = json.loads(task.result[1]["data"])
    assert card["summary"] == "华东区域销售分析"
    assert card["scope"] == ["华东"]
    assert _events_of(task)[-1][1] == {"final_phase": "awaiting_confirm"}


async def test_late_subscriber_can_replay_after_disconnect(monkeypatch):
    calls: list = []
    graph = GatedGraph({"requirement_card": _card(), "execution_status": "SUCCESS"})
    _patch(monkeypatch, graph, calls)
    req = ChatRequest(user_query="只看华东", session_id="s4", mode="supplement")

    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s4")
    it = resp.body_iterator.__aiter__()
    await it.__anext__()
    await it.aclose()
    await graph.started.wait()
    graph.gate.set()
    task = registry.get_confirmed_task("s4")
    await _wait_until(lambda: task.finished)

    from app.main import _subscribe_events

    replay = [e async for e in _subscribe_events(task, "parsing")]
    assert [e["event"] for e in replay] == ["phase", "requirement", "done"]


# --- 失败路径：绝不假装成功 ------------------------------------------------


async def test_persist_failed_does_not_emit_a_card(monkeypatch):
    calls: list = []
    _patch(
        monkeypatch,
        FakeGraph({
            "requirement_card": _card(),
            "execution_status": "FAILED",
            "error": {"code": "PERSIST_FAILED", "message": "db down"},
        }),
        calls,
    )
    req = ChatRequest(user_query="只看华东", session_id="s5", mode="supplement")
    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s5")
    events = [e async for e in resp.body_iterator]

    assert [e["event"] for e in events] == ["phase", "error", "done"]
    err = json.loads(events[1]["data"])
    assert err["code"] == "PERSIST_FAILED"
    assert err["recoverable"] is True
    assert json.loads(events[-1]["data"]) == {"final_phase": "error"}
    assert ("s5", "error", "supplement") in calls


async def test_missing_card_emits_no_card_error(monkeypatch):
    calls: list = []
    _patch(monkeypatch, FakeGraph({"requirement_card": None, "execution_status": "FAILED"}), calls)
    req = ChatRequest(user_query="q", session_id="s6", mode="new")
    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s6")
    events = [e async for e in resp.body_iterator]

    err = json.loads(events[1]["data"])
    assert events[1]["event"] == "error"
    assert err["code"] == "NO_CARD"


async def test_task_timeout_is_bounded_and_reports(monkeypatch):
    calls: list = []
    _patch(monkeypatch, FakeGraph(error=None), calls)
    monkeypatch.setattr("app.main.MAX_TASK_DURATION", 0.05)

    class HangingGraph:
        async def ainvoke(self, initial: dict, config: dict) -> dict:
            await asyncio.sleep(999)
            return {}

    monkeypatch.setattr("app.main.build_requirement_analysis_graph", lambda: HangingGraph())
    req = ChatRequest(user_query="q", session_id="s7", mode="new")
    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s7")
    events = [e async for e in resp.body_iterator]

    err = json.loads(events[1]["data"])
    assert events[1]["event"] == "error"
    assert err["code"] == "TASK_TIMEOUT"
    assert json.loads(events[-1]["data"]) == {"final_phase": "error"}
    assert ("s7", "error", "new") in calls


# --- 执行过程（trace 帧）----------------------------------------------------


class TraceEmittingGraph:
    """模拟 langgraph 回调：对给定节点发 running/success，验证需求轮也发 trace 帧。"""

    def __init__(self, result: dict, nodes: tuple[str, ...]) -> None:
        self._result = result
        self._nodes = nodes

    async def ainvoke(self, initial: dict, config: dict) -> dict:
        for handler in config.get("callbacks") or []:
            for node in self._nodes:
                await handler.on_chain_start(
                    {}, {}, metadata={"langgraph_node": node}, name=node,
                )
                await handler.on_chain_end(
                    {}, metadata={"langgraph_node": node}, name=node,
                )
        return self._result


async def test_requirement_turn_emits_trace_frames(monkeypatch):
    """需求轮也要发 trace 帧（plan 2026-11-04-trace-visible-reasoning）。

    用户要能看见「Agent 在想什么」：识别意图 → 解析需求 → 保存需求卡。
    此前需求轮只发一条 phase=parsing，前端什么步骤都看不到。
    """
    calls: list = []
    graph = TraceEmittingGraph(
        {"requirement_card": _card(), "execution_status": "SUCCESS"},
        ("classify_intent", "requirement_parse", "persist_draft"),
    )
    _patch(monkeypatch, graph, calls)
    req = ChatRequest(user_query="2024年各区域销售额", session_id="s-trace", mode="new")

    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s-trace")
    events = [e async for e in resp.body_iterator]

    traces = [json.loads(e["data"]) for e in events if e["event"] == "trace"]
    assert [t["step"] for t in traces] == [
        "识别意图", "识别意图", "解析需求", "解析需求", "保存需求卡", "保存需求卡",
    ]
    assert traces[0]["status"] == "running" and traces[0]["kind"] == "agent"
    assert traces[1]["status"] == "success"
    # trace 帧不能挤掉原有终态事件
    assert [e["event"] for e in events][-1] == "done"
    assert "requirement" in [e["event"] for e in events]


# --- 同会话重入 ------------------------------------------------------------


async def test_busy_session_returns_409(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def hanging(t: registry.ConfirmedTask) -> None:
        started.set()
        await release.wait()

    calls: list = []
    _patch(monkeypatch, FakeGraph({"requirement_card": _card()}), calls)
    t1 = registry.start_confirmed_task("s8", 1, "requirement", hanging)
    await started.wait()

    req = ChatRequest(user_query="q", session_id="s8", mode="new")
    with pytest.raises(Exception) as excinfo:
        await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s8")
    assert getattr(excinfo.value, "status_code", None) == 409

    release.set()
    await asyncio.wait_for(t1.events.get(), timeout=1)
    assert t1.finished


# --- 显式偏好短路不进图 ----------------------------------------------------


async def test_explicit_preference_short_circuits_without_graph(monkeypatch):
    calls: list = []

    def _boom():  # 图一旦被构建就失败——证明短路真的没进图
        raise AssertionError("graph must not be built for explicit preference")

    _patch(monkeypatch, FakeGraph({}), calls)
    monkeypatch.setattr("app.main.build_requirement_analysis_graph", _boom)

    async def fake_reply(query: str, user_id: int):
        return "好的，已记住偏好：" + query

    monkeypatch.setattr("app.main._maybe_explicit_preference_reply", fake_reply)
    req = ChatRequest(user_query="以后都用柱状图", session_id="s9", mode="new")
    resp = await _chat_requirement_analysis(req, None, {"id": 1}, session_id="s9")
    events = [e async for e in resp.body_iterator]

    assert [e["event"] for e in events] == ["phase", "report", "done"]
    assert json.loads(events[0]["data"]) == {"phase": "idle"}
    assert json.loads(events[-1]["data"]) == {"final_phase": "idle"}
    assert registry.get_confirmed_task("s9") is None
