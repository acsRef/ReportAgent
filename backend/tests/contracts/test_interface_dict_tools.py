"""search_interface_dictionary：未配置降级、命中序列化、401 重登、不可达不抛栈。

P2 Task 2 增量：MCP-first dispatcher（MCP 成功走 MCP；失败时 flag-gated HTTP fallback）。

现有 httpx-mock 测试通过 `_force_mcp_unavailable` 触发 fallback 路径——MCP 模拟失败，
走原 httpx fallback（既有测试覆盖的契约不变）。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

pytestmark = pytest.mark.contracts


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def dict_env(monkeypatch):
    monkeypatch.setenv("RAGENT_URL", "http://fake:8000")
    monkeypatch.setenv("RAGENT_USER", "admin")
    monkeypatch.setenv("RAGENT_PASSWORD", "admin123")
    monkeypatch.setenv("DICT_KB_NAME", "数据字典")


def _force_mcp_unavailable(monkeypatch, mod):
    """让 MCP 路径失败 → dispatcher 走 HTTP fallback（既有 httpx mock 测试用）。"""
    fake = MagicMock()
    fake.call_tool.side_effect = MCPBoundaryError(
        MCPErrorCode.MCP_UNAVAILABLE, "test-forces-fallback"
    )
    monkeypatch.setattr(mod, "get_rag_mcp_client", lambda: fake)


def _mock_mcp_success(monkeypatch, mod, *, matches=None, side_effect=None):
    """让 MCP 路径返回受控结果（dispatcher 新测试用）。"""
    fake = MagicMock()
    if side_effect is not None:
        fake.call_tool.side_effect = side_effect
    else:
        fake.call_tool.return_value = {"matches": matches or []}
    monkeypatch.setattr(mod, "get_rag_mcp_client", lambda: fake)
    return fake


def test_unset_env_degrades_gracefully(monkeypatch):
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    monkeypatch.delenv("RAGENT_URL", raising=False)
    # MCP 失败 → fallback 到 HTTP；HTTP 也因 RAGENT_URL 为空返回未配置错
    _force_mcp_unavailable(monkeypatch, mod)
    mod._token_cache.clear()

    out = json.loads(search_interface_dictionary.invoke({"query": "销售额"}))
    assert "未配置" in out["error"]


def test_happy_path_serializes_matches(monkeypatch, dict_env):
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    _force_mcp_unavailable(monkeypatch, mod)

    def fake_post(url, **kw):
        return _Resp(200, {"access_token": "t"})

    def fake_request(method, url, **kw):
        if url.endswith("/api/v1/kb"):
            return _Resp(200, [{"id": "kb-9", "name": "数据字典"}])
        return _Resp(200, {"items": [{"chunk_id": "c1", "document_id": "d1",
                                      "text": "total_amount 销售金额", "title": "dict-table_public_fact_sales.md",
                                      "section_path": "", "score": 0.8}], "degraded": False})

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    monkeypatch.setattr(mod.httpx, "request", fake_request)
    mod._token_cache.clear()

    out = json.loads(search_interface_dictionary.invoke({"query": "total_amount 是什么", "top_k": 3}))
    assert out["matches"][0]["text"].startswith("total_amount")
    assert out["matches"][0]["source"] == "dict-table_public_fact_sales.md"
    assert out["matches"][0]["data_source_type"] == "table"  # 表名命中 → table


def test_data_source_type_marked_stream_for_websocket(monkeypatch, dict_env):
    """接口/长连接/推送类字典块必须标 data_source_type=stream，让 LLM 别写 SQL。"""
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    _force_mcp_unavailable(monkeypatch, mod)

    def fake_post(url, **kw): return _Resp(200, {"access_token": "t"})
    def fake_request(method, url, **kw):
        if url.endswith("/api/v1/kb"):
            return _Resp(200, [{"id": "kb-9", "name": "数据字典"}])
        return _Resp(200, {"items": [
            {"chunk_id": "c1", "document_id": "d1", "text": "amt = 实付金额",
             "title": "market-push心跳与on_message字段说明", "section_path": "接口字典: market-push > 消息 `heartbeat` 字段",
             "score": 0.8},
            {"chunk_id": "c2", "document_id": "d2", "text": "total_amount = 销售金额",
             "title": "dict-table_public_fact_sales.md", "section_path": "表 `public.fact_sales` > 字段",
             "score": 0.7},
        ], "degraded": False})

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    monkeypatch.setattr(mod.httpx, "request", fake_request)
    mod._token_cache.clear()

    out = json.loads(search_interface_dictionary.invoke({"query": "amt", "top_k": 5}))
    by_source = {m["source"]: m for m in out["matches"]}
    assert by_source["market-push心跳与on_message字段说明"]["data_source_type"] == "stream"
    assert by_source["dict-table_public_fact_sales.md"]["data_source_type"] == "table"


def test_unreachable_returns_error_text(monkeypatch, dict_env):
    from app.tools.interface_dict_tools import search_interface_dictionary
    import httpx as real_httpx
    import app.tools.interface_dict_tools as mod

    _force_mcp_unavailable(monkeypatch, mod)

    def boom(*a, **kw):
        raise real_httpx.ConnectError("refused")

    monkeypatch.setattr(mod.httpx, "post", boom)
    mod._token_cache.clear()
    out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
    assert "不可达" in out["error"]


def test_empty_result_semantics(monkeypatch, dict_env):
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    _force_mcp_unavailable(monkeypatch, mod)

    def fake_post(url, **kw):
        return _Resp(200, {"access_token": "t"})

    def fake_request(method, url, **kw):
        if url.endswith("/api/v1/kb"):
            return _Resp(200, [{"id": "kb-9", "name": "数据字典"}])
        return _Resp(200, {"items": [], "degraded": False})

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    monkeypatch.setattr(mod.httpx, "request", fake_request)
    mod._token_cache.clear()
    out = json.loads(search_interface_dictionary.invoke({"query": "不存在的字段"}))
    assert out["matches"] == []
    assert "无匹配" in out["note"]


def test_second_401_returns_login_failed_text(monkeypatch, dict_env):
    """重登后仍 401（账号被锁等）→ 登录失败文案 + 原始响应体，而非通用 HTTP 401。

    终审 I-3：对齐 ragent-py 侧 6d31a80 的 original_detail 保留模式。
    """
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    _force_mcp_unavailable(monkeypatch, mod)

    def fake_post(url, **kw):
        return _Resp(200, {"access_token": "t"})

    def fake_request(method, url, **kw):
        if url.endswith("/api/v1/kb"):
            return _Resp(200, [{"id": "kb-9", "name": "数据字典"}])
        return _Resp(401, {"detail": "account locked"})  # 两次 retrieve 都 401

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    monkeypatch.setattr(mod.httpx, "request", fake_request)
    mod._token_cache.clear()
    mod._kb_id_cache.clear()

    out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
    assert "登录失败" in out["error"], f"未翻译为登录失败文案: {out!r}"
    assert "account locked" in out["error"], f"未保留原始响应诊断体: {out!r}"


def test_second_403_returns_permission_text(monkeypatch, dict_env):
    """重登后 403 → 无权读取文案（I-3 的 status 分支）。"""
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    _force_mcp_unavailable(monkeypatch, mod)

    calls = {"retrieve": 0}

    def fake_post(url, **kw):
        return _Resp(200, {"access_token": "t"})

    def fake_request(method, url, **kw):
        if url.endswith("/api/v1/kb"):
            return _Resp(200, [{"id": "kb-9", "name": "数据字典"}])
        calls["retrieve"] += 1
        return _Resp(401, {"detail": "expired"}) if calls["retrieve"] == 1 \
            else _Resp(403, {"detail": "kb forbidden"})

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    monkeypatch.setattr(mod.httpx, "request", fake_request)
    mod._token_cache.clear()
    mod._kb_id_cache.clear()

    out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
    assert "无权读取" in out["error"], f"未翻译为无权读取文案: {out!r}"
    assert "kb forbidden" in out["error"]


# ─────────────────────────────────────────────────────────────
# P2 Task 2: MCP-first dispatcher 行为契约
# ─────────────────────────────────────────────────────────────


class TestSearchInterfaceDictDispatcher:
    """search_interface_dictionary 的 MCP-first dispatcher 行为。

    契约（docs/plans/2026-08-26-p2-rag-mcp-boundary.md 决策 1/4）：
      - MCP 成功 → 走 MCP 路径（HTTP 不应被调）
      - MCPBoundaryError(UNAVAILABLE) + fallback allowed → HTTP fallback
      - MCPBoundaryError(UNAVAILABLE) + flag 锁定 → JSON error 含 MCP code
      - MCPBoundaryError(INVALID_RESPONSE) → JSON error（不 retry 不 fallback）
    """

    def test_mcp_path_used_when_available(self, monkeypatch):
        from app.tools.interface_dict_tools import search_interface_dictionary
        import app.tools.interface_dict_tools as mod

        fake = _mock_mcp_success(monkeypatch, mod, matches=[
            {
                "chunk_id": "c1", "document_id": "d1",
                "text": "total_amount 销售金额",
                "title": "dict-table_public_fact_sales.md",
                "section_path": "表字段",
                "score": 0.8,
            },
        ])
        mod._token_cache.clear()  # 防沾染
        # HTTP 路径不应被调（设个断言）
        http_called = []
        monkeypatch.setattr(
            mod.httpx, "post",
            lambda *a, **kw: (http_called.append(1) or _Resp(200, {"access_token": "t"})),
        )

        out = json.loads(
            search_interface_dictionary.invoke({"query": "total_amount 是什么", "top_k": 3})
        )
        # MCP 路径结果：source 用 title，data_source_type=table（表名命中）
        assert out["matches"][0]["text"].startswith("total_amount")
        assert out["matches"][0]["source"] == "dict-table_public_fact_sales.md"
        assert out["matches"][0]["data_source_type"] == "table"
        assert not http_called, "MCP 成功时不应触发 HTTP fallback"
        fake.call_tool.assert_called_once_with(
            "search_dictionary", {"query": "total_amount 是什么", "top_k": 3}
        )

    def test_fallback_to_http_on_mcp_unavailable(self, monkeypatch, dict_env):
        """MCP UNAVAILABLE + flag 未锁 → HTTP fallback（既有契约）。"""
        from app.tools.interface_dict_tools import search_interface_dictionary
        import app.tools.interface_dict_tools as mod

        _force_mcp_unavailable(monkeypatch, mod)

        def fake_post(url, **kw):
            return _Resp(200, {"access_token": "t"})

        def fake_request(method, url, **kw):
            if url.endswith("/api/v1/kb"):
                return _Resp(200, [{"id": "kb-9", "name": "数据字典"}])
            return _Resp(200, {"items": [{
                "chunk_id": "c1", "document_id": "d1",
                "text": "total_amount 销售金额", "title": "dict-table_public_fact_sales.md",
                "section_path": "", "score": 0.8,
            }], "degraded": False})

        monkeypatch.setattr(mod.httpx, "post", fake_post)
        monkeypatch.setattr(mod.httpx, "request", fake_request)
        mod._token_cache.clear()

        out = json.loads(search_interface_dictionary.invoke({"query": "total_amount"}))
        assert out["matches"][0]["text"].startswith("total_amount")

    def test_flag_locked_returns_mcp_error_json(self, monkeypatch):
        """flag 锁定 → MCP UNAVAILABLE → 返回 JSON error 含 MCP_<CODE>。"""
        from app.tools.interface_dict_tools import search_interface_dictionary
        import app.tools.interface_dict_tools as mod

        _force_mcp_unavailable(monkeypatch, mod)
        monkeypatch.setattr(mod, "_fallback_allowed", lambda: False)

        out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
        assert "error" in out
        assert "MCP_UNAVAILABLE" in out["error"], (
            f"flag 锁定时应把 MCP code 写进既有 error 形状: {out!r}"
        )

    def test_invalid_response_returns_error_json_without_fallback(self, monkeypatch):
        """MCP INVALID_RESPONSE → 显式错误（不 fallback；fallback 放行也无济于事）。"""
        from app.tools.interface_dict_tools import search_interface_dictionary
        import app.tools.interface_dict_tools as mod

        _mock_mcp_success(
            monkeypatch, mod,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_INVALID_RESPONSE, "bad json"),
        )
        monkeypatch.setattr(mod, "_fallback_allowed", lambda: True)
        http_called = []

        monkeypatch.setattr(
            mod.httpx, "post",
            lambda *a, **kw: (http_called.append(1) or _Resp(200, {"access_token": "t"})),
        )

        out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
        assert "error" in out
        assert "MCP_INVALID_RESPONSE" in out["error"]
        assert not http_called, "INVALID_RESPONSE 不应触发 HTTP fallback"

    def test_mcp_empty_matches_returns_empty_with_note(self, monkeypatch):
        """MCP 合法空命中 → matches=[] + note（与 HTTP 路径契约一致）。"""
        from app.tools.interface_dict_tools import search_interface_dictionary
        import app.tools.interface_dict_tools as mod

        # 模拟 classifier 把空命中包装成 {"matches": [], "_note": "字典库无匹配：..."}
        fake = MagicMock()
        fake.call_tool.return_value = {"matches": [], "_note": "字典库无匹配：xxx"}
        monkeypatch.setattr(mod, "get_rag_mcp_client", lambda: fake)

        out = json.loads(search_interface_dictionary.invoke({"query": "xxx"}))
        assert out["matches"] == []
        assert "无匹配" in out["note"]
