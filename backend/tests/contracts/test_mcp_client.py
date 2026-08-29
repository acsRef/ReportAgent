"""MCP client tests（P2, docs/plans/2026-08-26-p2-rag-mcp-boundary.md Q2/Q4/Q6/Q7/Q8）。

离线——mock asyncio loop + future + CallToolResult，不起真子进程。
覆盖：
  - 响应分类器 5 桶（Q2）
  - Transport 异常归一为 MCP_TIMEOUT / MCP_UNAVAILABLE（Q8 Pin 1）
  - CallToolResult → raw_text 提取（Q8 Pin 2）
  - Retry：仅 TIMEOUT，max_attempts=2（Q4 + Q8 Pin）
  - 单例 + 关闭幂等 + shim 无条件委托（Q6）
  - _subprocess_env 包含 DICT_KB_NAME（Q7）
  - flag 解析三级优先级（Q5）
  - mcp_client 不反向 import mcp_faq_client（P0 钉子）
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from app.tools.mcp_client import (
    RagMCPClient,
    _MCPConfig,
    _extract_text,
    _fallback_allowed,
    _resolve_phase2_flag,
    _validate_matches_contract,
    close_rag_mcp_client,
    get_rag_mcp_client,
)
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

pytestmark = pytest.mark.contracts


# ════════════════════════════════════════════════════════════════
# 响应分类器（Q2 决议）
# ════════════════════════════════════════════════════════════════


class TestClassifyResponse:
    def _classify(self, raw_text: str):
        """通过 client 实例调方法（_classify_response 是 RagMCPClient 的方法）。"""
        return RagMCPClient()._classify_response(raw_text)

    def test_classify_json_object_passes_through(self):
        raw = json.dumps({"matches": [{"text": "x", "score": 0.9}], "degraded": False})
        out = self._classify(raw)
        assert out == {"matches": [{"text": "x", "score": 0.9}], "degraded": False}

    def test_classify_json_with_internal_fields_passes_through(self):
        """client 不感知稳定字段，tool 层负责过滤（Q8 澄清）。"""
        raw = json.dumps({
            "matches": [{"chunk_id": "c1", "document_id": "d1", "text": "t",
                         "title": "f", "section_path": "p", "score": 0.5}],
            "degraded": True,
        })
        out = self._classify(raw)
        # 内部字段原样透传，client 不剥
        assert "chunk_id" in out["matches"][0]
        assert "document_id" in out["matches"][0]
        assert out["degraded"] is True

    def test_classify_empty_match_prefix_dict(self):
        out = self._classify("字典库无匹配：foo")
        assert out == {"matches": [], "_note": "字典库无匹配：foo"}

    def test_classify_empty_match_prefix_faq(self):
        out = self._classify("FAQ 无匹配：bar")
        assert out == {"matches": [], "_note": "FAQ 无匹配：bar"}

    def test_classify_invalid_param_prefix_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            self._classify("缺少必填参数 query")
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_classify_login_failure_raises_unavailable(self):
        with pytest.raises(MCPBoundaryError) as ei:
            self._classify("登录失败：请检查账号")
        assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE

    def test_classify_permission_denied_raises_unavailable(self):
        with pytest.raises(MCPBoundaryError) as ei:
            self._classify("无权读取字典知识库")
        assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE

    def test_classify_retrieve_failure_raises_unavailable(self):
        with pytest.raises(MCPBoundaryError) as ei:
            self._classify("检索失败：HTTP 500 internal")
        assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE

    def test_classify_client_exception_raises_unavailable(self):
        with pytest.raises(MCPBoundaryError) as ei:
            self._classify("客户端异常: connection reset")
        assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE

    def test_classify_unknown_non_json_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            self._classify("随机垃圾文本，没有任何已知前缀")
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_classify_empty_string_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            self._classify("")
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_classify_json_array_not_object_raises_invalid(self):
        """JSON 顶层非 object → INVALID（Q8 ⑦：client 只做协议形态校验）。"""
        with pytest.raises(MCPBoundaryError) as ei:
            self._classify(json.dumps([1, 2, 3]))
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_classify_json_scalar_not_object_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            self._classify(json.dumps("hello"))
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_classify_partial_json_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            self._classify('{"matches": [')  # 截断
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE


# ════════════════════════════════════════════════════════════════
# Transport 异常归一（Q8 Pin 1）
# ════════════════════════════════════════════════════════════════


class _FakeFuture:
    def __init__(self, *, result=None, error: Optional[Exception] = None) -> None:
        self._result = result
        self._error = error
        self.cancel_called = False

    def result(self, timeout=None):
        if self._error is not None:
            raise self._error
        return self._result

    def cancel(self):
        self.cancel_called = True
        return True


def _client_with_mock_loop(monkeypatch) -> RagMCPClient:
    """构造 client 但用 mock loop 避免起真线程。"""
    client = RagMCPClient(_MCPConfig())
    client._loop = MagicMock()
    return client


def _patch_future(monkeypatch, future: "_FakeFuture") -> None:
    def _wrapped(coro, loop):
        coro.close()  # 避免 unawaited coroutine warning
        return future

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", _wrapped)


class TestDoCallTransportNormalization:
    def test_timeout_error_becomes_mcp_timeout(self, monkeypatch):
        client = _client_with_mock_loop(monkeypatch)
        _patch_future(
            monkeypatch, _FakeFuture(error=asyncio.TimeoutError())
        )
        with pytest.raises(MCPBoundaryError) as ei:
            client._do_call("search_dictionary", {"query": "x"})
        assert ei.value.code is MCPErrorCode.MCP_TIMEOUT
        assert "timeout" in ei.value.detail

    def test_connection_error_becomes_mcp_unavailable(self, monkeypatch):
        client = _client_with_mock_loop(monkeypatch)
        _patch_future(monkeypatch, _FakeFuture(error=ConnectionError("refused")))
        with pytest.raises(MCPBoundaryError) as ei:
            client._do_call("search_dictionary", {"query": "x"})
        assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE
        assert "ConnectionError" in ei.value.detail

    def test_broken_pipe_becomes_mcp_unavailable(self, monkeypatch):
        client = _client_with_mock_loop(monkeypatch)
        _patch_future(
            monkeypatch, _FakeFuture(error=BrokenPipeError("closed"))
        )
        with pytest.raises(MCPBoundaryError) as ei:
            client._do_call("search_dictionary", {"query": "x"})
        assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE

    def test_oserror_becomes_mcp_unavailable(self, monkeypatch):
        client = _client_with_mock_loop(monkeypatch)
        _patch_future(monkeypatch, _FakeFuture(error=OSError(36, "Broken pipe")))
        with pytest.raises(MCPBoundaryError) as ei:
            client._do_call("search_dictionary", {"query": "x"})
        assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE

    def test_unknown_subprocess_error_becomes_mcp_unavailable(self, monkeypatch):
        """subprocess spawn fail / SDK 未知异常兜底为 UNAVAILABLE。"""
        client = _client_with_mock_loop(monkeypatch)
        _patch_future(
            monkeypatch, _FakeFuture(error=RuntimeError("spawn failed"))
        )
        with pytest.raises(MCPBoundaryError) as ei:
            client._do_call("search_dictionary", {"query": "x"})
        assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE
        assert "RuntimeError" in ei.value.detail


# ════════════════════════════════════════════════════════════════
# Async lifecycle cleanup（review 修订钉子）
# ════════════════════════════════════════════════════════════════


class _FakeAsyncCM:
    """可记录 __aenter__/__aexit__ 调用的 async context manager。"""

    def __init__(self, name: str = "cm") -> None:
        self.name = name
        self.aenter_count = 0
        self.aexit_count = 0

    async def __aenter__(self):
        self.aenter_count += 1
        return self

    async def __aexit__(self, *args):
        self.aexit_count += 1
        return False


class _FakeAsyncSession(_FakeAsyncCM):
    """模拟 MCP ClientSession：__aenter__/__aexit__ + 长跑 call_tool（可被 cancel）。"""

    def __init__(self, name: str = "session") -> None:
        super().__init__(name)
        self.call_tool_count = 0

    async def call_tool(self, name: str, args: dict):
        self.call_tool_count += 1
        # 模拟长跑 MCP 调用（可被 CancelledError 中断）
        await asyncio.sleep(100)


def _run_coro_sync(coro):
    """在测试线程内同步运行 coroutine 到完成。返回 (result_or_exc, captured_logs)。"""
    new_loop = asyncio.new_event_loop()
    try:
        return new_loop.run_until_complete(coro)
    finally:
        new_loop.close()


class TestResetAsync:
    def test_awaits_session_then_read_cm_in_order(self):
        """_reset_async 必须真正 await session 和 read_cm 的 __aexit__，且顺序：session → read_cm。"""
        client = RagMCPClient(_MCPConfig())
        session = _FakeAsyncCM("session")
        read_cm = _FakeAsyncCM("read_cm")
        client._session = session
        client._read_cm = read_cm

        # 直接 await _reset_async
        asyncio.run(client._reset_async())

        assert session.aexit_count == 1, "session.__aexit__ was not awaited"
        assert read_cm.aexit_count == 1, "read_cm.__aexit__ was not awaited"
        assert client._session is None
        assert client._read_cm is None

    def test_state_cleared_before_cleanup(self):
        """状态先清（防重入），再 await cleanup；cleanup 失败不影响状态。"""
        client = RagMCPClient(_MCPConfig())
        session = _FakeAsyncCM("session")
        client._session = session
        client._read_cm = _FakeAsyncCM("read_cm")

        # __aexit__ 抛异常 → 状态仍应被清
        async def boom(*args):
            raise RuntimeError("aexit boom")

        session.__aexit__ = boom
        asyncio.run(client._reset_async())

        assert client._session is None  # 状态已清
        assert client._read_cm is None  # 状态已清

    def test_handles_none_session_and_read_cm(self):
        """session / read_cm 为 None 时不抛。"""
        client = RagMCPClient(_MCPConfig())
        # 两者都 None
        asyncio.run(client._reset_async())  # 不应抛


def _patch_rcts_to_run_sync(monkeypatch, run_fn):
    """patch asyncio.run_coroutine_threadsafe 让 coro 在新 loop 上同步跑完。"""
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", run_fn)


class TestSyncReset:
    def test_sync_reset_actually_runs_async_cleanup(self, monkeypatch):
        """sync _reset() 必须把 cleanup 调度到 loop 上并 await；不能只清状态。"""
        client = RagMCPClient(_MCPConfig())
        client._loop = MagicMock()
        session = _FakeAsyncCM("session")
        read_cm = _FakeAsyncCM("read_cm")
        client._session = session
        client._read_cm = read_cm

        def rcts(coro, loop):
            new_loop = asyncio.new_event_loop()
            try:
                new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
            fut = MagicMock()
            fut.result = lambda timeout=None: None
            return fut

        _patch_rcts_to_run_sync(monkeypatch, rcts)
        client._reset()

        # 关键断言：__aexit__ 被真正调用（不是只清状态指针）
        assert session.aexit_count == 1, "session cleanup was not awaited"
        assert read_cm.aexit_count == 1, "read_cm cleanup was not awaited"
        assert client._session is None
        assert client._read_cm is None

    def test_sync_reset_without_loop_clears_state(self):
        """loop 已无时 _reset 仍能清状态（早期失败 / close 后再 reset）。"""
        client = RagMCPClient(_MCPConfig())
        client._loop = None
        client._session = _FakeAsyncCM()
        client._read_cm = _FakeAsyncCM()
        client._reset()
        assert client._session is None
        assert client._read_cm is None


class TestDoCallTimeoutLifecycle:
    def test_timeout_cancels_inflight_and_preserves_new_session_for_retry(self, monkeypatch):
        """TIMEOUT 路径（review 修订钉子）：

        1. 原 future 仍 running → future.cancel() + 等待完成
        2. 旧 coroutine 收到 CancelledError → 自己的 except 块自清 session
           （**关键**：cleanup 走 local capture，不碰 self._session 全局）
        3. retry 拿干净 state → 新 session 创建且不被旧 cleanup 误关

        这是 review 暴露的真竞态：旧设计 main thread 直接 self._reset()，
        但旧 coroutine 仍在跑、可能改 self._session，导致 retry 创建的新
        session 被旧 cleanup 关掉。
        """
        client = RagMCPClient(_MCPConfig())
        client._loop = MagicMock()

        old_session = _FakeAsyncSession("old_session")
        old_read_cm = _FakeAsyncCM("old_read_cm")
        client._session = old_session
        client._read_cm = old_read_cm

        cancel_count = {"n": 0}
        coroutine_completed = {"value": False}

        def rcts(coro, loop):
            # 区分 _call_async（要 cancel + wait for cleanup）vs 其他（如
            # done_event.wait，纯跑完即可，不 cancel）
            coro_name = getattr(getattr(coro, "cr_code", None), "co_name", None)
            is_call_async = coro_name == "_call_async"

            new_loop = asyncio.new_event_loop()
            try:
                task = new_loop.create_task(coro)
                if is_call_async:
                    new_loop.run_until_complete(asyncio.sleep(0.01))
                    task.cancel()
                    cancel_count["n"] += 1
                try:
                    new_loop.run_until_complete(task)
                except (asyncio.CancelledError, Exception):
                    pass
                if is_call_async:
                    coroutine_completed["value"] = True
            finally:
                new_loop.close()

            if is_call_async:
                return _FakeFuture(error=asyncio.TimeoutError())
            else:
                # done_event.wait() 等辅助 coroutine：返回成功 future
                fut = MagicMock()
                fut.result = lambda timeout=None: None
                return fut

        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", rcts)

        with pytest.raises(MCPBoundaryError) as ei:
            client._do_call("search_dictionary", {"query": "x"})

        # 1. timeout 抛 MCP_TIMEOUT
        assert ei.value.code is MCPErrorCode.MCP_TIMEOUT
        # 2. future.cancel() 被调用
        assert cancel_count["n"] == 1, "future.cancel() was not called on timeout"
        # 3. 旧 coroutine 完整跑完（含 cleanup）
        assert coroutine_completed["value"], "coroutine did not complete before main thread returned"
        # 4. 旧 session 由 coroutine 关闭（不是 main thread 误关）
        assert old_session.aexit_count == 1, (
            f"old session not closed by coroutine (aexit_count={old_session.aexit_count})"
        )
        assert old_read_cm.aexit_count == 1, (
            f"old read_cm not closed by coroutine (aexit_count={old_read_cm.aexit_count})"
        )
        # 5. state 干净
        assert client._session is None
        assert client._read_cm is None

        # 6. retry 会创建新 session；验证新 session 不被旧 coroutine 的残留 cleanup 误关
        #    （per-invocation isolation：coroutine 持有 local capture，cleanup 不碰全局）
        new_session = _FakeAsyncCM("new_session")
        new_read_cm = _FakeAsyncCM("new_read_cm")
        client._session = new_session
        client._read_cm = new_read_cm

        import time
        time.sleep(0.05)  # 给任何潜在的延迟 cleanup 机会跑

    def test_timeout_drain_waits_for_done_event_signal(self, monkeypatch):
        """TIMEOUT 路径：drain 必须等 _call_async 的 done_event.set()——即
        coroutine 完整跑完（含 cleanup）。用慢 cleanup 验证 elapsed time 包含 cleanup。

        钉子：retry 进入前 cleanup 必须完成。
        """
        import time

        client = RagMCPClient(_MCPConfig())
        client._loop = MagicMock()

        cleanup_duration = 0.15  # seconds; 长于事件循环调度抖动
        cleanup_state = {"started": False, "completed": False}

        class _SlowSession(_FakeAsyncCM):
            async def call_tool(self, name, args):
                await asyncio.sleep(100)

            async def __aexit__(self, *args):
                cleanup_state["started"] = True
                await asyncio.sleep(cleanup_duration)
                cleanup_state["completed"] = True
                return False

        client._session = _SlowSession()
        client._read_cm = _FakeAsyncCM()

        def rcts(coro, loop):
            coro_name = getattr(getattr(coro, "cr_code", None), "co_name", None)
            is_call_async = coro_name == "_call_async"

            new_loop = asyncio.new_event_loop()
            try:
                task = new_loop.create_task(coro)
                if is_call_async:
                    new_loop.run_until_complete(asyncio.sleep(0.01))
                    task.cancel()
                try:
                    new_loop.run_until_complete(task)
                except (asyncio.CancelledError, Exception):
                    pass
            finally:
                new_loop.close()

            if is_call_async:
                return _FakeFuture(error=asyncio.TimeoutError())
            else:
                fut = MagicMock()
                fut.result = lambda timeout=None: None
                return fut

        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", rcts)

        start = time.monotonic()
        with pytest.raises(MCPBoundaryError):
            client._do_call("search_dictionary", {"query": "x"})
        elapsed = time.monotonic() - start

        # 核心不变量：cleanup 必须完成（done_event.set() 已发生）
        assert cleanup_state["started"], "cleanup never started"
        assert cleanup_state["completed"], (
            "_do_call returned before coroutine's cleanup finished——"
            "drain 没有真正等 done_event"
        )
        # Sanity check：elapsed time 至少包含 cleanup 耗时（说明 drain 在等）
        assert elapsed >= cleanup_duration * 0.8, (
            f"elapsed {elapsed:.3f}s < cleanup {cleanup_duration:.3f}s——"
            "drain 没有真的等 cleanup"
        )


class TestCloseLifecycle:
    def test_close_actually_awaits_session_and_read_cm(self, monkeypatch):
        """close() 必须真正 await session + read_cm 的 __aexit__。"""
        client = RagMCPClient(_MCPConfig())
        client._loop = MagicMock()
        session = _FakeAsyncCM("session")
        read_cm = _FakeAsyncCM("read_cm")
        client._session = session
        client._read_cm = read_cm

        def rcts(coro, loop):
            new_loop = asyncio.new_event_loop()
            try:
                new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
            fut = MagicMock()
            fut.result = lambda timeout=None: None
            return fut

        _patch_rcts_to_run_sync(monkeypatch, rcts)
        client.close()

        assert session.aexit_count == 1
        assert read_cm.aexit_count == 1
        assert client._session is None
        assert client._read_cm is None
        assert client._loop is None
        assert client._thread is None


# ════════════════════════════════════════════════════════════════
# CallToolResult → raw_text 提取（Q8 Pin 2）
# ════════════════════════════════════════════════════════════════


class _FakeTextContent:
    def __init__(self, type_: str, text: str) -> None:
        self.type = type_
        self.text = text


class _FakeResult:
    def __init__(self, content) -> None:
        self.content = content


class TestExtractText:
    def test_single_text_part(self):
        result = _FakeResult([_FakeTextContent("text", '{"matches": []}')])
        assert _extract_text(result) == '{"matches": []}'

    def test_multiple_text_parts_joined_with_newline(self):
        result = _FakeResult([
            _FakeTextContent("text", "line1"),
            _FakeTextContent("text", "line2"),
        ])
        assert _extract_text(result) == "line1\nline2"

    def test_empty_content_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            _extract_text(_FakeResult([]))
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_none_content_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            _extract_text(_FakeResult(None))
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_non_text_content_raises_invalid(self):
        class _FakeImage:
            type = "image"
            text = None  # 模拟 image content

        result = _FakeResult([
            _FakeTextContent("text", "ok"),
            _FakeImage(),
        ])
        with pytest.raises(MCPBoundaryError) as ei:
            _extract_text(result)
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE
        assert "non-text" in ei.value.detail


# ════════════════════════════════════════════════════════════════
# Retry：仅 MCP_TIMEOUT，max_attempts=2（Q4）
# ════════════════════════════════════════════════════════════════


class TestCallWithRetry:
    def test_timeout_first_attempt_succeeds_second(self, monkeypatch):
        client = _client_with_mock_loop(monkeypatch)
        client._reset = lambda: None
        calls = {"n": 0}

        def fake_do_call(name, args):
            calls["n"] += 1
            if calls["n"] == 1:
                raise MCPBoundaryError(
                    MCPErrorCode.MCP_TIMEOUT, "first timeout"
                )
            return "second ok"

        monkeypatch.setattr(client, "_do_call", fake_do_call)
        assert client._call_with_retry("search_dictionary", {"query": "x"}) == "second ok"
        assert calls["n"] == 2

    def test_timeout_exhausts_budget_raises(self, monkeypatch):
        client = _client_with_mock_loop(monkeypatch)
        client._reset = lambda: None
        calls = {"n": 0}

        def fake_do_call(name, args):
            calls["n"] += 1
            raise MCPBoundaryError(MCPErrorCode.MCP_TIMEOUT, f"timeout {calls['n']}")

        monkeypatch.setattr(client, "_do_call", fake_do_call)
        with pytest.raises(MCPBoundaryError) as ei:
            client._call_with_retry("search_dictionary", {"query": "x"})
        assert ei.value.code is MCPErrorCode.MCP_TIMEOUT
        assert calls["n"] == 2  # max_attempts=2，不超过 2 次

    def test_unavailable_does_not_retry(self, monkeypatch):
        client = _client_with_mock_loop(monkeypatch)
        client._reset = lambda: None
        calls = {"n": 0}

        def fake_do_call(name, args):
            calls["n"] += 1
            raise MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down")

        monkeypatch.setattr(client, "_do_call", fake_do_call)
        with pytest.raises(MCPBoundaryError) as ei:
            client._call_with_retry("search_dictionary", {"query": "x"})
        assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE
        assert calls["n"] == 1  # UNAVAILABLE 不 retry

    def test_invalid_response_does_not_retry(self, monkeypatch):
        client = _client_with_mock_loop(monkeypatch)
        client._reset = lambda: None
        calls = {"n": 0}

        def fake_do_call(name, args):
            calls["n"] += 1
            raise MCPBoundaryError(MCPErrorCode.MCP_INVALID_RESPONSE, "bad")

        monkeypatch.setattr(client, "_do_call", fake_do_call)
        with pytest.raises(MCPBoundaryError) as ei:
            client._call_with_retry("search_dictionary", {"query": "x"})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE
        assert calls["n"] == 1

    def test_empty_result_does_not_retry(self, monkeypatch):
        """EMPTY 走成功路径，不消耗 retry budget。"""
        client = _client_with_mock_loop(monkeypatch)
        client._reset = lambda: None
        calls = {"n": 0}

        def fake_do_call(name, args):
            calls["n"] += 1
            return "字典库无匹配：foo"  # classifier → {matches: [], _note: ...}

        monkeypatch.setattr(client, "_do_call", fake_do_call)
        result = client._call_with_retry("search_dictionary", {"query": "x"})
        assert result == "字典库无匹配：foo"
        assert calls["n"] == 1


# ════════════════════════════════════════════════════════════════
# call_tool 端到端（classifier + retry 联动）
# ════════════════════════════════════════════════════════════════


class TestCallToolEndToEnd:
    def test_call_tool_success_returns_parsed(self, monkeypatch):
        client = _client_with_mock_loop(monkeypatch)
        client._reset = lambda: None
        monkeypatch.setattr(client, "_do_call", lambda name, args: json.dumps({
            "matches": [{"text": "t", "score": 0.9}], "degraded": False
        }))
        out = client.call_tool("search_dictionary", {"query": "x"})
        assert out["matches"][0]["text"] == "t"

    def test_call_tool_passes_degraded_through(self, monkeypatch):
        """degraded=True 是 mcp_client 透传字段，client 不剥（Q8 澄清）。"""
        client = _client_with_mock_loop(monkeypatch)
        client._reset = lambda: None
        monkeypatch.setattr(
            client, "_do_call",
            lambda name, args: json.dumps({"matches": [], "degraded": True})
        )
        out = client.call_tool("search_dictionary", {"query": "x"})
        assert out["degraded"] is True

    def test_call_tool_empty_match_returns_empty_matches(self, monkeypatch):
        client = _client_with_mock_loop(monkeypatch)
        client._reset = lambda: None
        monkeypatch.setattr(client, "_do_call", lambda name, args: "FAQ 无匹配：x")
        out = client.call_tool("search_faq", {"query": "x"})
        assert out["matches"] == []
        assert "_note" in out

    def test_call_tool_invalid_response_propagates(self, monkeypatch):
        client = _client_with_mock_loop(monkeypatch)
        client._reset = lambda: None
        monkeypatch.setattr(client, "_do_call", lambda name, args: "缺少必填参数")
        with pytest.raises(MCPBoundaryError) as ei:
            client.call_tool("search_faq", {"query": "x"})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE


# ════════════════════════════════════════════════════════════════
# _subprocess_env 必须含 DICT_KB_NAME（Q7）
# ════════════════════════════════════════════════════════════════


class TestSubprocessEnv:
    def test_includes_dict_kb_name(self, monkeypatch):
        monkeypatch.setenv("DICT_KB_NAME", "测试字典")
        monkeypatch.setenv("RAGENT_URL", "http://test")
        monkeypatch.delenv("UNRELATED_KEY", raising=False)
        client = RagMCPClient()
        env = client._subprocess_env()
        assert env["DICT_KB_NAME"] == "测试字典"
        assert env["RAGENT_URL"] == "http://test"

    def test_includes_faq_kb_name(self, monkeypatch):
        monkeypatch.setenv("FAQ_KB_NAME", "FAQ-测试")
        client = RagMCPClient()
        env = client._subprocess_env()
        assert env["FAQ_KB_NAME"] == "FAQ-测试"

    def test_inherits_parent_env(self, monkeypatch):
        monkeypatch.setenv("PATH", "/some/path")
        client = RagMCPClient()
        env = client._subprocess_env()
        assert env["PATH"] == "/some/path"


# ════════════════════════════════════════════════════════════════
# 单例 + 关闭幂等 + shim 无条件委托（Q6）
# ════════════════════════════════════════════════════════════════


class TestSingletonLifecycle:
    def test_get_returns_same_instance(self, monkeypatch):
        import app.tools.mcp_client as mod
        monkeypatch.setattr(mod, "_client", None)
        a = get_rag_mcp_client()
        b = get_rag_mcp_client()
        assert a is b
        # 清理
        close_rag_mcp_client()

    def test_close_after_get_creates_new_instance(self, monkeypatch):
        import app.tools.mcp_client as mod
        monkeypatch.setattr(mod, "_client", None)
        a = get_rag_mcp_client()
        close_rag_mcp_client()
        b = get_rag_mcp_client()
        assert a is not b
        # 清理
        close_rag_mcp_client()

    def test_close_without_get_is_noop(self):
        """close 在没有 _client 时必须安全（Q6 无条件委托 + 幂等）。"""
        import app.tools.mcp_client as mod
        mod._client = None
        # 不应抛
        close_rag_mcp_client()
        close_rag_mcp_client()  # 再调一次仍安全

    def test_close_mcp_faq_client_delegates_unconditionally(self, monkeypatch):
        """shim 关闭：即使 mcp_client 单例已初始化，shim 也必须关闭它。"""
        from app.tools import mcp_faq_client as shim
        import app.tools.mcp_client as mod

        monkeypatch.setattr(mod, "_client", None)
        real = get_rag_mcp_client()
        close_count = {"n": 0}
        real.close = lambda: close_count.__setitem__("n", close_count["n"] + 1)
        try:
            shim.close_mcp_faq_client()
            assert close_count["n"] == 1
        finally:
            # 重置模块状态
            mod._client = None

    def test_close_mcp_faq_client_when_nothing_initialized(self):
        """shim 在 mcp_client 单例未初始化时也必须安全 no-op。"""
        import app.tools.mcp_client as mod
        mod._client = None
        from app.tools import mcp_faq_client as shim
        # 不应抛
        shim.close_mcp_faq_client()


# ════════════════════════════════════════════════════════════════
# flag 解析三级优先级（Q5：REPORTAGENT_E2E > 显式 > APP_ENV 推断）
# ════════════════════════════════════════════════════════════════


class TestResolvePhase2Flag:
    def test_e2e_overrides_explicit_false(self, monkeypatch):
        """REPORTAGENT_E2E=1 必须覆盖 PHASE2_MCP_ONLY=false。"""
        monkeypatch.setenv("REPORTAGENT_E2E", "1")
        monkeypatch.setenv("PHASE2_MCP_ONLY", "false")
        assert _resolve_phase2_flag() is True

    def test_explicit_true_wins(self, monkeypatch):
        monkeypatch.delenv("REPORTAGENT_E2E", raising=False)
        monkeypatch.setenv("PHASE2_MCP_ONLY", "true")
        monkeypatch.setenv("APP_ENV", "development")  # 显式应压过
        assert _resolve_phase2_flag() is True

    def test_explicit_false_wins(self, monkeypatch):
        monkeypatch.delenv("REPORTAGENT_E2E", raising=False)
        monkeypatch.setenv("PHASE2_MCP_ONLY", "false")
        monkeypatch.delenv("APP_ENV", raising=False)
        assert _resolve_phase2_flag() is False

    def test_app_env_dev_default_false(self, monkeypatch):
        """P5 起默认 ON（停止 fallback），APP_ENV=development 也不再 false。"""
        monkeypatch.delenv("REPORTAGENT_E2E", raising=False)
        monkeypatch.delenv("PHASE2_MCP_ONLY", raising=False)
        monkeypatch.setenv("APP_ENV", "development")
        assert _resolve_phase2_flag() is True
        assert _fallback_allowed() is False

    def test_app_env_production_default_true(self, monkeypatch):
        monkeypatch.delenv("REPORTAGENT_E2E", raising=False)
        monkeypatch.delenv("PHASE2_MCP_ONLY", raising=False)
        monkeypatch.setenv("APP_ENV", "production")
        assert _resolve_phase2_flag() is True
        assert _fallback_allowed() is False

    def test_app_env_unset_default_true_fail_closed(self, monkeypatch):
        """APP_ENV 未设 → fail-closed（production 默认）。"""
        monkeypatch.delenv("REPORTAGENT_E2E", raising=False)
        monkeypatch.delenv("PHASE2_MCP_ONLY", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)
        assert _resolve_phase2_flag() is True


# ════════════════════════════════════════════════════════════════
# mcp_client 不反向 import mcp_faq_client（P0 钉子）
# ════════════════════════════════════════════════════════════════


class TestModuleBoundary:
    def test_mcp_client_does_not_import_mcp_faq_client(self):
        """mcp_client 是依赖底，不反向引用 shim（AST 级别检查 import 语句）。"""
        import ast
        from pathlib import Path

        src_path = Path(__file__).resolve().parents[2] / "app" / "tools" / "mcp_client.py"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "mcp_faq_client" in alias.name:
                        offenders.append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and "mcp_faq_client" in node.module:
                    offenders.append(f"from {node.module} import ...")
                for alias in node.names:
                    if "mcp_faq_client" in alias.name:
                        offenders.append(f"from {node.module} import {alias.name}")
        assert not offenders, (
            f"mcp_client.py 反向 import mcp_faq_client 会造成循环依赖: {offenders}"
        )

    def test_mcp_faq_client_imports_mcp_client(self):
        """shim 必须依赖 mcp_client（正向）。"""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[2] / "app" / "tools" / "mcp_faq_client.py").read_text(
            encoding="utf-8"
        )
        assert "from app.tools.mcp_client import" in src


# ════════════════════════════════════════════════════════════════
# _validate_matches_contract（review 第 2 轮 P1 修订）
# ════════════════════════════════════════════════════════════════


class TestValidateMatchesContract:
    """_validate_matches_contract 业务契约校验（review 第 2 轮 P1 修订）。

    plan 决策 3 稳定字段集：每个 item 必须含 text(str) + score(numeric)；
    不符合 → MCP_INVALID_RESPONSE，让上层显式处理而不是静默空结果。
    """

    def test_valid_matches_returns_normalized_list(self):
        result = {
            "matches": [
                {"text": "hello", "score": 0.9, "title": "t1", "chunk_id": "c1"},
                {"text": "world", "score": 0.7},
            ],
        }
        out = _validate_matches_contract(result)
        # 内部字段（chunk_id）被 strip，tool 层只见稳定契约
        assert out == [
            {"text": "hello", "score": 0.9, "title": "t1"},
            {"text": "world", "score": 0.7},
        ]

    def test_missing_matches_raises_invalid_response(self):
        """matches 字段缺失 → MCP_INVALID_RESPONSE（review 第 3 轮修订）。

        区别于 EMPTY_RESULT（matches=[] 合法）；不允许把 schema drift
        （如 {} 或 {"results": [...]}）当成「合法检索只是没命中」。
        """
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract({"degraded": False})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE
        assert "matches" in ei.value.detail

    def test_missing_matches_empty_dict_raises_invalid_response(self):
        """空 dict {} → MCP_INVALID_RESPONSE（典型 schema drift 形态）。"""
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract({})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_empty_matches_returns_empty(self):
        assert _validate_matches_contract({"matches": []}) == []

    def test_matches_not_list_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract({"matches": "not a list"})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_item_not_dict_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract({"matches": ["string instead of dict"]})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_item_missing_text_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract({"matches": [{"score": 0.9}]})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE
        assert "text" in ei.value.detail

    def test_item_text_not_str_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract({"matches": [{"text": 123, "score": 0.9}]})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_item_text_none_raises_invalid(self):
        """text=None 是常见 bug（HTTP 路径误把 None 当 text）。"""
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract({"matches": [{"text": None, "score": 0.9}]})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_item_missing_score_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract({"matches": [{"text": "hello"}]})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE
        assert "score" in ei.value.detail

    def test_item_score_not_numeric_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract({"matches": [{"text": "hello", "score": "0.9"}]})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_item_score_bool_rejected(self):
        """bool 是 int 子类，但语义不是 score——必须拒。"""
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract({"matches": [{"text": "hello", "score": True}]})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_item_text_empty_string_allowed(self):
        """text 可以是空字符串（与 HTTP fallback 兼容），只要是 str。"""
        out = _validate_matches_contract({"matches": [{"text": "", "score": 0.5}]})
        assert out == [{"text": "", "score": 0.5}]

    def test_result_not_dict_raises_invalid(self):
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract("not a dict")  # type: ignore[arg-type]
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_stable_fields_preserved(self):
        """稳定契约字段（text/score/title/section_path）保留。"""
        result = {
            "matches": [{
                "text": "hello",
                "score": 0.9,
                "title": "t",
                "section_path": "p",
            }],
        }
        out = _validate_matches_contract(result)
        assert out[0]["text"] == "hello"
        assert out[0]["score"] == 0.9
        assert out[0]["title"] == "t"
        assert out[0]["section_path"] == "p"

    def test_internal_fields_stripped(self):
        """ragent-py 内部字段（chunk_id/document_id/embedding/rerank_score/kb_id）
        在 boundary 处 strip——tool 层只见稳定契约（review 第 3 轮 P1 修订）。

        这是 P2 边界职责的核心：ReportAgent 不应该知道 RAG 内部 response 形态。
        """
        result = {
            "matches": [{
                "text": "hello",
                "score": 0.9,
                "title": "t",
                # 内部字段——必须 strip
                "chunk_id": "c1",
                "document_id": "d1",
                "embedding": [0.1, 0.2, 0.3],
                "rerank_score": 0.95,
                "kb_id": "kb-1",
            }],
        }
        out = _validate_matches_contract(result)
        item = out[0]
        # 内部字段不应出现
        for internal_field in ("chunk_id", "document_id", "embedding",
                               "rerank_score", "kb_id"):
            assert internal_field not in item, (
                f"{internal_field} 是 RAG 内部字段，不应透传到 tool 层"
            )
        # 稳定契约字段保留
        assert item["text"] == "hello"
        assert item["score"] == 0.9
        assert item["title"] == "t"

    def test_error_index_in_detail(self):
        """第 N 个 item 失败 → detail 含索引（定位错误用）。"""
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract({"matches": [
                {"text": "ok", "score": 0.5},
                {"text": "bad"},  # 缺 score
            ]})
        assert "matches[1]" in ei.value.detail
