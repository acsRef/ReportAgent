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
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# 任务完成后保留时长：供断连后迟到的订阅者幂等读取最终事件。
_TASK_TTL = datetime.timedelta(minutes=10)


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


def complete(entry: ConfirmedTask, result: list[dict]) -> None:
    """任务正常结束：写入最终事件序列并唤醒订阅者。"""
    entry.result = result
    entry.finished = True
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