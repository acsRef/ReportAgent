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

    def result(self, timeout=None):
        if self._error is not None:
            raise self._error
        return self._result


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
        monkeypatch.delenv("REPORTAGENT_E2E", raising=False)
        monkeypatch.delenv("PHASE2_MCP_ONLY", raising=False)
        monkeypatch.setenv("APP_ENV", "development")
        assert _resolve_phase2_flag() is False
        assert _fallback_allowed() is True

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
