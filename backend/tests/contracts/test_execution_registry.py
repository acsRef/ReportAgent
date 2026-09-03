"""ExecutionRegistry 契约测试：启动 / 重入拒绝 / 完成信号 / 覆盖 / 异常兜底。"""
import asyncio

import pytest

from app.infra.execution.registry import (
    BusyError,
    ConfirmedTask,
    complete,
    get_confirmed_task,
    publish,
    start_confirmed_task,
)

DONE = {"event": "done", "data": "{}"}

# P11：publish/live 语义——执行中事件在线推给订阅者，complete 后整体留档重放。
E1 = {"event": "trace", "data": '{"step":"规划查询"}'}
E2 = {"event": "trace", "data": '{"step":"执行查询"}'}


async def _noop_runner(task: ConfirmedTask) -> None:
    complete(task, [DONE])


async def test_start_and_complete():
    task = start_confirmed_task("s1", 1, "confirm", _noop_runner)
    assert get_confirmed_task("s1") is task
    # P11：final 事件先经队列推给在线订阅者，然后是完成哨兵
    assert await asyncio.wait_for(task.events.get(), timeout=1) == DONE
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


async def test_two_concurrent_confirms_one_wins_one_busy():
    """Final Hardening ⑨：两个并发 confirm 同时到达同一 session——恰一个成功、
    另一个 BusyError（409 语义）。deterministic：barrier 保证 A 在途时 B 才到达，
    结果不依赖调度时序。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(task: ConfirmedTask) -> None:
        started.set()
        await release.wait()
        complete(task, [DONE])

    outcomes: list[str] = []

    async def confirm_a():
        t = start_confirmed_task("race-1", 1, "confirm", slow)
        outcomes.append("a:started")
        await asyncio.wait_for(t.events.get(), timeout=1)
        outcomes.append("a:done")

    async def confirm_b():
        # 与 A 同时被调度：A 由 barrier 保持在途，B 的 start 必然落在
        # 「session 忙碌」窗口内 → BusyError（与 API 409 SESSION_BUSY 同源）
        await started.wait()
        try:
            start_confirmed_task("race-1", 1, "confirm", _noop_runner)
            outcomes.append("b:accepted")
        except BusyError:
            outcomes.append("b:busy")
        finally:
            release.set()  # 放行 A 收尾

    await asyncio.gather(confirm_a(), confirm_b())
    assert outcomes == ["a:started", "b:busy", "a:done"], outcomes


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


async def test_publish_streams_to_online_subscriber_before_complete():
    """P11：complete 前 publish 的事件立即可被订阅者消费，无需等任务结束。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(task: ConfirmedTask) -> None:
        started.set()
        publish(task, E1)
        publish(task, E2)
        await release.wait()
        complete(task, [DONE])

    task = start_confirmed_task("p1", 1, "confirm", slow)
    await started.wait()
    # 在线订阅者在 complete 前就能逐条拿到 live 事件
    assert await asyncio.wait_for(task.events.get(), timeout=1) == E1
    assert await asyncio.wait_for(task.events.get(), timeout=1) == E2
    release.set()
    assert await asyncio.wait_for(task.events.get(), timeout=1) == DONE
    assert await asyncio.wait_for(task.events.get(), timeout=1) is None


async def test_complete_replays_live_plus_final():
    """P11：迟到订阅者重放 = live 快照 + final 事件，顺序保持。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(task: ConfirmedTask) -> None:
        started.set()
        publish(task, E1)
        await release.wait()
        complete(task, [DONE])

    task = start_confirmed_task("p2", 1, "confirm", slow)
    await started.wait()
    release.set()
    await asyncio.wait_for(task.events.get(), timeout=1)
    assert task.result == [E1, DONE]


async def test_publish_after_complete_is_ignored():
    """complete 后 publish 不再入队/入档（防 finally 竞态污染重放）。"""
    task = start_confirmed_task("p3", 1, "confirm", _noop_runner)
    assert await asyncio.wait_for(task.events.get(), timeout=1) == DONE
    publish(task, E1)
    assert task.result == [DONE]
    # 队列里只剩完成哨兵，E1 未被入队
    assert await asyncio.wait_for(task.events.get(), timeout=1) is None
    assert task.events.empty()