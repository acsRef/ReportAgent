"""执行过程帧的「内容」（plan 2026-11-04-trace-visible-reasoning 续）。

## 为什么需要

`trace` 帧一开始只带步骤名（`解析需求`）——用户看得到「在做这一步」，看不到
**这一步得到了什么**。用户明确要求「思考的内容也要输出」，所以这里给节点一个
极轻的写入口：

```python
detail("解析需求", "时间 2024年 · 口径 销售额（用户点名） · 待确认 1 项")
```

`ProgressTraceHandler` 在该节点收尾时把 detail 拼进 success 帧的 `detail` 字段。

## 设计边界

- **只放确定性结论**（意图、口径、行数、失败原因等），不放 prompt / SQL 全文 / 模型散文；
- 通过 `contextvars` 传递，`asyncio.create_task` 自动继承 → 后台任务里的节点同样写入；
- 没有安装 sink 时是 no-op（单测直调节点不会炸）。
"""
from __future__ import annotations

import contextvars
from typing import Optional

# node（中文标签）→ 这一步的结论。ProgressTraceHandler 收尾时读取。
_detail_sink: contextvars.ContextVar[Optional[dict[str, str]]] = contextvars.ContextVar(
    "progress_detail_sink", default=None,
)


def install(sink: dict[str, str]) -> contextvars.Token:
    """安装一个 detail 收集器（runner 在 ainvoke 前调用）。"""
    return _detail_sink.set(sink)


def reset(token: contextvars.Token) -> None:
    _detail_sink.reset(token)


def detail(node_label: str, text: str) -> None:
    """记录某一步的结论（幂等：同一节点多次写入以最后一次为准）。"""
    sink = _detail_sink.get()
    if sink is None or not text:
        return
    sink[node_label] = " ".join(str(text).split())[:160]


def current(node_label: str) -> str:
    sink = _detail_sink.get()
    if sink is None:
        return ""
    return sink.get(node_label, "")
