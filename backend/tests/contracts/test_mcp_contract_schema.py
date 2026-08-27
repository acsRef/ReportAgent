"""P2 MCP Schema Contract Freeze（docs/plans/2026-08-26-p2-rag-mcp-boundary.md 决策 5 钉子 3）。

与 Task 2 已有测试的分工：
- test_mcp_client.py::TestValidateMatchesContract —— helper 函数级行为（14 例，
  「validator 逻辑对不对」）；
- 本文件 —— **契约面冻结**（「稳定/内部字段表这个架构不变量还在不在」）：
  1. `_INTERNAL_RESULT_FIELDS` 快照冻结（同 LEGACY BRIDGE 快照手法）——
     任何增删必须显式改快照并同步 Task 4 mcp-contract.md 字段表；
  2. 完整 boundary 链（`call_tool` → `_validate_matches_contract` 组合，
     即 rag_schema/interface_dict_tools/faq_tools 实际使用的通路）端到端：
     样本含内部字段 → 输出只剩稳定契约字段；缺 text/score → INVALID_RESPONSE。
离线可跑：monkeypatch `_call_with_retry`（Task 2 已验证该 patch 点），不起真子进程。
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.contracts

from app.tools.mcp_client import (
    RagMCPClient,
    _INTERNAL_RESULT_FIELDS,
    _validate_matches_contract,
)
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode


# plan 决策 3 表格 + Task 2 review 第 3 轮 P1 修订的唯一真相源。
# 这些是 ragent-py 内部字段：boundary strip，tool 层永不可见。
FROZEN_INTERNAL_FIELDS: frozenset[str] = frozenset(
    {
        "chunk_id",
        "document_id",
        "embedding",
        "rerank_score",
        "kb_id",
    }
)

# 稳定契约（plan 决策 3）：items[] = {text, score, title?, section_path?}；
# FAQ 通道另含 question（Task 2 review 第 2 轮 Tool Contract 统一）。
STABLE_FIELDS: frozenset[str] = frozenset(
    {"text", "score", "title", "section_path", "question", "source"}
)


def _client_returning(raw_text: str, monkeypatch) -> RagMCPClient:
    """构造 call_tool 直接返回 raw_text 的 client（跳过 transport）。

    Task 2 已验证 `_call_with_retry` 是稳定 patch 点；分类器与之后的
    `_validate_matches_contract` 走真实实现。
    """
    client = RagMCPClient()
    monkeypatch.setattr(client, "_call_with_retry", lambda name, args: raw_text)
    return client


def _boundary_call(raw_text: str, monkeypatch) -> list[dict]:
    """完整 boundary 链：call_tool（分类器）→ _validate_matches_contract。

    这正是 tool 层 `_via_mcp` 函数的调用组合（rag_schema.py:30 /
    interface_dict_tools.py:27 / faq_tools.py:9 的 import 面）。
    """
    client = _client_returning(raw_text, monkeypatch)
    result = client.call_tool("search_dictionary", {"query": "x", "top_k": 3})
    return _validate_matches_contract(result)


# ---------------------------------------------------------------------------
# 钉子 1：内部字段 denylist 快照冻结
# ---------------------------------------------------------------------------


def test_internal_fields_denylist_snapshot():
    """`_INTERNAL_RESULT_FIELDS` 必须恰好等于冻结快照——这是 plan 决策 3
    「response（内部字段，禁止依赖）」表的代码化身。

    悄悄往快照里加字段 = 把本该 strip 的内部字段透出到 tool 层（泄漏）；
    悄悄删字段 = 内部字段透传（ragent-py 实现细节变成隐式跨仓契约）。
    两个方向都要求显式改本快照并同步 Task 4 mcp-contract.md 字段表。
    （快照手法同 P1 LEGACY BRIDGE imports 冻结。）
    """
    assert _INTERNAL_RESULT_FIELDS == FROZEN_INTERNAL_FIELDS, (
        f"内部字段 denylist 快照漂移:\n"
        f"  代码: {sorted(_INTERNAL_RESULT_FIELDS)}\n"
        f"  冻结: {sorted(FROZEN_INTERNAL_FIELDS)}\n"
        f"  多出: {sorted(_INTERNAL_RESULT_FIELDS - FROZEN_INTERNAL_FIELDS)}\n"
        f"  缺失: {sorted(FROZEN_INTERNAL_FIELDS - _INTERNAL_RESULT_FIELDS)}\n"
        "变更须同步 docs/architecture/mcp-contract.md 字段表（Task 4）。"
    )


def test_stable_and_internal_field_sets_disjoint():
    """稳定契约字段与内部字段必须不相交——同一字段既「Agent 可依赖」又
    「boundary strip」是契约自相矛盾（Task 4 文档表格的不变前提）。"""
    overlap = STABLE_FIELDS & _INTERNAL_RESULT_FIELDS
    assert not overlap, f"字段同时出现在稳定表与内部表（契约矛盾）: {sorted(overlap)}"


# ---------------------------------------------------------------------------
# 钉子 2：端到端——内部字段在完整 boundary 链后被剥净
# ---------------------------------------------------------------------------


def test_full_boundary_strips_internal_fields(mock_session_sample_with_internal_fields, monkeypatch):
    """真实形态样本（稳定字段 + 内部字段混杂）经完整 boundary 链
    （call_tool 分类器 → _validate_matches_contract 规范化）后，
    每个 item 的键集必须恰好是稳定字段——chunk_id/document_id/embedding/
    rerank_score/kb_id 一个都不许透到 tool 层。

    plan 决策 5 钉子 3 原文：「mock MCP session 返回带内部字段的样本 →
    断言规范化后只剩稳定字段」。
    """
    items = _boundary_call(mock_session_sample_with_internal_fields, monkeypatch)
    assert items, "样本应至少命中一条"
    for i, item in enumerate(items):
        leaked = set(item) & _INTERNAL_RESULT_FIELDS
        assert not leaked, f"matches[{i}] 泄漏内部字段: {sorted(leaked)}"
        unknown = set(item) - STABLE_FIELDS
        assert not unknown, (
            f"matches[{i}] 出现稳定表之外字段: {sorted(unknown)}"
            "（新增稳定字段须同步决策 3 表格与本测试 STABLE_FIELDS）"
        )
        # 稳定字段值不许在 strip 过程中被篡改
        assert item["text"] and isinstance(item["score"], (int, float))


@pytest.fixture
def mock_session_sample_with_internal_fields() -> str:
    """ragent-py search_dictionary 真实响应形态：每个 match 同时携带
    稳定字段（text/score/title/section_path）与内部字段（chunk_id/...）。"""
    return json.dumps(
        {
            "matches": [
                {
                    "text": "# 表 `public.fact_sales`",
                    "score": 0.87,
                    "title": "fact_sales",
                    "section_path": "字典库/表结构",
                    "chunk_id": "c-123",
                    "document_id": "d-456",
                    "embedding": [0.01, 0.02, 0.03],
                    "rerank_score": 0.95,
                    "kb_id": "kb-dict",
                }
            ],
            "degraded": False,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# 钉子 3：端到端——缺 text / 缺 score → MCP_INVALID_RESPONSE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_item,why",
    [
        ({"score": 0.5}, "缺 text"),
        ({"text": "片段"}, "缺 score"),
        ({"text": 123, "score": 0.5}, "text 非 str"),
        ({"text": "片段", "score": None}, "score 为 None"),
    ],
)
def test_full_boundary_missing_stable_field_raises_invalid(bad_item, why, monkeypatch):
    """稳定契约字段缺失/类型错时，完整 boundary 链必须抛
    MCPBoundaryError(MCP_INVALID_RESPONSE)——不许静默降级成少字段返回或空数组
    （plan 决策 5 钉子 3 原文：「缺 text/score → 断言 MCP_INVALID_RESPONSE」）。

    参数化四例都是 Tool Contract 违约形态：Agent 拿到缺 score 的 item 无法排序，
    拿到缺 text 的 item 无法注入 context——宁可显式失败不可带病透传。
    """
    raw = json.dumps({"matches": [bad_item]}, ensure_ascii=False)
    with pytest.raises(MCPBoundaryError) as excinfo:
        _boundary_call(raw, monkeypatch)
    assert excinfo.value.code is MCPErrorCode.MCP_INVALID_RESPONSE, (
        f"{why}: 期望 MCP_INVALID_RESPONSE，实得 {excinfo.value.code}"
    )


# ---------------------------------------------------------------------------
# 钉子 4：EMPTY_RESULT 端到端合法——与 INVALID/UNAVAILABLE 严格区分
# ---------------------------------------------------------------------------


def test_full_boundary_empty_matches_is_legal(monkeypatch):
    """{"matches": []} 经完整 boundary 链返回 [] 不抛——合法零命中
    （EMPTY_RESULT）与协议错（缺 matches → INVALID）的分界是 Task 2 review
    第 3 轮 P1 钉死的行为，本钉子把它冻结在端到端层面：
    将来谁把 `matches: []` 也当成 INVALID（过度收紧）或把缺字段放行成 []
    （回退到静默空数组），这里都会红。"""
    assert _boundary_call(json.dumps({"matches": []}), monkeypatch) == []


def test_full_boundary_schema_drift_not_disguised_as_empty(monkeypatch):
    """`{}` / {"results": [...]} 等 schema drift 不许被当成合法空命中
    （宪法 §7：MCP 失败不许默默返回空数组伪装"没结果"）——端到端必须 INVALID。"""
    for drift in ("{}", '{"results": []}'):
        with pytest.raises(MCPBoundaryError) as excinfo:
            _boundary_call(drift, monkeypatch)
        assert excinfo.value.code is MCPErrorCode.MCP_INVALID_RESPONSE, (
            f"schema drift {drift!r} 未被识别为 INVALID（被伪装成空命中？）"
        )
