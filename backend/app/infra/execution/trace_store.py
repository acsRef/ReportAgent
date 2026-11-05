"""思考链路落库（plan `2026-11-05-trace-persistence.md`）。

前端把「已思考（用时 N 秒）」展开后读的是**这一轮的 trace 帧**（步骤 + 结论 + 模型原文）。
此前这些帧只经 SSE 送到当前页面，进程里不留、库里不存 → F5 之后历史轮次点开是空的。

这里把它落到 `app.conversations`（`message_type='turn_trace'`，content=NULL，
metadata=`{entries, duration_ms, status}`）——会话账本本来就该记得「助手这一轮做了什么」，
不额外建表、不加迁移。

约定：
- 帧形状沿用 SSE trace payload（`step/status/detail/reasoning/kind`），
  入库时只把 `step` 改名为前端 `TimelineEntry.nodeName`，并补 `ts`；
- 同一 nodeName 只留一条（后到覆盖，时间戳保留第一次看到的时刻，耗时按开始算）；
- 落库失败只 warning：trace 是观测数据，不能影响主链。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

MESSAGE_TYPE = "turn_trace"

# 模型原文上限（与 SSE 帧同一口径，见 requirement_analysis_graph.turn_reasoning）
MAX_REASONING_CHARS = 4000

_VALID_STATUS = frozenset({"running", "success", "error", "pending"})


def collect_frame(frame: dict | None, ts: int | None = None) -> dict | None:
    """SSE 帧（`{"event": "trace", "data": "…json…"}`）→ 待落库的帧 dict。

    非 trace 帧、坏 JSON、缺字段一律返回 None（宁可少一条，不要写脏数据）。
    `ts` 是帧到达时间（毫秒），默认取当前时间。
    """
    if not isinstance(frame, dict) or frame.get("event") != "trace":
        return None
    raw = frame.get("data")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    step = payload.get("step")
    if not isinstance(step, str) or not step:
        return None
    collected = dict(payload)
    collected["ts"] = int(ts if ts is not None else time.time() * 1000)
    return collected


def build_trace_metadata(frames: list[dict] | None, status: str = "done") -> dict | None:
    """帧列表 → 落库 metadata；没有可用帧返回 None（不写空行）。"""
    entries: list[dict[str, Any]] = []
    index: dict[str, int] = {}

    for frame in frames or []:
        if not isinstance(frame, dict):
            continue
        step = frame.get("step")
        frame_status = frame.get("status")
        if not isinstance(step, str) or not step or frame_status not in _VALID_STATUS:
            continue
        ts = frame.get("ts")
        try:
            ts_value = int(ts) if ts is not None else int(time.time() * 1000)
        except (TypeError, ValueError):
            ts_value = int(time.time() * 1000)

        entry: dict[str, Any] = {
            "nodeName": step,
            "status": frame_status,
            "detail": str(frame.get("detail") or ""),
            "ts": ts_value,
        }
        kind = frame.get("kind")
        if isinstance(kind, str) and kind:
            entry["kind"] = kind
        reasoning = frame.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            entry["reasoning"] = reasoning[:MAX_REASONING_CHARS]

        if step in index:
            previous = entries[index[step]]
            # 状态/结论用最新一帧，时间仍按「这一步第一次出现」算（耗时才是真的）
            entry["ts"] = previous["ts"]
            entries[index[step]] = entry
        else:
            index[step] = len(entries)
            entries.append(entry)

    if not entries:
        return None

    duration_ms = max(0, int(entries[-1]["ts"]) - int(entries[0]["ts"]))
    return {
        "entries": entries,
        "duration_ms": duration_ms,
        "status": status,
    }


async def save_turn_trace(
    *,
    session_id: str,
    user_id: int,
    frames: list[dict] | None,
    status: str = "done",
) -> None:
    """把这一轮的 trace 落一行到 `app.conversations`；失败只记日志。"""
    metadata = build_trace_metadata(frames, status)
    if metadata is None:
        return
    try:
        from app.infra.conversation.repository import save_message

        await save_message(
            session_id,
            int(user_id or 0),
            "assistant",
            None,
            MESSAGE_TYPE,
            metadata,
        )
    except Exception as exc:  # noqa: BLE001 - 观测数据落库失败不得影响主链
        logger.warning("save turn_trace failed: session=%s err=%s", session_id, exc)
