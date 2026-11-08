"""进程内后台执行任务注册表。

报告路径（/confirm、/retry、mode=adjust）的 graph 执行从 SSE 响应任务
解耦成独立后台任务：客户端断连只取消 SSE 的 event_generator，后台任务
继续跑到 persist_report 落库——「后台跑完」语义。

每 session 至多一条任务；新任务启动前检查占用，重入抛 BusyError。
完成信号走 `events` 队列（put_nowait(None)），最终事件序列放 `result`，
订阅者据此 yield——本模块不感知 SSE 事件格式（低耦合）。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# 任务完成后保留时长：供断连后迟到的订阅者幂等读取最终事件。
_TASK_TTL = datetime.timedelta(minutes=10)

# 2026-11-08（plan interruption-resilience）：思考链路是流式的，每 350ms 一帧。
# 若订阅者中途断开（断流/刷新/关页面），这些帧没人消费——没有任何上限就会一直堆。
# 实测一轮流式 8–25s ≈ 25–75 帧，600 已是极宽松的安全阀；超过后只停「过程帧」，
# 终态帧（phase/report/error/done）照发，用户看到的结果不受影响。
MAX_STREAM_FRAMES = 600


def _is_running_trace(evt: dict) -> tuple[str, bool]:
    """→ (step, 是否 running 的过程帧)。坏 JSON 一律当作非过程帧。"""
    if evt.get("event") != "trace":
        return "", False
    try:
        payload = json.loads(evt.get("data") or "{}")
    except (TypeError, ValueError):
        return "", False
    if not isinstance(payload, dict):
        return "", False
    step = payload.get("step") or ""
    return (step, payload.get("status") == "running")


class BusyError(Exception):
    """同 session 已有未完成的后台任务。HTTP 层映射 409 SESSION_BUSY。"""


@dataclass
class ConfirmedTask:
    session_id: str
    user_id: int
    kind: str  # "confirm" | "adjust"
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    finished: bool = False
    result: list[dict] | None = None
    # P11：已 publish 的实时事件留档，complete 时并入 result 供迟到订阅者重放。
    # 2026-11-08：同一 step 的 running 过程帧只留最后一帧（见 `publish`），否则流式会有上千帧。
    live: list[dict] = field(default_factory=list)
    # 已发布的**流式过程帧**计数（安全阀用；终态帧不计）
    stream_frames: int = 0
    started_at: datetime.datetime = field(default_factory=datetime.datetime.now)
    task: asyncio.Task | None = None


Runner = Callable[[ConfirmedTask], Awaitable[None]]

_tasks: dict[str, ConfirmedTask] = {}


def start_confirmed_task(
    session_id: str, user_id: int, kind: str, runner: Runner
) -> ConfirmedTask:
    """启动并注册一个后台任务；同 session 已有未完成任务 → BusyError。"""
    _sweep()
    existing = _tasks.get(session_id)
    if existing and not existing.finished:
        raise BusyError(f"session {session_id} already has a running task")
    entry = ConfirmedTask(session_id=session_id, user_id=user_id, kind=kind)
    _tasks[session_id] = entry
    entry.task = asyncio.create_task(_run(entry, runner))
    return entry


def get_confirmed_task(session_id: str) -> ConfirmedTask | None:
    return _tasks.get(session_id)


def publish(entry: ConfirmedTask, evt: dict) -> None:
    """P11：执行中发布实时事件——推给在线订阅者并留档进 live。

    complete 后调用是 no-op：防 finally 竞态污染已冻结的重放快照。

    2026-11-08（plan interruption-resilience）：流式思考帧要**有界**——
    1. `live` 里同一 step 的 running 帧只保留最后一帧（迟到订阅者重放不再吐出上千帧）；
    2. 过程帧总数超过 `MAX_STREAM_FRAMES` 后不再 publish（订阅者可能早就断了），
       终态帧不受影响——图继续跑、结果照常落库与上报。
    """
    if entry.finished:
        return

    step, is_running = _is_running_trace(evt)
    if is_running:
        if entry.stream_frames >= MAX_STREAM_FRAMES:
            logger.warning(
                "stream frames capped at %s (session=%s, step=%s)",
                MAX_STREAM_FRAMES, entry.session_id, step,
            )
            return
        entry.stream_frames += 1

    if step:
        # 同一个 step 在留档里只保留**最新一帧**（流式续帧 / 收尾帧都就地替换）——
        # 迟到订阅者拿到的重放是「每步最终状态」，不会看到上千个中间帧。
        for index in range(len(entry.live) - 1, -1, -1):
            prev_step, _ = _is_running_trace(entry.live[index])
            if prev_step == step:
                entry.live[index] = evt
                break
        else:
            entry.live.append(evt)
    else:
        entry.live.append(evt)

    entry.events.put_nowait(evt)


def complete(entry: ConfirmedTask, result: list[dict] | None) -> None:
    """任务正常结束：final 事件推入队列供在线订阅者收尾，唤醒订阅者。

    重放源 `result` = live 快照 + final 事件——迟到订阅者拿到
    progress 全量 + 终态，顺序与在线订阅者一致。
    """
    final = list(result or [])
    entry.result = [*entry.live, *final]
    entry.finished = True
    for evt in final:
        entry.events.put_nowait(evt)
    entry.events.put_nowait(None)


async def _run(entry: ConfirmedTask, runner: Runner) -> None:
    try:
        await runner(entry)
    except asyncio.CancelledError:
        logger.info("background task cancelled: session=%s", entry.session_id)
        entry.events.put_nowait(None)  # 取消也唤醒订阅者走兜底（result 保持 None）
        raise
    except Exception as exc:  # noqa: BLE001 - 兜底唤醒订阅者，绝不静默挂死
        logger.exception("background task failed: session=%s: %s", entry.session_id, exc)
        entry.events.put_nowait(None)
    finally:
        if not entry.finished:
            # runner 未 complete 也唤醒订阅者（result=None → 订阅者走兜底事件）
            entry.events.put_nowait(None)
        entry.finished = True


def _sweep() -> None:
    """惰性清理：start 时移除已过期（finished 且超 TTL）的条目。"""
    now = datetime.datetime.now()
    stale = [
        sid for sid, t in _tasks.items()
        if t.finished and now - t.started_at > _TASK_TTL
    ]
    for sid in stale:
        _tasks.pop(sid, None)