"""MCP client tests（P2, docs/plans/2026-08-26-p2-rag-mcp-boundary.md Q2/Q4/Q6/Q7/Q8）。

离线——mock asyncio loop + future + CallToolResult，不起真子进程。
覆盖：
  - 响应分类器 5 桶（Q2）：空命中 / 协议错 / 不可达各一条合并断言
  - Transport 异常归一为 MCP_TIMEOUT / MCP_UNAVAILABLE（Q8 Pin 1）
  - CallToolResult → raw_text 提取（Q8 Pin 2）
  - Retry：仅 TIMEOUT，max_attempts=2（Q4 + Q8 Pin）
  - 单例 + 关闭幂等 + shim 无条件委托（Q6）
  - _subprocess_env 包含 DICT_KB_NAME / FAQ_KB_NAME（Q7）
  - flag 解析（Q5）
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

    def test_empty_hit_is_not_error(self):
        """两个 EMPTY 前缀（字典库 / FAQ）同属一张表：空命中不是错误，只标注 _note。"""
        for prefix in ("字典库无匹配：foo", "FAQ 无匹配：bar"):
            assert self._classify(prefix) == {"matches": [], "_note": prefix}

    def test_invalid_text_raises_invalid(self):
        """缺参前缀 / 未知文本 / 空串 / JSON 数组 / 标量 / 截断 JSON → 同一条 INVALID。"""
        cases = (
            "缺少必填参数 query",
            "随机垃圾文本，没有任何已知前缀",
            "",
            json.dumps([1, 2, 3]),
            json.dumps("hello"),
            '{"matches": [',  # 截断
        )
        for raw in cases:
            with pytest.raises(MCPBoundaryError) as ei:
                self._classify(raw)
            assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE, raw

    def test_unavailable_prefixes_raise_unavailable(self):
        """登录失败 / 无权 / 检索失败 / 客户端异常：四个运行时前缀一次断言。"""
        for raw in (
            "登录失败：请检查账号",
            "无权读取字典知识库",
            "检索失败：HTTP 500 internal",
            "客户端异常: connection reset",
        ):
            with pytest.raises(MCPBoundaryError) as ei:
                self._classify(raw)
            assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE, raw


# ════════════════════════════════════════════════════════════════
# Transport 异常归一（Q8 Pin 1）
# ════════════════════════════════════════════════════════════════


class _FakeFuture:
    def __init__(self, *, result=None, error: Exception | None = None) -> None:
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

    def test_transport_failures_become_mcp_unavailable(self, monkeypatch):
        """ConnectionError / BrokenPipe / OSError / 未知异常 → 同一个 UNAVAILABLE。

        不断言 detail 里的异常类名——那是实现细节，不是运行时承诺。
        """
        for exc in (
            ConnectionError("refused"),
            BrokenPipeError("closed"),
            OSError(36, "Broken pipe"),
            RuntimeError("spawn failed"),
        ):
            client = _client_with_mock_loop(monkeypatch)
            _patch_future(monkeypatch, _FakeFuture(error=exc))
            with pytest.raises(MCPBoundaryError) as ei:
                client._do_call("search_dictionary", {"query": "x"})
            assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE, repr(exc)


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


def _patch_rcts_to_run_sync(monkeypatch, run_fn):
    """patch asyncio.run_coroutine_threadsafe 让 coro 在新 loop 上同步跑完。"""
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", run_fn)


def _run_coro_sync_rcts(coro, loop):
    """rcts 替身：把 coro 在临时 loop 上跑完，返回已完成的 future。"""
    new_loop = asyncio.new_event_loop()
    try:
        new_loop.run_until_complete(coro)
    finally:
        new_loop.close()
    fut = MagicMock()
    fut.result = lambda timeout=None: None
    return fut


class TestResetAsync:
    def test_reset_awaits_cleanup_and_clears_state(self, monkeypatch):
        """_reset_async / _reset 的完整清理契约：

        1. 真 await session → read_cm 的 __aexit__（不是只清状态指针）
        2. cleanup 抛错仍清状态（重入安全）
        3. session / read_cm 为 None 时不抛
        4. sync _reset 把 cleanup 调度到 loop 上并等它跑完
        5. loop 已无时 _reset 仍能清状态
        """
        # 1. async 清理：两个 __aexit__ 都被 await，状态清空
        client = RagMCPClient(_MCPConfig())
        session = _FakeAsyncCM("session")
        read_cm = _FakeAsyncCM("read_cm")
        client._session = session
        client._read_cm = read_cm

        asyncio.run(client._reset_async())

        assert session.aexit_count == 1, "session.__aexit__ was not awaited"
        assert read_cm.aexit_count == 1, "read_cm.__aexit__ was not awaited"
        assert client._session is None
        assert client._read_cm is None

        # 2. cleanup 抛错 → 状态仍被清（先清状态再 cleanup）
        boom_client = RagMCPClient(_MCPConfig())
        boom_session = _FakeAsyncCM("session")
        boom_read_cm = _FakeAsyncCM("read_cm")

        async def _boom(*args):
            raise RuntimeError("aexit boom")

        boom_session.__aexit__ = _boom
        boom_read_cm.__aexit__ = _boom
        boom_client._session = boom_session
        boom_client._read_cm = boom_read_cm

        asyncio.run(boom_client._reset_async())

        assert boom_client._session is None
        assert boom_client._read_cm is None

        # 3. 两者皆 None → 不抛
        asyncio.run(RagMCPClient(_MCPConfig())._reset_async())

        # 4. sync _reset：cleanup 真的被 await（不是只清状态）
        sync_client = RagMCPClient(_MCPConfig())
        sync_client._loop = MagicMock()
        sync_session = _FakeAsyncCM("session")
        sync_read_cm = _FakeAsyncCM("read_cm")
        sync_client._session = sync_session
        sync_client._read_cm = sync_read_cm

        _patch_rcts_to_run_sync(monkeypatch, _run_coro_sync_rcts)
        sync_client._reset()

        assert sync_session.aexit_count == 1, "session cleanup was not awaited"
        assert sync_read_cm.aexit_count == 1, "read_cm cleanup was not awaited"
        assert sync_client._session is None
        assert sync_client._read_cm is None

        # 5. loop 已无（早期失败 / close 后再 reset）→ 仍清状态
        loopless = RagMCPClient(_MCPConfig())
        loopless._loop = None
        loopless._session = _FakeAsyncCM()
        loopless._read_cm = _FakeAsyncCM()

        loopless._reset()

        assert loopless._session is None
        assert loopless._read_cm is None


class TestDoCallTimeoutLifecycle:
    def test_timeout_cancels_inflight_and_drains_before_retry(self, monkeypatch):
        """TIMEOUT 路径（review 修订钉子）：

        1. 原 future 仍 running → future.cancel()
        2. drain 必须等 _call_async 跑完（含 cleanup）才返回
        3. 旧 coroutine 收到 CancelledError → 自己的 except 块自清 session
           （**关键**：cleanup 走 local capture，不碰 self._session 全局）
        4. 抛 MCP_TIMEOUT 时 state 已干净
        """
        client = RagMCPClient(_MCPConfig())
        client._loop = MagicMock()

        cleanup_duration = 0.05  # 让 cleanup 有可观察的耗时（无时间阈值断言）

        class _SlowSession(_FakeAsyncSession):
            async def __aexit__(self, *args):
                await asyncio.sleep(cleanup_duration)
                self.aexit_count += 1
                return False

        old_session = _SlowSession("old_session")
        old_read_cm = _FakeAsyncCM("old_read_cm")
        client._session = old_session
        client._read_cm = old_read_cm

        cancel_count = {"n": 0}
        coroutine_completed = {"value": False}

        def rcts(coro, loop):
            # 区分 _call_async（要 cancel + 等 cleanup）vs 其他（如
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
        # 3. 旧 coroutine 完整跑完（含 cleanup），_do_call 才返回
        assert coroutine_completed["value"], "coroutine did not complete before main thread returned"
        # 4. 旧 session 由 coroutine 关闭（不是 main thread 误关）
        assert old_session.aexit_count == 1, (
            f"old session not closed by coroutine (aexit_count={old_session.aexit_count})"
        )
        assert old_read_cm.aexit_count == 1, (
            f"old read_cm not closed by coroutine (aexit_count={old_read_cm.aexit_count})"
        )
        # 5. state 干净（retry 拿到干净视图）
        assert client._session is None
        assert client._read_cm is None


class TestCloseLifecycle:
    def test_close_awaits_cleanup_and_is_idempotent(self, monkeypatch):
        """close() 必须真 await session + read_cm 的 __aexit__；且重复调用安全。"""
        client = RagMCPClient(_MCPConfig())
        client._loop = MagicMock()
        session = _FakeAsyncCM("session")
        read_cm = _FakeAsyncCM("read_cm")
        client._session = session
        client._read_cm = read_cm

        _patch_rcts_to_run_sync(monkeypatch, _run_coro_sync_rcts)
        client.close()

        assert session.aexit_count == 1
        assert read_cm.aexit_count == 1
        assert client._session is None
        assert client._read_cm is None
        assert client._loop is None
        assert client._thread is None

        # 幂等：loop 已无 → 再 close 一次也不应抛 / 不应重复 cleanup
        client.close()
        assert session.aexit_count == 1
        assert read_cm.aexit_count == 1


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
    def test_single_and_multiple_text_parts(self):
        assert _extract_text(_FakeResult([_FakeTextContent("text", '{"matches": []}')])) == '{"matches": []}'

        multi = _FakeResult([
            _FakeTextContent("text", "line1"),
            _FakeTextContent("text", "line2"),
        ])
        assert _extract_text(multi) == "line1\nline2"

    def test_empty_none_and_non_text_content_raise_invalid(self):
        class _FakeImage:
            type = "image"
            text = None  # 模拟 image content

        for result in (
            _FakeResult([]),
            _FakeResult(None),
            _FakeResult([_FakeTextContent("text", "ok"), _FakeImage()]),
        ):
            with pytest.raises(MCPBoundaryError) as ei:
                _extract_text(result)
            assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

        # 非 text 内容 detail 点明原因
        with pytest.raises(MCPBoundaryError) as ei:
            _extract_text(_FakeResult([_FakeImage()]))
        assert "non-text" in ei.value.detail


# ════════════════════════════════════════════════════════════════
# Retry：仅 MCP_TIMEOUT，max_attempts=2（Q4）
# ════════════════════════════════════════════════════════════════


class TestCallWithRetry:
    def test_timeout_first_attempt_succeeds_second(self, monkeypatch):
        client = _client_with_mock_loop(monkeypatch)
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
        calls = {"n": 0}

        def fake_do_call(name, args):
            calls["n"] += 1
            raise MCPBoundaryError(MCPErrorCode.MCP_TIMEOUT, f"timeout {calls['n']}")

        monkeypatch.setattr(client, "_do_call", fake_do_call)
        with pytest.raises(MCPBoundaryError) as ei:
            client._call_with_retry("search_dictionary", {"query": "x"})
        assert ei.value.code is MCPErrorCode.MCP_TIMEOUT
        assert calls["n"] == 2  # max_attempts=2，不超过 2 次

    def test_unavailable_and_invalid_do_not_retry(self, monkeypatch):
        """只有 TIMEOUT 进 retry 预算；UNAVAILABLE / INVALID 一次就上抛。"""
        for code in (MCPErrorCode.MCP_UNAVAILABLE, MCPErrorCode.MCP_INVALID_RESPONSE):
            client = _client_with_mock_loop(monkeypatch)
            calls = {"n": 0}

            def fake_do_call(name, args, _code=code):
                calls["n"] += 1
                raise MCPBoundaryError(_code, "no retry")

            monkeypatch.setattr(client, "_do_call", fake_do_call)
            with pytest.raises(MCPBoundaryError) as ei:
                client._call_with_retry("search_dictionary", {"query": "x"})
            assert ei.value.code is code
            assert calls["n"] == 1, f"{code.value} 不应 retry"


# ════════════════════════════════════════════════════════════════
# call_tool 端到端（classifier + retry 联动）
# ════════════════════════════════════════════════════════════════


class TestCallToolEndToEnd:
    def test_call_tool_success_passes_dict_through(self, monkeypatch):
        """成功路径：顶层 dict 原样透传——degraded 与内部字段都由 client 原样带出

        （client 不感知稳定字段，过滤是 tool 层的职责，Q8 澄清）。
        """
        client = _client_with_mock_loop(monkeypatch)
        payloads = (
            # 内部字段 + degraded=True 全部原样透传
            {
                "matches": [{
                    "chunk_id": "c1", "document_id": "d1", "text": "t",
                    "title": "f", "section_path": "p", "score": 0.5,
                }],
                "degraded": True,
            },
            # 空 matches + degraded=False 也是合法成功形态
            {"matches": [], "degraded": False},
        )
        for payload in payloads:
            monkeypatch.setattr(
                client, "_do_call", lambda name, args, _p=payload: json.dumps(_p)
            )
            out = client.call_tool("search_dictionary", {"query": "x"})
            assert out == payload

    def test_call_tool_empty_match_returns_empty_matches(self, monkeypatch):
        """空 ≠ 错：EMPTY 前缀走成功路径返回 matches=[]。"""
        client = _client_with_mock_loop(monkeypatch)
        monkeypatch.setattr(client, "_do_call", lambda name, args: "FAQ 无匹配：x")
        out = client.call_tool("search_faq", {"query": "x"})
        assert out["matches"] == []
        assert "_note" in out

    def test_call_tool_invalid_response_propagates(self, monkeypatch):
        """错 ≠ 空：协议错必须显式上抛，不许伪装成空结果。"""
        client = _client_with_mock_loop(monkeypatch)
        monkeypatch.setattr(client, "_do_call", lambda name, args: "缺少必填参数")
        with pytest.raises(MCPBoundaryError) as ei:
            client.call_tool("search_faq", {"query": "x"})
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE


# ════════════════════════════════════════════════════════════════
# _subprocess_env 必须含 KB 名（Q7）
# ════════════════════════════════════════════════════════════════


class TestSubprocessEnv:
    def test_includes_dict_and_faq_kb_name(self, monkeypatch):
        """Q7：漏掉任一 KB 名都会让 MCP 通道与 HTTP 通道检索不同知识库。"""
        monkeypatch.setenv("DICT_KB_NAME", "测试字典")
        monkeypatch.setenv("FAQ_KB_NAME", "FAQ-测试")
        env = RagMCPClient()._subprocess_env()
        assert env["DICT_KB_NAME"] == "测试字典"
        assert env["FAQ_KB_NAME"] == "FAQ-测试"


# ════════════════════════════════════════════════════════════════
# 单例 + 关闭幂等 + shim 无条件委托（Q6）
# ════════════════════════════════════════════════════════════════


class TestSingletonLifecycle:
    def test_get_returns_singleton_and_close_creates_new(self, monkeypatch):
        import app.tools.mcp_client as mod
        monkeypatch.setattr(mod, "_client", None)

        a = get_rag_mcp_client()
        assert get_rag_mcp_client() is a  # 单例

        close_rag_mcp_client()
        b = get_rag_mcp_client()
        assert a is not b  # close 后重建

        close_rag_mcp_client()

    def test_shim_close_delegates_and_is_safe_when_empty(self, monkeypatch):
        """shim 关闭：无条件委托给 mcp_client 单例；单例未初始化时安全 no-op。"""
        from app.tools import mcp_faq_client as shim
        import app.tools.mcp_client as mod

        monkeypatch.setattr(mod, "_client", None)
        # 未初始化 → 不抛
        shim.close_mcp_faq_client()

        real = get_rag_mcp_client()
        close_count = {"n": 0}
        real.close = lambda: close_count.__setitem__("n", close_count["n"] + 1)
        try:
            shim.close_mcp_faq_client()
            assert close_count["n"] == 1
        finally:
            mod._client = None

        # close 幂等（无单例时重复调用仍安全）
        close_rag_mcp_client()
        close_rag_mcp_client()


# ════════════════════════════════════════════════════════════════
# flag 解析（Q5：REPORTAGENT_E2E > 显式；P5 起默认 ON）
# ════════════════════════════════════════════════════════════════


class TestResolvePhase2Flag:
    def test_e2e_overrides_explicit_false(self, monkeypatch):
        """REPORTAGENT_E2E=1 必须覆盖 PHASE2_MCP_ONLY=false。"""
        monkeypatch.setenv("REPORTAGENT_E2E", "1")
        monkeypatch.setenv("PHASE2_MCP_ONLY", "false")
        assert _resolve_phase2_flag() is True

    def test_explicit_false_allows_fallback(self, monkeypatch):
        monkeypatch.delenv("REPORTAGENT_E2E", raising=False)
        monkeypatch.setenv("PHASE2_MCP_ONLY", "false")
        monkeypatch.delenv("APP_ENV", raising=False)
        assert _resolve_phase2_flag() is False
        assert _fallback_allowed() is True

    def test_defaults_true_and_fallback_locked(self, monkeypatch):
        """P5 后 _resolve_phase2_flag 不再读 APP_ENV：无显式值时一律默认 True。

        （含显式 true 用例——它同样压过 APP_ENV，落在同一断言上。）
        """
        monkeypatch.delenv("REPORTAGENT_E2E", raising=False)
        cases = (
            {"APP_ENV": "development"},
            {"APP_ENV": "production"},
            {},  # APP_ENV 未设 → fail-closed
            {"PHASE2_MCP_ONLY": "true", "APP_ENV": "development"},
        )
        for env in cases:
            monkeypatch.delenv("PHASE2_MCP_ONLY", raising=False)
            monkeypatch.delenv("APP_ENV", raising=False)
            for key, val in env.items():
                monkeypatch.setenv(key, val)
            assert _resolve_phase2_flag() is True, env
            assert _fallback_allowed() is False, env


# ════════════════════════════════════════════════════════════════
# _validate_matches_contract（review 第 2 轮 P1 修订）
# ════════════════════════════════════════════════════════════════


class TestValidateMatchesContract:
    """_validate_matches_contract 业务契约校验（review 第 2 轮 P1 修订）。

    plan 决策 3 稳定字段集：每个 item 必须含 text(str) + score(numeric)；
    不符合 → MCP_INVALID_RESPONSE，让上层显式处理而不是静默空结果。
    """

    def test_normalizes_stable_fields_and_strips_internal(self):
        """稳定字段保留 + 内部字段 strip + 空 text 合法（兼容 HTTP fallback）。

        这是 P2 边界职责的核心：ReportAgent 不应该知道 RAG 内部 response 形态。
        """
        result = {
            "matches": [
                {
                    "text": "hello", "score": 0.9, "title": "t1", "section_path": "p",
                    # ragent-py 内部字段——必须 strip
                    "chunk_id": "c1", "document_id": "d1",
                    "embedding": [0.1, 0.2, 0.3], "rerank_score": 0.95, "kb_id": "kb-1",
                },
                {"text": "world", "score": 0.7},
            ],
        }
        assert _validate_matches_contract(result) == [
            {"text": "hello", "score": 0.9, "title": "t1", "section_path": "p"},
            {"text": "world", "score": 0.7},
        ]
        # text 可以是空字符串，只要是 str
        assert _validate_matches_contract({"matches": [{"text": "", "score": 0.5}]}) == [
            {"text": "", "score": 0.5}
        ]

    def test_missing_matches_field_raises_invalid_response(self):
        """matches 缺失 / 空 dict → INVALID（典型 schema drift），不是「合法没命中」。"""
        for result in ({"degraded": False}, {}):
            with pytest.raises(MCPBoundaryError) as ei:
                _validate_matches_contract(result)
            assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE
            assert "matches" in ei.value.detail

    def test_empty_matches_is_legal(self):
        """EMPTY ≠ DRIFT：matches=[] 是合法空命中，通过校验。"""
        assert _validate_matches_contract({"matches": []}) == []

    def test_shape_errors_raise_invalid_response(self):
        """result 非 dict / matches 非 list / item 非 dict → 同一 INVALID 分支。"""
        for result in (
            "not a dict",  # type: ignore[arg-type]
            {"matches": "not a list"},
            {"matches": ["string instead of dict"]},
        ):
            with pytest.raises(MCPBoundaryError) as ei:
                _validate_matches_contract(result)
            assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

    def test_text_shape_errors_raise_invalid_with_index(self):
        """text 缺失 / 非 str / None → INVALID，且 detail 带 matches[i] 索引。"""
        for result in (
            {"matches": [{"score": 0.9}]},
            {"matches": [{"text": 123, "score": 0.9}]},
            {"matches": [{"text": None, "score": 0.9}]},
        ):
            with pytest.raises(MCPBoundaryError) as ei:
                _validate_matches_contract(result)
            assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE
            assert "text" in ei.value.detail

        # 第 2 个 item 的 text 坏了 → detail 里能看到索引（定位错误用）
        with pytest.raises(MCPBoundaryError) as ei:
            _validate_matches_contract({"matches": [
                {"text": "ok", "score": 0.5},
                {"text": 123, "score": 0.9},
            ]})
        assert "matches[1]" in ei.value.detail

    def test_score_shape_errors_raise_invalid(self):
        """score 缺失 / 非数值 / bool（int 子类但语义不对）→ 全部拒。"""
        for result in (
            {"matches": [{"text": "hello"}]},
            {"matches": [{"text": "hello", "score": "0.9"}]},
            {"matches": [{"text": "hello", "score": True}]},
        ):
            with pytest.raises(MCPBoundaryError) as ei:
                _validate_matches_contract(result)
            assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE
            assert "score" in ei.value.detail
