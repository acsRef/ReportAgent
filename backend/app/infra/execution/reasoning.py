"""推理模型的思维链通道（2026-11-05，用户：「模型的推理链路不能给前端看吗，我说的是推理过程」）。

与 `progress_detail` 的区别：

| 通道 | 内容 | 谁写 |
|---|---|---|
| `progress_detail` | **确定性结论**（意图=report / 行数=53 / 失败原因） | 业务代码 |
| `reasoning`（本模块） | **模型原文思维链**（`reasoning_content`） | LLM Adapter |

为什么要分开：结论是「我们判定的结果」，思维链是「模型自己怎么想的」——
前者可以直接当答案展示，后者只适合看着调试/理解，不能混在一条文案里。

用法：runner 安装 sink；`LLMAdapter.generate_with_reasoning` 把 reasoning 写进
**当前节点**；`ProgressTraceHandler` 收尾时把 reasoning 拼进 success 帧的 `reasoning` 字段，
前端在步骤下方用独立块展示（可折叠）。
"""
from __future__ import annotations

import contextvars
from typing import Optional

# node（langgraph 节点名）→ 该节点调模型时拿到的思维链（保留原文，不再压缩成一句话）
_reasoning_sink: contextvars.ContextVar[Optional[dict[str, str]]] = contextvars.ContextVar(
    "llm_reasoning_sink", default=None,
)
# 当前正在执行的节点名（由 ProgressTraceHandler 在 on_chain_start 时设置）
_current_node: contextvars.ContextVar[str] = contextvars.ContextVar(
    "llm_reasoning_node", default="",
)

_MAX_CHARS = 4000


def install(sink: dict[str, str]) -> contextvars.Token:
    return _reasoning_sink.set(sink)


def reset(token: contextvars.Token) -> None:
    _reasoning_sink.reset(token)


def set_node(node: str) -> None:
    _current_node.set(node or "")


def record(text: str, node: str = "") -> None:
    """记录一次模型调用的思维链（同一节点多次调用按追加合并）。"""
    if not text:
        return
    sink = _reasoning_sink.get()
    if sink is None:
        return
    key = node or _current_node.get()
    if not key:
        return
    previous = sink.get(key, "")
    merged = f"{previous}\n{text}".strip() if previous else text
    sink[key] = merged[:_MAX_CHARS]


def current(node: str) -> str:
    sink = _reasoning_sink.get()
    if sink is None:
        return ""
    return sink.get(node, "")
