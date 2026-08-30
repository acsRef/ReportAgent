"""P11 progress 事件：后台 confirmed 图节点生命周期 → SSE trace 帧。

契约（frontend-contract §二）：不新增 SSE 顶层事件类型，progress 族由
`trace` 载荷细化表达——`{step, status, detail, kind}`，前端按
`kind × status` 推导 started/completed/failed。映射表是确定性契约：
不在表内的节点不发事件；`agent.thinking ≈ 现 thinking`、
`report.generated/updated` 由 report 节点 trace + 既有 report 事件覆盖。
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from langchain_core.callbacks import AsyncCallbackHandler

# node name -> (kind, 用户文案)。kind ∈ agent|tool|sql|repair|report。
PROGRESS_NODES: dict[str, tuple[str, str]] = {
    "plan": ("agent", "规划查询"),
    "data_agent": ("tool", "准备分析数据"),
    "sql_agent": ("agent", "执行 SQL 分析"),
    "generate_sql": ("sql", "生成 SQL"),
    "validate": ("sql", "校验 SQL"),
    "execute": ("sql", "执行查询"),
    "evaluate": ("agent", "评估结果"),
    "diagnose": ("repair", "诊断修复"),
    "report_agent": ("report", "生成报告"),
    "plan_analysis": ("report", "组织报告结构"),
    "run_step": ("report", "撰写报告内容"),
    "build_output": ("report", "汇总报告"),
}

# langchain 内部包装器名——不是图节点，不发事件（沿 legacy _format_event 名单）。
_NOISE_NAMES = frozenset({
    "LangGraph",
    "LangGraphRunnableSequence",
    "LangGraphRunnableGraph",
    "RunnableSequence",
    "RunnableParallel",
    "RunnableLambda",
    "RunnablePassthrough",
})


def format_progress_frame(node: str, status: str) -> Optional[dict]:
    """node + 生命周期状态 → SSE trace 帧；未映射节点返回 None。"""
    hit = PROGRESS_NODES.get(node)
    if hit is None:
        return None
    kind, label = hit
    return {
        "event": "trace",
        "data": json.dumps(
            {"step": label, "status": status, "detail": "", "kind": kind},
            ensure_ascii=False,
        ),
    }


class ProgressTraceHandler(AsyncCallbackHandler):
    """langgraph 回调 → publish trace 帧。

    同一 node 的同名状态重复触发去重（`run_step` 循环 / 节点内部 Runnable
    序列继承 `langgraph_node` metadata 都会再触发）——只在状态跃迁时发帧。
    """

    def __init__(self, on_frame: Callable[[dict], None]) -> None:
        self._on_frame = on_frame
        self._last: dict[str, str] = {}

    def _emit(self, node: str, name: Optional[str], status: str) -> None:
        if name in _NOISE_NAMES:
            return
        if self._last.get(node) == status:
            return
        self._last[node] = status
        frame = format_progress_frame(node, status)
        if frame is not None:
            self._on_frame(frame)

    async def on_chain_start(
        self, serialized, inputs, *, run_id=None, parent_run_id=None,
        tags=None, metadata=None, name=None, **kwargs,
    ) -> None:
        node = (metadata or {}).get("langgraph_node") or (name or "")
        self._emit(node, name, "running")

    async def on_chain_end(
        self, outputs, *, run_id=None, parent_run_id=None,
        tags=None, metadata=None, name=None, **kwargs,
    ) -> None:
        node = (metadata or {}).get("langgraph_node") or (name or "")
        self._emit(node, name, "success")

    async def on_chain_error(
        self, error, *, run_id=None, parent_run_id=None,
        tags=None, metadata=None, name=None, **kwargs,
    ) -> None:
        node = (metadata or {}).get("langgraph_node") or (name or "")
        self._emit(node, name, "error")
