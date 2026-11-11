"""失败可恢复性的单元契约（plan 2026-11-11-failure-recovery-durability P2/P3）。

两个容易写错、错了却「看起来正常」的点：

1. **自动重试的准入**：只有可恢复失败才排；预算用尽不排；不可恢复（permission /
   timeout）硬重试只是白烧一次钱。
2. **增量落盘的游标**：`save_span` 是纯 INSERT（无 ON CONFLICT），游标写错就意味着
   同一行插两次、或者该写的没写——所以这里直接数 save_span 的调用次数。
"""
from __future__ import annotations

import asyncio

import pytest

from app import main as main_module
from app.infra.trace import sdk as sdk_module
from app.infra.trace.sdk import Tracer

pytestmark = pytest.mark.contracts


# ── P2：自动重试 ────────────────────────────────────────────────────────────


def _error_event(**payload) -> dict:
    import json

    return {"event": "error", "data": json.dumps(payload, ensure_ascii=False)}


def test_last_error_payload_reads_recoverable() -> None:
    events = [
        {"event": "trace", "data": "{}"},
        _error_event(code="QUERY_TIMEOUT", kind="timeout", recoverable=True),
        {"event": "done", "data": "{}"},
    ]

    payload = main_module._last_error_payload(events)

    assert payload["recoverable"] is True
    assert payload["kind"] == "timeout"


def test_last_error_payload_empty_when_no_error() -> None:
    assert main_module._last_error_payload([{"event": "done", "data": "{}"}]) == {}


def test_spawn_skips_non_recoverable(monkeypatch) -> None:
    """permission 这类不可恢复失败不该自动重试——重跑一次还是同样的失败。"""
    created: list[str] = []

    def fake_create_task(coro, name=None):
        created.append(name or "")
        coro.close()
        return None

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    events = [_error_event(kind="permission", recoverable=False)]
    main_module._spawn_auto_retry("s1", 1, {"draft_id": 9}, events)

    assert created == []


def test_spawn_skips_when_budget_zero(monkeypatch) -> None:
    created: list[str] = []
    monkeypatch.setenv("AUTO_RETRY_MAX", "0")
    monkeypatch.setattr(
        asyncio, "create_task",
        lambda coro, name=None: (created.append(name), coro.close())[1],
    )
    events = [_error_event(kind="timeout", recoverable=True)]

    main_module._spawn_auto_retry("s1", 1, {"draft_id": 9}, events)

    assert created == []


def test_auto_retry_records_then_starts(monkeypatch) -> None:
    """预算够 → 先记账（budget_key=auto）再起任务；次数用尽 → 一步都不走。"""
    from app.infra.db import retry_attempt_repository

    recorded: list[dict] = []
    started: list[str] = []

    async def fake_count(session_id, draft_id):
        return 0

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setenv("AUTO_RETRY_BACKOFF_S", "0")
    monkeypatch.setattr(retry_attempt_repository, "count_auto", fake_count)
    monkeypatch.setattr(retry_attempt_repository, "record", fake_record)
    monkeypatch.setattr(
        main_module.registry, "get_confirmed_task", lambda sid: None,
    )
    monkeypatch.setattr(
        main_module.registry, "start_confirmed_task",
        lambda sid, uid, kind, runner: started.append(sid),
    )
    monkeypatch.setattr(main_module, "build_confirmed_execution_graph", lambda: object())

    asyncio.run(main_module._auto_retry_later("s1", 1, 9, "timeout", max_auto=1))

    assert recorded and recorded[0]["budget_key"] == "auto"
    assert recorded[0]["action"] == "auto_retry"
    assert started == ["s1"]


def test_auto_retry_stops_when_budget_exhausted(monkeypatch) -> None:
    from app.infra.db import retry_attempt_repository

    recorded: list[dict] = []
    started: list[str] = []

    async def fake_count(session_id, draft_id):
        return 1  # 已经用掉 1 次

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(retry_attempt_repository, "count_auto", fake_count)
    monkeypatch.setattr(retry_attempt_repository, "record", fake_record)
    monkeypatch.setattr(
        main_module.registry, "start_confirmed_task",
        lambda *a, **k: started.append("x"),
    )

    asyncio.run(main_module._auto_retry_later("s1", 1, 9, "timeout", max_auto=1))

    assert recorded == []
    assert started == []


def test_auto_retry_yields_to_running_task(monkeypatch) -> None:
    """延迟期间用户自己点了重试 → 自动重试让位（不叠加第二次执行）。"""
    from app.infra.db import retry_attempt_repository

    started: list[str] = []

    class _Running:
        finished = False

    async def fake_count(session_id, draft_id):
        return 0

    async def fake_record(**kwargs):
        return None

    monkeypatch.setenv("AUTO_RETRY_BACKOFF_S", "0")
    monkeypatch.setattr(retry_attempt_repository, "count_auto", fake_count)
    monkeypatch.setattr(retry_attempt_repository, "record", fake_record)
    monkeypatch.setattr(
        main_module.registry, "get_confirmed_task", lambda sid: _Running(),
    )
    monkeypatch.setattr(
        main_module.registry, "start_confirmed_task",
        lambda *a, **k: started.append("x"),
    )

    asyncio.run(main_module._auto_retry_later("s1", 1, 9, "timeout", max_auto=1))

    assert started == []


# ── P3：span 增量落盘 ───────────────────────────────────────────────────────


class _FakeRepo:
    def __init__(self) -> None:
        self.traces = 0
        self.spans: list[str] = []
        self.calls: list[str] = []

    async def save_trace(self, trace) -> None:
        self.traces += 1
        self.calls.append("trace")

    async def save_span(self, span) -> None:
        self.spans.append(span.span_name)

    async def save_llm_call(self, call) -> None:
        self.calls.append("llm")


def _tracer_with_spans(n: int) -> Tracer:
    tracer = Tracer("t-1")
    for i in range(n):
        with tracer.span(f"n{i}"):
            pass
    return tracer


def test_partial_flush_writes_only_new_spans(monkeypatch) -> None:
    repo = _FakeRepo()
    monkeypatch.setattr(sdk_module, "TraceRepository", lambda: repo)
    tracer = _tracer_with_spans(3)

    asyncio.run(tracer.flush_partial())
    assert repo.traces == 1
    assert repo.spans == ["n0", "n1", "n2"]

    # 再来两个 span：第二次增量只写这两个，且 trace 行不重复插
    for i in (3, 4):
        with tracer.span(f"n{i}"):
            pass
    asyncio.run(tracer.flush_partial())
    assert repo.traces == 1
    assert repo.spans == ["n0", "n1", "n2", "n3", "n4"]


def test_partial_flush_is_noop_when_nothing_new(monkeypatch) -> None:
    repo = _FakeRepo()
    monkeypatch.setattr(sdk_module, "TraceRepository", lambda: repo)
    tracer = _tracer_with_spans(2)

    asyncio.run(tracer.flush_partial())
    asyncio.run(tracer.flush_partial())

    assert repo.spans == ["n0", "n1"]
    assert repo.traces == 1


def test_final_flush_writes_only_the_tail(monkeypatch) -> None:
    """终态 flush 与增量 flush 共用游标——尾部的 span 只写一次。"""
    repo = _FakeRepo()
    monkeypatch.setattr(sdk_module, "TraceRepository", lambda: repo)
    monkeypatch.setattr(sdk_module, "_flush_langfuse", lambda _t: asyncio.sleep(0))
    tracer = _tracer_with_spans(2)
    asyncio.run(tracer.flush_partial())
    with tracer.span("tail"):
        pass

    asyncio.run(tracer.flush())
    asyncio.run(tracer.flush())  # flush 会 pop 桶；重复调用不应再写

    assert repo.spans == ["n0", "n1", "tail"]


def test_maybe_flush_partial_respects_threshold(monkeypatch) -> None:
    repo = _FakeRepo()
    monkeypatch.setattr(sdk_module, "TraceRepository", lambda: repo)
    monkeypatch.setenv("TRACE_FLUSH_EVERY", "2")
    tracer = _tracer_with_spans(1)

    asyncio.run(sdk_module._maybe_flush_partial(tracer))
    assert repo.spans == []          # 才 1 个，没到阈值

    tracer2 = _tracer_with_spans(2)
    asyncio.run(sdk_module._maybe_flush_partial(tracer2))
    assert repo.spans == ["n0", "n1"]


def test_threshold_zero_disables_incremental(monkeypatch) -> None:
    repo = _FakeRepo()
    monkeypatch.setattr(sdk_module, "TraceRepository", lambda: repo)
    monkeypatch.setenv("TRACE_FLUSH_EVERY", "0")
    tracer = _tracer_with_spans(5)

    asyncio.run(sdk_module._maybe_flush_partial(tracer))

    assert repo.spans == []
