"""P11 progress 事件：映射表 / handler 去重 / callbacks 转发 / runner 装配。

format_progress_frame 与 ProgressTraceHandler 是确定性契约（node → kind/文案），
接线在 _run_confirmed_graph（config.callbacks）——CallbackFiringGraph 模拟
langchain 回调触发验证端到端帧序列，不依赖真实 DB / LLM。
"""
import json

import pytest

from app.infra.execution.progress import (
    ProgressTraceHandler,
    format_progress_frame,
)
from app.main import _run_confirmed_graph
from app.infra.execution import registry

pytestmark = pytest.mark.api


# --- format_progress_frame：确定性映射表 ---------------------------------------------


def test_format_known_node_running():
    frame = format_progress_frame("generate_sql", "running")
    assert frame is not None
    assert frame["event"] == "trace"
    data = json.loads(frame["data"])
    assert data == {"step": "生成 SQL", "status": "running", "detail": "", "kind": "sql"}


def test_format_unknown_node_returns_none():
    assert format_progress_frame("sql_gate", "running") is None
    assert format_progress_frame("security_guard", "success") is None


def test_format_all_mapped_kinds():
    assert json.loads(format_progress_frame("plan", "running")["data"])["kind"] == "agent"
    assert json.loads(format_progress_frame("data_agent", "running")["data"])["kind"] == "tool"
    assert json.loads(format_progress_frame("diagnose", "running")["data"])["kind"] == "repair"
    assert json.loads(format_progress_frame("run_step", "running")["data"])["kind"] == "report"


# --- ProgressTraceHandler：噪声过滤 + 状态跃迁去重 -----------------------------------


async def _fire(handler, node, name, method, error=None):
    """按 langchain 回调协议签名触发（on_chain_start 收 2 位置参，end/error 收 1）。"""
    meta = {"langgraph_node": node} if node else {}
    if method == "on_chain_start":
        await handler.on_chain_start(None, {}, name=name, metadata=meta)
    elif method == "on_chain_end":
        await handler.on_chain_end({}, name=name, metadata=meta)
    else:
        await handler.on_chain_error(error or RuntimeError("x"), name=name, metadata=meta)


async def test_handler_start_then_end_emits_two_frames():
    seen: list[dict] = []
    h = ProgressTraceHandler(on_frame=seen.append)
    await _fire(h, "execute", "execute", "on_chain_start")
    await _fire(h, "execute", "execute", "on_chain_end")
    statuses = [json.loads(f["data"])["status"] for f in seen]
    assert statuses == ["running", "success"]


async def test_handler_dedupes_same_state_and_noise_names():
    seen: list[dict] = []
    h = ProgressTraceHandler(on_frame=seen.append)
    await _fire(h, "execute", "execute", "on_chain_start")
    # 同 node 的内部 Runnable 序列再触发同态 start → 去重
    await _fire(h, "execute", "RunnableSeq", "on_chain_start")
    await _fire(h, "execute", "execute", "on_chain_start")
    # 同态 end 重复 → 去重
    await _fire(h, "execute", "execute", "on_chain_end")
    await _fire(h, "execute", "execute", "on_chain_end")
    statuses = [json.loads(f["data"])["status"] for f in seen]
    assert statuses == ["running", "success"]


async def test_handler_error_frame():
    seen: list[dict] = []
    h = ProgressTraceHandler(on_frame=seen.append)
    await _fire(h, "diagnose", "diagnose", "on_chain_start")
    await _fire(h, "diagnose", "diagnose", "on_chain_error")
    statuses = [json.loads(f["data"])["status"] for f in seen]
    assert statuses == ["running", "error"]


async def test_handler_ignores_unmapped_node():
    seen: list[dict] = []
    h = ProgressTraceHandler(on_frame=seen.append)
    await _fire(h, "sql_gate", "sql_gate", "on_chain_start")
    await _fire(h, "sql_gate", "sql_gate", "on_chain_end")
    assert seen == []


# --- _run_confirmed_graph 装配：config.callbacks → live trace 帧 --------------------


class CallbackFiringGraph:
    """ainvoke 时手动触发 config 里的 handler——模拟 langgraph 节点回调。"""

    def __init__(self, result: dict):
        self._result = result
        self.seen_config: dict | None = None

    async def ainvoke(self, initial: dict, config: dict) -> dict:
        self.seen_config = config
        for cb in (config or {}).get("callbacks") or []:
            await cb.on_chain_start(None, {}, name="generate_sql", metadata={"langgraph_node": "generate_sql"})
            await cb.on_chain_end({}, name="generate_sql", metadata={"langgraph_node": "generate_sql"})
        return self._result


SUCCESS_RESULT = {
    "execution_status": "SUCCESS",
    "report_payload": {"answer": {"text": "ok"}, "execution_status": "SUCCESS"},
    "error": None,
    "sql": "SELECT 1",
}


async def test_runner_wires_handler_progress_frames(monkeypatch):
    """装配契约：graph 执行期 publish 的 trace 帧出现在 report 之前（在线可见）。"""
    from tests.api.test_confirm_background import _patch_deps

    calls: list = []
    _patch_deps(monkeypatch, calls)
    task = registry.ConfirmedTask(session_id="s1", user_id=1, kind="confirm")
    graph = CallbackFiringGraph(SUCCESS_RESULT)
    await _run_confirmed_graph(task, graph, {"trace_id": "t1"}, "s1", "confirm")
    assert graph.seen_config is not None
    assert len(graph.seen_config.get("callbacks") or []) == 1
    events = [e["event"] for e in task.result]
    assert events == ["trace", "trace", "report", "done"]
    first = json.loads(task.result[0]["data"])
    assert first["kind"] == "sql"
    assert first["status"] == "running"
