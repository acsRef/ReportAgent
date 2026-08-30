"""确认流后台化集成测试：409 BUSY / 后台完成落库 / 迟到订阅 / 事件契约。

直接驱动 main.py 的 _run_confirmed_graph / _subscribe_events / _start_confirmed_stream，
用 FakeGraph mock ainvoke，monkeypatch tracer 与 update_phase——不依赖真实 DB / LLM。
"""
import asyncio
import json

import pytest

from app.agent.confirmed_execution_graph import RequirementIncompleteError
from app.infra.execution import registry
from app.main import (
    _run_confirmed_graph,
    _start_confirmed_stream,
    _subscribe_events,
)

pytestmark = pytest.mark.api


class FakeTracer:
    async def flush(self) -> None:
        pass


class FakeGraph:
    def __init__(self, result: dict | None = None, error: Exception | None = None):
        self._result = result
        self._error = error

    async def ainvoke(self, initial: dict, config: dict) -> dict:
        if self._error:
            raise self._error
        return self._result


SUCCESS_RESULT = {
    "execution_status": "SUCCESS",
    "report_payload": {"answer": {"text": "ok"}, "execution_status": "SUCCESS"},
    "error": None,
    "sql": "SELECT 1",
}

FAILED_RESULT = {
    "execution_status": "FAILED",
    "report_payload": None,
    "error": {"kind": "timeout", "message": "timeout", "code": "EXECUTION_ERROR"},
    "sql": "SELECT 1",
}


def _patch_deps(monkeypatch, calls: list):
    monkeypatch.setattr("app.main.get_tracer", lambda *a, **kw: FakeTracer())

    async def fake_update(session_id, phase, failed_action=None):
        calls.append((session_id, phase, failed_action))

    monkeypatch.setattr("app.main.session_manager.update_phase", fake_update)
    return fake_update


# --- _run_confirmed_graph：后台任务事件 + phase 写入 ---------------------------------


async def test_success_emits_report_and_phase(monkeypatch):
    calls: list = []
    _patch_deps(monkeypatch, calls)
    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    await _run_confirmed_graph(
        task, FakeGraph(SUCCESS_RESULT), {"trace_id": "t1"}, "s1", "confirm"
    )
    assert task.finished
    assert task.result is not None
    assert [e["event"] for e in task.result] == ["report", "done"]
    assert json.loads(task.result[1]["data"])["final_phase"] == "report_ready"
    assert ("s1", "report_ready", None) in calls


async def test_failed_emits_error_and_phase(monkeypatch):
    calls: list = []
    _patch_deps(monkeypatch, calls)
    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    await _run_confirmed_graph(
        task, FakeGraph(FAILED_RESULT), {"trace_id": "t1"}, "s1", "confirm"
    )
    assert [e["event"] for e in task.result] == ["error", "done"]
    err = json.loads(task.result[0]["data"])
    assert err["code"] == "QUERY_TIMEOUT"
    assert err["sql"] == "SELECT 1"
    assert ("s1", "error", "sql") in calls


async def test_graph_exception_emits_event(monkeypatch):
    calls: list = []
    _patch_deps(monkeypatch, calls)
    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    graph = FakeGraph(error=RequirementIncompleteError("missing fields"))
    await _run_confirmed_graph(
        task, graph, {"trace_id": "t1"}, "s1", "confirm"
    )
    assert [e["event"] for e in task.result] == ["error", "done"]
    err = json.loads(task.result[0]["data"])
    assert err["code"] == "REQUIREMENT_INCOMPLETE"
    assert ("s1", "error", "confirm") in calls


# --- P9 背景任务超时（MAX_TASK_DURATION → Persist FAILED → TASK_TIMEOUT 事件）-------


class HangingGraph:
    async def ainvoke(self, initial: dict, config: dict) -> dict:
        await asyncio.sleep(999)


async def test_task_timeout_emits_task_timeout_and_persists_error(monkeypatch):
    calls: list = []
    _patch_deps(monkeypatch, calls)
    persist_calls: list = []

    async def fake_persist_error_run(**kwargs):
        persist_calls.append(kwargs)
        return {"version": 1}

    monkeypatch.setattr(
        "app.main.report_version_service.persist_error_run", fake_persist_error_run
    )
    monkeypatch.setattr("app.main.MAX_TASK_DURATION", 0.05)
    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    await _run_confirmed_graph(
        task, HangingGraph(), {"trace_id": "t1", "user_query": "q"}, "s1", "confirm"
    )

    # 不允许永远停在 generating：error + done 事件、phase=error、FAILED 落库
    assert [e["event"] for e in task.result] == ["error", "done"]
    err = json.loads(task.result[0]["data"])
    assert err["code"] == "TASK_TIMEOUT"
    assert err["recoverable"] is False
    assert json.loads(task.result[1]["data"])["final_phase"] == "error"
    assert ("s1", "error", "confirm") in calls

    assert len(persist_calls) == 1
    kwargs = persist_calls[0]
    assert kwargs["session_id"] == "s1"
    assert kwargs["user_id"] == 1
    # graph 被 cancel 后 draft_id 拿不到——诚实降级传 None
    assert kwargs["requirement_draft_id"] is None
    assert kwargs["title"] == "报告"
    assert kwargs["error_detail"]["code"] == "TASK_TIMEOUT"
    assert kwargs["error_detail"]["kind"] == "timeout"
    assert kwargs["trace_id"] == "t1"


async def test_fast_graph_unaffected_by_timeout_budget(monkeypatch):
    calls: list = []
    _patch_deps(monkeypatch, calls)
    monkeypatch.setattr("app.main.MAX_TASK_DURATION", 5)
    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    await _run_confirmed_graph(
        task, FakeGraph(SUCCESS_RESULT), {"trace_id": "t1"}, "s1", "confirm"
    )
    # 未超时路径行为不变：report + done、report_ready
    assert [e["event"] for e in task.result] == ["report", "done"]
    assert ("s1", "report_ready", None) in calls


async def test_generic_exception_classified_via_envelope(monkeypatch):
    """P9：泛化异常出口走 classify_exception——LLM 预算耗尽不再是笼统 INTERNAL。"""
    calls: list = []
    _patch_deps(monkeypatch, calls)
    from app.reliability.retry import LLMTimeoutError

    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    graph = FakeGraph(error=LLMTimeoutError("budget"))
    await _run_confirmed_graph(
        task, graph, {"trace_id": "t1"}, "s1", "confirm"
    )
    assert [e["event"] for e in task.result] == ["error", "done"]
    err = json.loads(task.result[0]["data"])
    assert err["code"] == "LLM_TIMEOUT"
    assert err["recoverable"] is True
    assert ("s1", "error", "confirm") in calls


async def test_unknown_exception_falls_to_internal_error(monkeypatch):
    """未知异常兜底 INTERNAL_ERROR / recoverable=False（原 INTERNAL 语义收编为契约码）。"""
    calls: list = []
    _patch_deps(monkeypatch, calls)
    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    graph = FakeGraph(error=RuntimeError("boom"))
    await _run_confirmed_graph(
        task, graph, {"trace_id": "t1"}, "s1", "confirm"
    )
    err = json.loads(task.result[0]["data"])
    assert err["code"] == "INTERNAL_ERROR"
    assert err["recoverable"] is False


# --- _subscribe_events：未完成等信号 / 已完成重放 -----------------------------------


async def test_subscribe_waits_then_replays():
    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    it = _subscribe_events(task, "generating").__aiter__()
    first = await it.__anext__()
    assert first["event"] == "phase"
    assert json.loads(first["data"])["phase"] == "generating"
    # 任务此刻完成——订阅者在 await 中醒来并重放
    registry.complete(task, [
        {"event": "report", "data": "{}"},
        {"event": "done", "data": json.dumps({"final_phase": "report_ready"})},
    ])
    assert (await it.__anext__())["event"] == "report"
    assert (await it.__anext__())["event"] == "done"
    with pytest.raises(StopAsyncIteration):
        await it.__anext__()


async def test_subscribe_late_subscriber_replays():
    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    registry.complete(task, [
        {"event": "report", "data": "{}"},
        {"event": "done", "data": json.dumps({"final_phase": "report_ready"})},
    ])
    events = [e async for e in _subscribe_events(task, "generating")]
    assert [e["event"] for e in events] == ["report", "done"]


# --- _start_confirmed_stream：端到端 + 409 -------------------------------------------


async def test_confirm_stream_end_to_end(monkeypatch):
    calls: list = []
    _patch_deps(monkeypatch, calls)
    resp = _start_confirmed_stream(
        "s10", 1, "confirm", FakeGraph(SUCCESS_RESULT),
        {"trace_id": "t1"}, failed_action="confirm", phase_label="generating",
    )
    events = [e async for e in resp.body_iterator]
    assert [e["event"] for e in events] == ["phase", "report", "done"]
    assert ("s10", "report_ready", None) in calls


async def test_start_confirmed_stream_busy_409():
    started = asyncio.Event()
    release = asyncio.Event()

    async def hanging(t: registry.ConfirmedTask) -> None:
        started.set()
        await release.wait()

    t1 = registry.start_confirmed_task("s9", 1, "confirm", hanging)
    await started.wait()
    with pytest.raises(Exception) as excinfo:
        _start_confirmed_stream(
            "s9", 1, "confirm", FakeGraph(SUCCESS_RESULT),
            {"trace_id": "t1"}, failed_action="confirm", phase_label="generating",
        )
    assert excinfo.value.status_code == 409
    release.set()
    await asyncio.wait_for(t1.events.get(), timeout=1)
    assert t1.finished