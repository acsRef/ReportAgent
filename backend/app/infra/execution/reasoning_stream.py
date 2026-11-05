"""思考链路的**流式**通道：模型边想，前端边看（plan 2026-11-05-streaming-reasoning）。

分工与既有 `reasoning` 通道一致——都是 contextvar sink，由 runner 安装：

| 通道 | 内容 | 谁写 |
|---|---|---|
| `reasoning`（已有） | 每个节点的**最终**思维链 | LLM Adapter 收尾时写 |
| `reasoning_stream`（本模块） | 流式过程中的**累计**思维链 | `chat_with_reasoning(on_delta=…)` |

帧形态沿用 P11 契约（不新增 SSE 顶层事件类型）：`trace` + `status=running`，
`step` 固定为「理解意图 · 思考」，`reasoning` 是累计文本——前端按 nodeName 就地更新，
所以看到的是一段**越来越长**的思考链路。

为什么节流：provider 每 2~3 个字符一个 delta（实测 418 个），全发会把 SSE 打爆。
默认 350ms 发一帧；`TURN_REASONING_STREAM=0` 可直接关掉。
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 帧内文本上限（与落库/展示口径一致；完整文本仍在最终 success 帧与 DB）
MAX_FRAME_CHARS = 4000

# 默认节点名（前端把它当成一个「步骤 · 思考」）
DEFAULT_STEP = "理解意图 · 思考"

_sink: contextvars.ContextVar[Optional["_Sink"]] = contextvars.ContextVar(
    "llm_reasoning_stream_sink", default=None,
)


def enabled() -> bool:
    return (os.getenv("TURN_REASONING_STREAM", "1").strip() not in ("0", "false", "no"))


def interval_seconds() -> float:
    try:
        return max(0.05, float(os.getenv("TURN_STREAM_INTERVAL_MS", "350")) / 1000.0)
    except (TypeError, ValueError):
        return 0.35


class _Sink:
    """一个 runner 一个 sink：节流发布 + 计数（便于日志/测试）。"""

    def __init__(self, publish: Callable[[dict], None], step: str) -> None:
        self._publish = publish
        self._step = step
        self._last_emit = 0.0
        self._last_text = ""
        self.frames_emitted = 0

    def emit(self, text: str) -> None:
        if not text or text == self._last_text:
            return
        now = time.monotonic()
        if now - self._last_emit < interval_seconds():
            return
        self._last_emit = now
        self._last_text = text
        self.frames_emitted += 1
        frame = {
            "event": "trace",
            "data": json.dumps(
                {
                    "step": self._step,
                    "status": "running",
                    "detail": "思考中…",
                    "reasoning": text[-MAX_FRAME_CHARS:],
                    "kind": "agent",
                },
                ensure_ascii=False,
            ),
        }
        try:
            self._publish(frame)
        except Exception as exc:  # noqa: BLE001 - 渲染通道绝不能影响主链
            logger.warning("reasoning stream publish failed: %s", exc)


def install(publish: Callable[[dict], None], step: str = DEFAULT_STEP):
    """runner 侧安装（返回 token，交给 `reset`）。"""
    if not enabled():
        return _sink.set(None)
    return _sink.set(_Sink(publish, step))


def reset(token) -> None:
    try:
        _sink.reset(token)
    except ValueError:  # 跨上下文 reset（极少见）——忽略，不要打断收尾
        logger.debug("reasoning stream reset out of context")


def emit(text: str) -> None:
    """模型边想边调：把**累计**思维链交给当前 sink（没安装就静默跳过）。"""
    sink = _sink.get()
    if sink is None or not text:
        return
    sink.emit(text)


def current_frames_emitted() -> int:
    sink = _sink.get()
    return sink.frames_emitted if sink is not None else 0
