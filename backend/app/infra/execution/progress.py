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
    # ── confirmed-execution 图 ────────────────────────────────────────────
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
    # ── requirement-analysis 图（2026-11-04：需求轮也要能看见「在想什么」）──
    "security_guard": ("agent", "安全检查"),
    "classify_intent": ("agent", "识别意图"),
    # 2026-11-05（plan turn-understanding-agent）：强模型理解 → 确定性归并 → 可报告性裁决
    "turn_understand": ("agent", "理解意图"),
    "task_reduce": ("agent", "归并任务"),
    "reportability": ("agent", "可报告性"),
    "requirement_project": ("agent", "投影需求卡"),
    "requirement_parse": ("agent", "解析需求"),
    "requirement_preview": ("tool", "口径预览"),
    "persist_draft": ("agent", "保存需求卡"),
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


def format_progress_frame(
    node: str, status: str, detail_text: str = "",
) -> Optional[dict]:
    """node + 生命周期状态 → SSE trace 帧；未映射节点返回 None。

    `detail_text` = 这一步的**结论**（P1-②/2026-11-04 用户要求「思考的内容也要输出」）：
    意图、口径、行数、失败原因这类确定性短句，由 `progress_detail.detail()` 写入。
    """
    hit = PROGRESS_NODES.get(node)
    if hit is None:
        return None
    kind, label = hit
    return {
        "event": "trace",
        "data": json.dumps(
            {"step": label, "status": status, "detail": detail_text, "kind": kind},
            ensure_ascii=False,
        ),
    }


class ProgressTraceHandler(AsyncCallbackHandler):
    """langgraph 回调 → publish trace 帧。

    同一 node 的同名状态重复触发去重（`run_step` 循环 / 节点内部 Runnable
    序列继承 `langgraph_node` metadata 都会再触发）——只在状态跃迁时发帧。

    ## 为什么 success 不能靠 `on_chain_end`（2026-11-04 修正）

    实测本仓库使用的 langgraph 版本里，节点的 `on_chain_end` **既没有
    `metadata.langgraph_node`、也没有 `name`**（只有 graph 层的 START/END 带名字）：

        START 'LangGraph' node=None
        START 'security_guard' node=security_guard
        END   None node=None          ← 归因不了，旧实现直接丢弃

    于是 progress 帧历史上**只发过 running、从没发过 success**（P11 契约写的是
    「started/completed」，实现只落了 started）。现在改成两条确定性规则：

    1. **线性图假设**：新节点开始时，把此前未收尾的节点标 success
       （我们的两张图都是线性/条件链，没有并行分叉）；
    2. 图跑完时由 runner 调 `close_all()` 收尾最后一个节点；失败路径调
       `fail_pending()` 标 error——绝不留下永远 ◐ 的步骤。
    """

    def __init__(self, on_frame: Callable[[dict], None]) -> None:
        self._on_frame = on_frame
        self._last: dict[str, str] = {}
        self._pending: list[str] = []

    def _emit(self, node: str, name: Optional[str], status: str) -> None:
        if name in _NOISE_NAMES:
            return
        if self._last.get(node) == status:
            return
        self._last[node] = status
        # 只有「收尾」帧带内容（running 时节点还没有结论）
        detail_text = ""
        if status in ("success", "error"):
            from app.infra.execution import progress_detail

            label = PROGRESS_NODES.get(node, ("", ""))[1]
            detail_text = progress_detail.current(label)
        frame = format_progress_frame(node, status, detail_text)
        if frame is not None:
            self._on_frame(frame)

    def _close_pending(self, except_node: Optional[str] = None) -> None:
        """把挂起的节点收成 success（保留 `except_node`，它正在开始）。

        `except_node` 的存在是因为：同一节点的内部 Runnable 会**再次**继承
        `langgraph_node` 触发一次 start——若把它自己也收掉，就会 running/success
        反复横跳（实测一次节点产生 6 帧）。
        """
        keep: list[str] = []
        for node in self._pending:
            if node == except_node:
                keep.append(node)
                continue
            self._emit(node, node, "success")
        self._pending = keep

    def close_all(self) -> None:
        """图正常跑完：收尾最后一个节点（runner 在 complete 前调用）。"""
        self._close_pending()

    def fail_pending(self) -> None:
        """图异常退出：挂起节点标 error（runner 在异常路径调用）。"""
        pending, self._pending = self._pending, []
        for node in pending:
            self._emit(node, node, "error")

    async def on_chain_start(
        self, serialized, inputs, *, run_id=None, parent_run_id=None,
        tags=None, metadata=None, name=None, **kwargs,
    ) -> None:
        node = (metadata or {}).get("langgraph_node") or (name or "")
        if node in PROGRESS_NODES:
            # 线性图：新节点已开始 ⇒ 之前的节点必然结束（end 事件归因不了）
            self._close_pending(except_node=node)
            if node not in self._pending:
                self._pending.append(node)
        self._emit(node, name, "running")

    async def on_chain_end(
        self, outputs, *, run_id=None, parent_run_id=None,
        tags=None, metadata=None, name=None, **kwargs,
    ) -> None:
        node = (metadata or {}).get("langgraph_node") or (name or "")
        if node in PROGRESS_NODES and node in self._pending:
            self._pending.remove(node)
            self._emit(node, name, "success")

    async def on_chain_error(
        self, error, *, run_id=None, parent_run_id=None,
        tags=None, metadata=None, name=None, **kwargs,
    ) -> None:
        node = (metadata or {}).get("langgraph_node") or (name or "")
        if node in PROGRESS_NODES and node in self._pending:
            self._pending.remove(node)
        self._emit(node, name, "error")
        self.fail_pending()
