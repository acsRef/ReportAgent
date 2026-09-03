"""P15 e2e 扩充：MCP-down seam 契约。

钉：双 gate fail-closed（REPORTAGENT_E2E=1 且 header）、request-scoped contextvar
（scoped 退出恢复、后台任务继承语义）、以及 seam 激活时 `_retrieve_dict_via_mcp` 在调
MCP **之前** raise MCPBoundaryError(MCP_UNAVAILABLE)——走既有 graceful 降级
（search_tables→[] / get_table_ddl→None），不新增绕过旁路。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.reliability import mcp_down
from app.tools import rag_schema
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

pytestmark = pytest.mark.contracts


def test_gate_fail_closed(monkeypatch):
    """REPORTAGENT_E2E 未设 → header 再合法也返回 False / 不注入（生产零行为变化）。"""
    monkeypatch.delenv("REPORTAGENT_E2E", raising=False)
    assert mcp_down.parse_header("on") is False
    with mcp_down.scoped(True):
        assert mcp_down.active() is False, "gate 关时 scoped(True) 也不得激活"


def test_parse_header_allows_truthy(monkeypatch):
    monkeypatch.setenv("REPORTAGENT_E2E", "1")
    for v in ("on", "1", "true", "ON", " yes "):
        assert mcp_down.parse_header(v) is True, f"{v!r} 应解析为激活"
    assert mcp_down.parse_header("") is False
    assert mcp_down.parse_header("2") is False
    assert mcp_down.parse_header("off") is False


def test_scoped_request_local(monkeypatch):
    """scoped 只在本请求 context 生效，退出恢复（不泄漏到并发请求/后续调用）。"""
    monkeypatch.setenv("REPORTAGENT_E2E", "1")
    assert mcp_down.active() is False
    with mcp_down.scoped(True):
        assert mcp_down.active() is True
    assert mcp_down.active() is False, "scoped 退出必须复位"


def test_seam_raises_before_mcp_and_degrades_gracefully(monkeypatch):
    """seam 激活 → 调 MCP 前 raise MCP_UNAVAILABLE → search_tables→[] / ddl→None。

    核心：注入形态与真实 MCP 中断同一错误分类，走既有 graceful 降级；MCP client
    的 call_tool 不得被调用（证明不是 mock 掉 MCP，而是在边界前抛错）。
    """
    monkeypatch.setenv("REPORTAGENT_E2E", "1")
    fake = MagicMock()
    monkeypatch.setattr(rag_schema, "get_rag_mcp_client", lambda: fake)
    # P5 收口默认：flag 锁定（无 HTTP fallback）→ 降级为 []/None
    monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: False)

    with mcp_down.scoped(True):
        rows = rag_schema.search_tables_from_rag("销售额")
        ddl = rag_schema.get_table_ddl_from_rag("fact_sales")
    assert rows == [], "MCP down 时 search_tables 应 graceful 降级为空"
    assert ddl is None, "MCP down 时 get_table_ddl 应 graceful 降级为 None"
    assert not fake.call_tool.called, "seam 必须在调用 MCP 之前 raise（不得消耗 MCP 预算）"

    # 边界层本身 raise 的是 MCP_UNAVAILABLE 分类（上层可据此显式降级，非静默空）
    with mcp_down.scoped(True):
        with pytest.raises(MCPBoundaryError) as ei:
            rag_schema._retrieve_dict_via_mcp("销售额", 3)
    assert ei.value.code == MCPErrorCode.MCP_UNAVAILABLE


def test_fail_closed_keeps_real_path(monkeypatch):
    """gate 开但 seam 未 scoped（无 header）→ 正常走 MCP，不注入。"""
    monkeypatch.setenv("REPORTAGENT_E2E", "1")
    fake = MagicMock()
    fake.call_tool.return_value = {
        "matches": [{
            "text": "# 表 public.fact_sales\n销售事实表\n## 字段\n"
                    "字段 sale_id 类型 integer 含义 主键",
            "score": 0.9,
        }],
    }
    monkeypatch.setattr(rag_schema, "get_rag_mcp_client", lambda: fake)
    rows = rag_schema.search_tables_from_rag("销售额")
    assert len(rows) == 1 and rows[0]["table_name"] == "fact_sales"
    assert fake.call_tool.called, "非 seam 请求必须真调 MCP"
