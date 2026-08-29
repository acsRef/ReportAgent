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
    _force_mcp_unavailable(monkeypatch, mod)
    mod._token_cache.clear()

    out = json.loads(search_interface_dictionary.invoke({"query": "销售额"}))
    assert "MCP_UNAVAILABLE" in out["error"]


def test_happy_path_serializes_matches(monkeypatch, dict_env):
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    _mock_mcp_success(monkeypatch, mod, matches=[
        {"chunk_id": "c1", "document_id": "d1",
         "text": "total_amount 销售金额", "title": "dict-table_public_fact_sales.md",
         "section_path": "", "score": 0.8},
    ])
    mod._token_cache.clear()

    out = json.loads(search_interface_dictionary.invoke({"query": "total_amount 是什么", "top_k": 3}))
    assert out["matches"][0]["text"].startswith("total_amount")
    assert out["matches"][0]["source"] == "dict-table_public_fact_sales.md"
    assert out["matches"][0]["data_source_type"] == "table"  # 表名命中 → table


def test_data_source_type_marked_stream_for_websocket(monkeypatch, dict_env):
    """接口/长连接/推送类字典块必须标 data_source_type=stream，让 LLM 别写 SQL。"""
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    _mock_mcp_success(monkeypatch, mod, matches=[
        {"chunk_id": "c1", "document_id": "d1", "text": "amt = 实付金额",
         "title": "market-push心跳与on_message字段说明", "section_path": "接口字典: market-push > 消息 `heartbeat` 字段",
         "score": 0.8},
        {"chunk_id": "c2", "document_id": "d2", "text": "total_amount = 销售金额",
         "title": "dict-table_public_fact_sales.md", "section_path": "表 `public.fact_sales` > 字段",
         "score": 0.7},
    ])
    mod._token_cache.clear()

    out = json.loads(search_interface_dictionary.invoke({"query": "amt", "top_k": 5}))
    by_source = {m["source"]: m for m in out["matches"]}
    assert by_source["market-push心跳与on_message字段说明"]["data_source_type"] == "stream"
    assert by_source["dict-table_public_fact_sales.md"]["data_source_type"] == "table"


def test_unreachable_returns_error_text(monkeypatch, dict_env):
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    _force_mcp_unavailable(monkeypatch, mod)
    mod._token_cache.clear()
    out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
    assert "MCP_UNAVAILABLE" in out["error"]


def test_empty_result_semantics(monkeypatch, dict_env):
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    _mock_mcp_success(monkeypatch, mod, matches=[])
    mod._token_cache.clear()
    out = json.loads(search_interface_dictionary.invoke({"query": "不存在的字段"}))
    assert out["matches"] == []
    assert "无匹配" in out["note"]


def test_second_401_returns_login_failed_text(monkeypatch, dict_env):
    """P5 起 HTTP 401/403 路径已不再经 fallback，此用例保留为 MCP 错误路径验证。"""
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    _force_mcp_unavailable(monkeypatch, mod)
    mod._token_cache.clear()
    mod._kb_id_cache.clear()

    out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
    assert "MCP_UNAVAILABLE" in out["error"]


def test_second_403_returns_permission_text(monkeypatch, dict_env):
    """P5 起 HTTP 403 路径已不再经 fallback，此用例保留为 MCP 错误路径验证。"""
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    _force_mcp_unavailable(monkeypatch, mod)
    mod._token_cache.clear()
    mod._kb_id_cache.clear()

    out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
    assert "MCP_UNAVAILABLE" in out["error"]


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
        """P5 起不再 HTTP fallback，MCP UNAVAILABLE 直接返回 MCP 错误。"""
        from app.tools.interface_dict_tools import search_interface_dictionary
        import app.tools.interface_dict_tools as mod

        _force_mcp_unavailable(monkeypatch, mod)
        mod._token_cache.clear()

        out = json.loads(search_interface_dictionary.invoke({"query": "total_amount"}))
        assert "MCP_UNAVAILABLE" in out["error"]

    def test_flag_locked_returns_mcp_error_json(self, monkeypatch):
        """flag 锁定 → MCP UNAVAILABLE → 返回 JSON error 含 MCP_<CODE>。"""
        from app.tools.interface_dict_tools import search_interface_dictionary
        import app.tools.interface_dict_tools as mod

        _force_mcp_unavailable(monkeypatch, mod)

        out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
        assert "error" in out
        assert "MCP_UNAVAILABLE" in out["error"]

    def test_invalid_response_returns_error_json_without_fallback(self, monkeypatch):
        """MCP INVALID_RESPONSE → 显式错误。"""
        from app.tools.interface_dict_tools import search_interface_dictionary
        import app.tools.interface_dict_tools as mod

        _mock_mcp_success(
            monkeypatch, mod,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_INVALID_RESPONSE, "bad json"),
        )

        out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
        assert "error" in out
        assert "MCP_INVALID_RESPONSE" in out["error"]

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


class TestMcpResponseSchemaValidation:
    """MCP response schema validation（review 第 2 轮 P1 修订）。

    search_interface_dictionary 必须拒绝坏 MCP response 而不是把空 text
    当成「没找到字段」。
    """

    def test_missing_text_returns_error_json(self, monkeypatch):
        from app.tools.interface_dict_tools import search_interface_dictionary
        import app.tools.interface_dict_tools as mod

        fake = MagicMock()
        fake.call_tool.return_value = {"matches": [{"score": 0.9, "title": "x"}]}
        monkeypatch.setattr(mod, "get_rag_mcp_client", lambda: fake)

        out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
        assert "error" in out
        assert "MCP_INVALID_RESPONSE" in out["error"]

    def test_missing_score_returns_error_json(self, monkeypatch):
        from app.tools.interface_dict_tools import search_interface_dictionary
        import app.tools.interface_dict_tools as mod

        fake = MagicMock()
        fake.call_tool.return_value = {"matches": [{"text": "hello", "title": "x"}]}
        monkeypatch.setattr(mod, "get_rag_mcp_client", lambda: fake)

        out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
        assert "error" in out
        assert "MCP_INVALID_RESPONSE" in out["error"]

    def test_non_mcp_exception_propagates_without_fallback(self, monkeypatch):
        """非 MCPBoundaryError（真程序 bug）→ 向上抛。"""
        from app.tools.interface_dict_tools import search_interface_dictionary
        import app.tools.interface_dict_tools as mod

        fake = MagicMock()
        fake.call_tool.side_effect = RuntimeError("parse bug not related to MCP boundary")
        monkeypatch.setattr(mod, "get_rag_mcp_client", lambda: fake)

        with pytest.raises(RuntimeError, match="parse bug"):
            search_interface_dictionary.invoke({"query": "x"})

    def test_matches_not_list_returns_error_json(self, monkeypatch):
        from app.tools.interface_dict_tools import search_interface_dictionary
        import app.tools.interface_dict_tools as mod

        fake = MagicMock()
        fake.call_tool.return_value = {"matches": "not a list"}
        monkeypatch.setattr(mod, "get_rag_mcp_client", lambda: fake)

        out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
        assert "error" in out
        assert "MCP_INVALID_RESPONSE" in out["error"]
