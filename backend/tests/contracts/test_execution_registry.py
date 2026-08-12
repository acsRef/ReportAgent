"""ExecutionRegistry 契约测试：启动 / 重入拒绝 / 完成信号 / 覆盖 / 异常兜底。"""
import asyncio

import pytest

from app.infra.execution.registry import (
    BusyError,
    ConfirmedTask,
    complete,
    get_confirmed_task,
    start_confirmed_task,
)

DONE = {"event": "done", "data": "{}"}


async def _noop_runner(task: ConfirmedTask) -> None:
    complete(task, [DONE])


async def test_start_and_complete():
    task = start_confirmed_task("s1", 1, "confirm", _noop_runner)
    assert get_confirmed_task("s1") is task
    # 完成信号到达队列
    assert await asyncio.wait_for(task.events.get(), timeout=1) is None
    assert task.finished
    assert task.result == [DONE]


async def test_busy_rejects_second_task():
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(task: ConfirmedTask) -> None:
        started.set()
        await release.wait()
        complete(task, [DONE])

    t1 = start_confirmed_task("s2", 1, "confirm", slow)
    await started.wait()
    with pytest.raises(BusyError):
        start_confirmed_task("s2", 1, "adjust", _noop_runner)
    release.set()
    await asyncio.wait_for(t1.events.get(), timeout=1)
    assert t1.finished


async def test_finished_task_can_be_replaced():
    t1 = start_confirmed_task("s3", 1, "confirm", _noop_runner)
    await asyncio.wait_for(t1.events.get(), timeout=1)
    t2 = start_confirmed_task("s3", 1, "confirm", _noop_runner)
    assert get_confirmed_task("s3") is t2
    await asyncio.wait_for(t2.events.get(), timeout=1)
    assert t2.finished


async def test_runner_exception_wakes_subscriber():
    async def boom(task: ConfirmedTask) -> None:
        raise RuntimeError("boom")

    task = start_confirmed_task("s4", 1, "confirm", boom)
    # 异常不静默：订阅者仍收到唤醒信号
    assert await asyncio.wait_for(task.events.get(), timeout=1) is None
    assert task.finished
    assert task.result is None


async def test_runner_cancellation_wakes_subscriber():
    started = asyncio.Event()

    async def hanging(task: ConfirmedTask) -> None:
        started.set()
        await asyncio.Event().wait()  # 永不返回

    task = start_confirmed_task("s5", 1, "confirm", hanging)
    await started.wait()
    assert task.task is not None
    task.task.cancel()
    # 取消后订阅者仍被唤醒（不静默挂死）
    assert await asyncio.wait_for(task.events.get(), timeout=1) is None
    assert task.finished