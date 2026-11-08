"""输出过程中的中断：缓冲有界 + 迟到订阅者可重放（plan 2026-11-08-interruption-resilience）。

这里只钉 `ExecutionRegistry` 这一层——它是「SSE 断连 ≠ 任务失败」的唯一依据：
- 订阅者断流后，帧还会继续 publish（后台照跑），所以缓冲必须有界；
- 迟到订阅者拿到的重放序列必须是「过程帧（每步一帧）+ 全部终态帧」，顺序稳定；
- 任务异常/取消都要唤醒订阅者，绝不静默挂死。

真实链路的断流/F5/关页面由 `.e2elogs/probe_interrupt_*.mjs` 覆盖（单独跑）。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.infra.execution import registry

pytestmark = pytest.mark.contracts


def _trace(step: str, status: str = "running", reasoning: str = "") -> dict:
    payload = {"step": step, "status": status, "detail": "", "kind": "agent"}
    if reasoning:
        payload["reasoning"] = reasoning
    return {"event": "trace", "data": json.dumps(payload, ensure_ascii=False)}


def _phase(phase: str) -> dict:
    return {"event": "phase", "data": json.dumps({"phase": phase})}


def _task() -> registry.ConfirmedTask:
    return registry.ConfirmedTask(session_id="s-1", user_id=1, kind="confirm")


def _trace_steps(events: list[dict]) -> list[tuple[str, str]]:
    out = []
    for evt in events:
        if evt.get("event") != "trace":
            continue
        payload = json.loads(evt["data"])
        out.append((payload.get("step", ""), payload.get("status", "")))
    return out


class TestLiveBufferIsBounded:
    def test_同一节点的流式续帧只留最后一帧(self) -> None:
        entry = _task()
        for i in range(20):
            registry.publish(entry, _trace("理解意图 · 思考", "running", "x" * (i + 1)))

        live = [e for e in entry.live if e.get("event") == "trace"]
        assert len(live) == 1
        assert json.loads(live[0]["data"])["reasoning"] == "x" * 20

    def test_不同节点各自保留一帧(self) -> None:
        entry = _task()
        for step in ("理解意图 · 思考", "解析需求"):
            for _ in range(5):
                registry.publish(entry, _trace(step, "running"))

        assert {step for step, _ in _trace_steps(entry.live)} == {"解析需求", "理解意图 · 思考"}
        assert len(_trace_steps(entry.live)) == 2

    def test_终态帧就地替换该步的过程帧(self) -> None:
        entry = _task()
        registry.publish(entry, _trace("理解意图 · 思考", "running", "半句"))
        registry.publish(entry, _trace("理解意图 · 思考", "success", "整段"))

        live_traces = [e for e in entry.live if e.get("event") == "trace"]
        assert _trace_steps(live_traces) == [("理解意图 · 思考", "success")]
        assert json.loads(live_traces[0]["data"])["reasoning"] == "整段"

    def test_流式帧总量有上限_终态不受影响(self) -> None:
        entry = _task()
        limit = registry.MAX_STREAM_FRAMES
        for _ in range(limit + 50):
            registry.publish(entry, _trace("理解意图 · 思考", "running"))
        registry.publish(entry, _trace("理解意图 · 思考", "success"))
        registry.publish(entry, _phase("awaiting_confirm"))

        assert entry.stream_frames == limit
        events = list(entry.events._queue)  # noqa: SLF001 - 断言队列长度用
        assert len([e for e in events if e.get("event") == "trace"]) == limit + 1  # +1 = success 帧
        assert events[-1] == _phase("awaiting_confirm")


class TestLateSubscriberReplay:
    def test_重放等于过程帧加终态帧(self) -> None:
        entry = _task()
        registry.publish(entry, _phase("parsing"))
        for _ in range(10):
            registry.publish(entry, _trace("理解意图 · 思考", "running"))
        registry.publish(entry, _trace("理解意图 · 思考", "success", "整段"))
        registry.complete(entry, [_phase("awaiting_confirm"), {"event": "done", "data": "{}"}])

        assert entry.finished is True
        assert entry.result is not None
        assert _trace_steps(entry.result) == [("理解意图 · 思考", "success")]
        assert [e["event"] for e in entry.result] == ["phase", "trace", "phase", "done"]

    def test_complete_之后_publish_不再进缓冲(self) -> None:
        entry = _task()
        registry.complete(entry, [])
        before = len(entry.live)
        registry.publish(entry, _trace("迟到的帧"))

        assert len(entry.live) == before
        assert _trace_steps(entry.live) == []


class TestSubscriberAlwaysWakesUp:
    def test_正常完成会推哨兵(self) -> None:
        entry = _task()
        registry.publish(entry, _phase("parsing"))
        registry.complete(entry, [{"event": "done", "data": "{}"}])

        events = list(entry.events._queue)  # noqa: SLF001
        assert events[-1] is None  # 订阅者据此结束 drain

    def test_runner_异常也唤醒且标记完成(self) -> None:
        async def boom(_entry: registry.ConfirmedTask) -> None:
            raise RuntimeError("graph blew up")

        entry = _task()
        asyncio.run(registry._run(entry, boom))  # noqa: SLF001 - 直接驱动内部 runner

        assert entry.finished is True
        assert entry.result is None  # 订阅者据此走兜底事件
        assert list(entry.events._queue)[-1] is None  # noqa: SLF001

    def test_runner_被取消也唤醒(self) -> None:
        async def hang(_entry: registry.ConfirmedTask) -> None:
            await asyncio.sleep(10)

        async def scenario() -> registry.ConfirmedTask:
            entry = _task()
            task = asyncio.create_task(registry._run(entry, hang))  # noqa: SLF001
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return entry

        entry = asyncio.run(scenario())
        assert entry.finished is True
        assert list(entry.events._queue)[-1] is None  # noqa: SLF001

    def test_同会话重入抛_Busy(self) -> None:
        async def hang(_entry: registry.ConfirmedTask) -> None:
            await asyncio.sleep(5)

        async def scenario() -> None:
            registry._tasks.pop("busy-session", None)  # noqa: SLF001
            first = registry.start_confirmed_task("busy-session", 1, "confirm", hang)
            try:
                with pytest.raises(registry.BusyError):
                    registry.start_confirmed_task("busy-session", 1, "confirm", hang)
            finally:
                first.task.cancel()  # type: ignore[union-attr]
                with pytest.raises(asyncio.CancelledError):
                    await first.task  # type: ignore[arg-type]
                registry._tasks.pop("busy-session", None)  # noqa: SLF001

        asyncio.run(scenario())
