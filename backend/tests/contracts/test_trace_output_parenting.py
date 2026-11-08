"""节点输出必须挂在节点自己身上（plan 2026-11-08-trace-inspector）。

`@traced_node` 的 `_handle_output_span` 原来在 `with tracer.span(...)` **外面**调用：
那时 span 栈已空 → 输出 span 的 `parent_span_id` 为 None、名字退化成 `node_output`，
于是「这个节点算出了什么」对不上任何节点（页面上点开是「输出：空」，
一轮 43 个 span 里还有十几个孤儿 `node_output`）。
"""
from __future__ import annotations

import pytest

from app.infra.trace.sdk import Tracer, traced_node

pytestmark = pytest.mark.contracts


@traced_node("my_node")
async def _async_node(state: dict) -> dict:
    return {"answer": 42}


@traced_node("my_node")
def _sync_node(state: dict) -> dict:
    return {"answer": 42}


def _runner(tracer: Tracer):
    """在 tracer 上下文里跑一个节点（traced_node 依赖 trace_id 找 tracer）。"""
    from app.infra.trace import sdk

    token = sdk._current_tracer.set(tracer)
    try:
        yield
    finally:
        sdk._current_tracer.reset(token)


class TestOutputSpanParenting:
    @pytest.mark.parametrize("runner", [_sync_node, _async_node])
    def test_输出挂在节点自己的_span_下(self, runner) -> None:
        from app.infra.trace import sdk

        tracer = Tracer("t-output")
        sdk._local["t-output"] = tracer
        token = sdk._current_tracer.set(tracer)
        try:
            if hasattr(runner, "__wrapped__") and runner is _async_node:
                import asyncio

                asyncio.run(runner({"trace_id": "t-output"}))
            else:
                runner({"trace_id": "t-output"})
        finally:
            sdk._current_tracer.reset(token)

        by_name = {span.span_name: span for span in tracer._spans}
        assert "my_node" in by_name
        assert "my_node_output" in by_name, "输出 span 名字应是 <节点>_output"

        node = by_name["my_node"]
        out = by_name["my_node_output"]
        assert out.parent_span_id == node.span_id, "输出 span 必须挂在节点 span 下"

    def test_不再产生孤儿_node_output(self) -> None:
        from app.infra.trace import sdk

        tracer = Tracer("t-orphan")
        sdk._local["t-orphan"] = tracer
        token = sdk._current_tracer.set(tracer)
        try:
            _sync_node({"trace_id": "t-orphan"})
        finally:
            sdk._current_tracer.reset(token)

        assert not any(span.span_name == "node_output" for span in tracer._spans)
