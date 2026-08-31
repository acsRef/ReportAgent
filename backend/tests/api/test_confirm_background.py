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
    def __init__(self) -> None:
        self.end_calls: list[str] = []

    async def flush(self) -> None:
        pass

    def end(self, status: str) -> None:
        self.end_calls.append(status)


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


def _patch_deps(monkeypatch, calls: list) -> list[FakeTracer]:
    tracers: list[FakeTracer] = []

    def fake_get_tracer(*a, **kw):
        t = FakeTracer()
        tracers.append(t)
        return t

    monkeypatch.setattr("app.main.get_tracer", fake_get_tracer)

    async def fake_update(session_id, phase, failed_action=None):
        calls.append((session_id, phase, failed_action))

    monkeypatch.setattr("app.main.session_manager.update_phase", fake_update)
    return tracers# --- _run_confirmed_graph：后台任务事件 + phase 写入 ---------------------------------


async def test_success_emits_report_and_phase(monkeypatch):
    calls: list = []
    _patch_deps(monkeypatch, calls)
    # mock 真实 DB persist 调用——测试焦点是 _persist_report → SSE verdict，不依赖 PG
    async def fake_persist_error_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_confirmed_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_empty_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_adjust_run(**kwargs):
        return {"version": 2, "parent_version": 1, "title": "报告（调整）"}
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_error_run", fake_persist_error_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_confirmed_run", fake_persist_confirmed_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_empty_run", fake_persist_empty_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_adjust_run", fake_persist_adjust_run)
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
    # mock 真实 DB persist 调用——测试焦点是 _persist_report → SSE verdict，不依赖 PG
    async def fake_persist_error_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_confirmed_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_empty_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_adjust_run(**kwargs):
        return {"version": 2, "parent_version": 1, "title": "报告（调整）"}
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_error_run", fake_persist_error_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_confirmed_run", fake_persist_confirmed_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_empty_run", fake_persist_empty_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_adjust_run", fake_persist_adjust_run)
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
    # mock 真实 DB persist 调用——测试焦点是 _persist_report → SSE verdict，不依赖 PG
    async def fake_persist_error_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_confirmed_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_empty_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_adjust_run(**kwargs):
        return {"version": 2, "parent_version": 1, "title": "报告（调整）"}
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_error_run", fake_persist_error_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_confirmed_run", fake_persist_confirmed_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_empty_run", fake_persist_empty_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_adjust_run", fake_persist_adjust_run)
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
    tracers = _patch_deps(monkeypatch, calls)
    # mock 真实 DB persist 调用——测试焦点是 _persist_report → SSE verdict，不依赖 PG
    async def fake_persist_error_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_confirmed_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_empty_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_adjust_run(**kwargs):
        return {"version": 2, "parent_version": 1, "title": "报告（调整）"}
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_error_run", fake_persist_error_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_confirmed_run", fake_persist_confirmed_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_empty_run", fake_persist_empty_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_adjust_run", fake_persist_adjust_run)
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
    # P9-2：graph 未跑完 → trace 终态必须 FAILED（flush 不改状态，end 才改）
    assert tracers and tracers[0].end_calls == ["FAILED"]


async def test_fast_graph_unaffected_by_timeout_budget(monkeypatch):
    calls: list = []
    tracers = _patch_deps(monkeypatch, calls)
    # mock 真实 DB persist 调用——测试焦点是 _persist_report → SSE verdict，不依赖 PG
    async def fake_persist_error_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_confirmed_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_empty_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_adjust_run(**kwargs):
        return {"version": 2, "parent_version": 1, "title": "报告（调整）"}
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_error_run", fake_persist_error_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_confirmed_run", fake_persist_confirmed_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_empty_run", fake_persist_empty_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_adjust_run", fake_persist_adjust_run)
    monkeypatch.setattr("app.main.MAX_TASK_DURATION", 5)
    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    await _run_confirmed_graph(
        task, FakeGraph(SUCCESS_RESULT), {"trace_id": "t1"}, "s1", "confirm"
    )
    # 未超时路径行为不变：report + done、report_ready
    assert [e["event"] for e in task.result] == ["report", "done"]
    assert ("s1", "report_ready", None) in calls
    # graph 跑完 → trace 终态由 _persist_report end("DONE") 负责，runner 不重复 end
    assert all(t.end_calls == [] for t in tracers)


async def test_generic_exception_classified_via_envelope(monkeypatch):
    """P9：泛化异常出口走 classify_exception——LLM 预算耗尽不再是笼统 INTERNAL。"""
    calls: list = []
    tracers = _patch_deps(monkeypatch, calls)
    # mock 真实 DB persist 调用——测试焦点是 _persist_report → SSE verdict，不依赖 PG
    async def fake_persist_error_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_confirmed_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_empty_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_adjust_run(**kwargs):
        return {"version": 2, "parent_version": 1, "title": "报告（调整）"}
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_error_run", fake_persist_error_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_confirmed_run", fake_persist_confirmed_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_empty_run", fake_persist_empty_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_adjust_run", fake_persist_adjust_run)
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
    # P9-2：异常提前退出 → trace 终态 FAILED
    assert tracers and tracers[0].end_calls == ["FAILED"]


async def test_unknown_exception_falls_to_internal_error(monkeypatch):
    """未知异常兜底 INTERNAL_ERROR / recoverable=False（原 INTERNAL 语义收编为契约码）。"""
    calls: list = []
    _patch_deps(monkeypatch, calls)
    # mock 真实 DB persist 调用——测试焦点是 _persist_report → SSE verdict，不依赖 PG
    async def fake_persist_error_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_confirmed_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_empty_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_adjust_run(**kwargs):
        return {"version": 2, "parent_version": 1, "title": "报告（调整）"}
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_error_run", fake_persist_error_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_confirmed_run", fake_persist_confirmed_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_empty_run", fake_persist_empty_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_adjust_run", fake_persist_adjust_run)
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
    # mock 真实 DB persist 调用——测试焦点是 _persist_report → SSE verdict，不依赖 PG
    async def fake_persist_error_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_confirmed_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_empty_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_adjust_run(**kwargs):
        return {"version": 2, "parent_version": 1, "title": "报告（调整）"}
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_error_run", fake_persist_error_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_confirmed_run", fake_persist_confirmed_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_empty_run", fake_persist_empty_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_adjust_run", fake_persist_adjust_run)
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

# --- P9-5：泛化异常 SSE 文案走 user_message，不再把 provider 原始异常直达用户 ---------


async def test_generic_exception_message_uses_user_copy(monkeypatch):
    calls: list = []
    _patch_deps(monkeypatch, calls)
    # mock 真实 DB persist 调用——测试焦点是 _persist_report → SSE verdict，不依赖 PG
    async def fake_persist_error_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_confirmed_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_empty_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_adjust_run(**kwargs):
        return {"version": 2, "parent_version": 1, "title": "报告（调整）"}
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_error_run", fake_persist_error_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_confirmed_run", fake_persist_confirmed_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_empty_run", fake_persist_empty_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_adjust_run", fake_persist_adjust_run)
    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    graph = FakeGraph(error=RuntimeError("minimax sdk internal trace: cfg dict at 0x..."))
    await _run_confirmed_graph(
        task, graph, {"trace_id": "t1"}, "s1", "confirm"
    )
    err = json.loads(task.result[0]["data"])
    assert err["code"] == "INTERNAL_ERROR"
    from app.reliability.errors import user_message

    assert err["message"] == user_message("other")
    assert "minimax" not in err["message"]


# --- P11 Review-1 P1-1：FAILED verdict 必须由 main.py 走 error SSE，不能被 _persist_report 抹成 report -

# 旧 test_failed_emits_error_and_phase 用 FakeGraph(mock ainvoke) 绕过了 _persist_report——掩盖了
# 「verdict 在 _persist_report return 时被覆盖成 DONE」的真实 graph 链路 bug。两个测试覆盖不同层级：

async def test_persist_report_does_not_overwrite_failed_verdict(monkeypatch):
    """Surgical：直接调 _persist_report 暴露 bug——失败 verdict 不能被 DONE 抹掉。"""
    import app.agent.confirmed_execution_graph as ceg
    from app.agent.confirmed_execution_graph import _persist_report

    async def fake_persist_error_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_release_lock(state):
        return None

    monkeypatch.setattr(
        "app.agent.confirmed_execution_graph.report_version_service.persist_error_run",
        fake_persist_error_run,
    )
    monkeypatch.setattr(ceg, "_release_draft_lock", fake_release_lock)

    state = {
        "session_id": "s1", "user_id": 1, "draft_id": 5,
        "execution_status": "FAILED",
        "error": {"code": "QUERY_FAILED", "message": "boom", "kind": "other"},
        "sql": "SELECT bad",
        "report_payload": {"answer": {"text": ""}},
        "trace_id": "t1",
    }
    result = await _persist_report(state)
    # P11 Review-1 P1-1：_persist_report 不能把 FAILED 抹成 DONE——
    # verdict 由 main.py 读取决定 SSE 出口（FAILED→error，其余→report）。
    assert "execution_status" not in result, (
        f"_persist_report overwrites verdict: got execution_status={result.get('execution_status')!r}"
    )
    # report_payload 仍带 version/title/parent_version（sse-v2 wire 形态，P11 D4）
    merged = result["report_payload"]
    assert merged["version"] == 1
    assert merged["title"] == "报告"


async def test_real_graph_failed_emits_error_not_report(monkeypatch):
    """End-to-end：真实 confirmed graph FAILED verdict 必须经 main.py 发 error SSE，不能是 report。
    需 monkeypatch 真实 graph 中所有 DB/load 节点（security_guard / load / sql_gate / data_agent）
    与 report_version_service persist_*_run——保留真实 _persist_report 作为测试焦点。
    """
    calls: list = []
    _patch_deps(monkeypatch, calls)

    # mock DB persist 调用
    async def fake_persist_error_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_confirmed_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_empty_run(**kwargs):
        return {"version": 1, "parent_version": None, "title": "报告"}
    async def fake_persist_adjust_run(**kwargs):
        return {"version": 2, "parent_version": 1, "title": "报告（调整）"}
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_error_run", fake_persist_error_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_confirmed_run", fake_persist_confirmed_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_empty_run", fake_persist_empty_run)
    monkeypatch.setattr("app.agent.confirmed_execution_graph.report_version_service.persist_adjust_run", fake_persist_adjust_run)

    # monkeypatch DB/load 节点保留真实 _persist_report
    import app.agent.confirmed_execution_graph as ceg
    async def fake_security(state): return {}
    async def fake_load(state): return {}
    async def fake_gate(state): return {"execution_status": "RUNNING"}
    async def fake_data(state): return {"schema_context": None}
    async def fake_sql(state, config=None):
        return {
            "query_result": None,
            "execution_status": "FAILED",
            "error": {"code": "QUERY_FAILED", "message": "boom", "kind": "other"},
            "sql": "SELECT bad",
        }
    async def fake_report(state, config=None):
        return {
            "chart_config": None, "insight": None,
            "execution_status": "FAILED",
            "error": {"code": "QUERY_FAILED", "message": "boom", "kind": "other"},
            "sql": "SELECT bad",
            "report_payload": {"answer": {"text": ""}},
        }
    monkeypatch.setattr(ceg, "_security_guard", fake_security)
    monkeypatch.setattr(ceg, "_load_confirmed_requirement", fake_load)
    monkeypatch.setattr(ceg, "_sql_gate", fake_gate)
    monkeypatch.setattr(ceg, "_confirmed_data_agent", fake_data)
    monkeypatch.setattr(ceg, "_confirmed_sql_agent", fake_sql)
    monkeypatch.setattr(ceg, "_confirmed_report_agent", fake_report)

    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    await _run_confirmed_graph(
        task, ceg.build_confirmed_execution_graph(),
        {"trace_id": "t1", "user_query": "q", "user_id": 1, "session_id": "s1"},
        "s1", "confirm",
    )

    event_types = [e["event"] for e in (task.result or [])]
    assert "report" not in event_types, (
        f"P11 Review-1 P1-1 regression: FAILED verdict leaked as report. events={event_types}"
    )
    assert "error" in event_types
    assert event_types[-1] == "done"
    err = json.loads(next(e for e in task.result if e["event"] == "error")["data"])
    assert err["code"] == "QUERY_FAILED"
    assert ("s1", "error", "sql") in calls
    assert json.loads(task.result[-1]["data"])["final_phase"] == "error"
