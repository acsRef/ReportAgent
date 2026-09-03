"""MCP 三码业务层 matrix（P15 reliability 收口 ⑥，consolidated）。

把散落在 test_reliability_errors / test_rag_schema 的 MCP 边界钉子收成一张
「业务降级 contract」表，逐码钉：

    code                    | classification (code/kind/recoverable) | dispatcher (HTTP fallback?) | tool 层降级
    MCP_TIMEOUT             | MCP_TIMEOUT / timeout / recoverable    | 仅 _fallback_allowed()      | search→[] / ddl→None + WARNING
    MCP_UNAVAILABLE         | MCP_UNAVAILABLE / connection / 非 rec  | 仅 _fallback_allowed()      | 同上
    MCP_INVALID_RESPONSE    | MCP_INVALID_RESPONSE / other / 非 rec  | 永不（re-raise）            | 同上
    clean no-match ([])     | 无 error（EMPTY_RESULT 合法）           | —                          | [] **无** WARNING

核心 anti-flattening 断言：MCP 三码在分类层**不互相同形**（recoverable 有别、SSE 出口码有
别）；INVALID ≠ no-match（no-match 是合法 [] 静默通过校验，INVALID 是协议错必须 re-raise，
即便 fallback 允许也不得吞）；MCP 错误降级为 [] 的路径**必落 WARNING 带 code**（与 no-match
的静默区分），供日志/观测层区分「真的没表」vs「MCP 挂了」。

注意：schema 检索到工具层把三码扁平为 []/None 是**故意降级**（设计如此，见 rag_schema
docstring——SQL 生成不因 schema 检索失败阻塞）；本 matrix 钉的是这个降级「不静默」的部分
（WARNING 带 code + 分类/策略不互相吞），以及它永远不会伪造 SUCCESS 的上游保证由
`mcp_down_execution` live case（③）承担。
"""
from __future__ import annotations

import logging

import pytest

from app.reliability.errors import classify_exception, classify_mcp_error
from app.tools import rag_schema
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

pytestmark = pytest.mark.contracts


def _mock_mcp(monkeypatch, *, side_effect):
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.call_tool.side_effect = side_effect
    monkeypatch.setattr(rag_schema, "get_rag_mcp_client", lambda: fake)
    return fake


# --- 分类层：三码 distinct（envelope 级 matrix，逐码不互相同形） -------------


@pytest.mark.parametrize(
    ("code", "expect_code", "expect_kind", "expect_recoverable"),
    [
        (MCPErrorCode.MCP_TIMEOUT, "MCP_TIMEOUT", "timeout", True),
        (MCPErrorCode.MCP_UNAVAILABLE, "MCP_UNAVAILABLE", "connection", False),
        (MCPErrorCode.MCP_INVALID_RESPONSE, "MCP_INVALID_RESPONSE", "other", False),
    ],
)
def test_classification_matrix(code, expect_code, expect_kind, expect_recoverable):
    env = classify_mcp_error(MCPBoundaryError(code, "detail"))
    assert (env.code, env.kind, env.recoverable) == (
        expect_code, expect_kind, expect_recoverable,
    ), "三码在分类层必须 distinct（recoverable 有别 = retry 判定有别）"


# --- SSE 出口：若 MCP 错从工具层逃逸，用户码是 MCP_* 而非 QUERY_* 混淆 ---------


@pytest.mark.parametrize(
    ("code", "expect_code"),
    [
        (MCPErrorCode.MCP_TIMEOUT, "MCP_TIMEOUT"),
        (MCPErrorCode.MCP_UNAVAILABLE, "MCP_UNAVAILABLE"),
        (MCPErrorCode.MCP_INVALID_RESPONSE, "MCP_INVALID_RESPONSE"),
    ],
)
def test_escape_classification_via_classify_exception(code, expect_code):
    env = classify_exception(MCPBoundaryError(code, "boom"))
    assert env.code == expect_code


# --- dispatcher：INVALID 永不 HTTP fallback；UNAVAILABLE/TIMEOUT 仅 flag 允许时 ----


def _patch_http_no_fallback(monkeypatch):
    """HTTP fallback 若被调用即失败（钉「不该走」的路径）。"""
    monkeypatch.setattr(
        rag_schema, "_retrieve_dict_http",
        lambda q, top_k: (_ for _ in ()).throw(AssertionError("不应走 HTTP fallback")),
    )


def test_dispatcher_invalid_never_falls_back(monkeypatch):
    """MCP_INVALID_RESPONSE 即便 fallback 允许也 re-raise（重试/回退同结果，无信息增益）。"""
    monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: True)
    _mock_mcp(monkeypatch, side_effect=MCPBoundaryError(
        MCPErrorCode.MCP_INVALID_RESPONSE, "bad json"))
    _patch_http_no_fallback(monkeypatch)
    with pytest.raises(MCPBoundaryError) as ei:
        rag_schema._retrieve_dict("销售额", 3)
    assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE


@pytest.mark.parametrize("code", [MCPErrorCode.MCP_TIMEOUT, MCPErrorCode.MCP_UNAVAILABLE])
def test_dispatcher_unavailable_timeout_fallback_only_if_allowed(monkeypatch, code):
    """UNAVAILABLE/TIMEOUT：flag 锁定 → re-raise；flag 允许 → HTTP fallback。"""
    # flag 锁定 → 上抛
    monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: False)
    _mock_mcp(monkeypatch, side_effect=MCPBoundaryError(code, "down"))
    with pytest.raises(MCPBoundaryError) as ei:
        rag_schema._retrieve_dict("销售额", 3)
    assert ei.value.code is code
    # flag 允许 → HTTP fallback 被调
    monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: True)
    called = []
    monkeypatch.setattr(
        rag_schema, "_retrieve_dict_http",
        lambda q, top_k: called.append((q, top_k)) or [{"text": "x", "score": 0.5}],
    )
    items = rag_schema._retrieve_dict("销售额", 3)
    assert called, "UNAVAILABLE/TIMEOUT + flag 允许必须走 HTTP fallback"


# --- no-match vs MCP 错误：静默 [] 是合法无结果；MCP 错降级必带 WARNING --------


def test_clean_no_match_is_silent_and_valid(monkeypatch, caplog):
    """干净无命中（matches=[]）→ [] 且无 WARNING（≠ MCP 错误路径的降级）。"""
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.call_tool.return_value = {"matches": []}
    monkeypatch.setattr(rag_schema, "get_rag_mcp_client", lambda: fake)
    with caplog.at_level(logging.WARNING, logger="app.tools.rag_schema"):
        rows = rag_schema.search_tables_from_rag("销售额")
    assert rows == []
    assert not any("schema search from rag failed" in r.message for r in caplog.records), (
        "no-match 是合法 EMPTY_RESULT，不得落 error 日志"
    )


@pytest.mark.parametrize(
    "code", [MCPErrorCode.MCP_TIMEOUT, MCPErrorCode.MCP_UNAVAILABLE,
             MCPErrorCode.MCP_INVALID_RESPONSE],
)
def test_mcp_error_degradation_logs_code(monkeypatch, caplog, code):
    """MCP 错降级为 [] 必落 WARNING 且带 code（观测层可区分 MCP 挂 vs 真没表）。"""
    monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: False)
    _mock_mcp(monkeypatch, side_effect=MCPBoundaryError(code, "boom"))
    with caplog.at_level(logging.WARNING, logger="app.tools.rag_schema"):
        rows = rag_schema.search_tables_from_rag("销售额")
    assert rows == [], "schema 检索失败按契约降级为 []（不阻塞 SQL 生成）"
    assert any(code.value in r.message for r in caplog.records), (
        f"降级日志必须带 {code.value}（区别 no-match 的静默）"
    )
