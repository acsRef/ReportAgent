"""P2 MCP Failure Semantics Matrix（docs/plans/2026-08-26-p2-rag-mcp-boundary.md 决策 5 钉子 4）。

4 错误类 × 2 flag 状态 = 8 格矩阵。每格钉三件事：
- **真实 transport 调用次数**（仅 TIMEOUT 触发 retry 预算 2，宪法 §11 固定值）
- **HTTP fallback 调用次数**（0 或 1）
- **最终返回值 / 上抛 error code**

与 Task 2 dispatcher tests（test_rag_schema.py::TestSearchTablesDispatcher 等）
的分工：后者测「一次调用走对路径」（UNAVAILABLE×flag-off、INVALID×flag-on、EMPTY
等覆盖），本文件覆盖矩阵剩余 cell + retry 预算 + fallback 闸门的端到端钉死：
- 新增 TIMEOUT × {flag off, on} 两格（Task 2 未覆盖）
- 新增 INVALID_RESPONSE × flag off 一格（Task 2 只测了 flag on）
- 新增 transport 调用次数断言（Task 2 未做）
- 8 格用 parametrize 一次性穷举，避免漏 cell

边界归属：`_fallback_allowed` flag 与 `_retrieve_dict_http` HTTP fallback 都是
rag_schema 模块级名字，monkeypatch 锚点稳定（Task 2 沿用同样模式）。
transport retry 计数锚点是 mcp_client singleton 的 `_do_call`（Task 1 review
第 4 轮已验证「retry 在 _call_with_retry 内发生」）。

离线可跑：纯 monkeypatch + 计数 mock，不触真子进程/PG/LLM。
"""
from __future__ import annotations

import pytest

from app.tools import rag_schema
from app.tools.mcp_client import get_rag_mcp_client
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

pytestmark = pytest.mark.smoke


class _CountingCall:
    """计数 + 可选返回/上抛。每次调用计数 +1。"""

    def __init__(self, *, return_value=None, raise_value=None):
        self.calls = 0
        self.args_log: list[tuple] = []
        self.return_value = return_value
        self.raise_value = raise_value

    def __call__(self, *args, **kwargs):
        self.calls += 1
        self.args_log.append(args)
        if self.raise_value is not None:
            raise self.raise_value
        return self.return_value


def _set_flag(monkeypatch, on: bool) -> None:
    """PHASE2_MCP_ONLY 锁定时 `_fallback_allowed()` 返回 False。"""
    monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: not on)


def _patch_transport(monkeypatch, *, return_value=None, raise_value=None) -> _CountingCall:
    """patch 真实 transport `_do_call`（retry 计数锚点）。"""
    client = get_rag_mcp_client()
    mock = _CountingCall(return_value=return_value, raise_value=raise_value)
    monkeypatch.setattr(client, "_do_call", mock)
    return mock


def _patch_fallback(monkeypatch, return_value) -> _CountingCall:
    """patch HTTP fallback `_retrieve_dict_http`。"""
    mock = _CountingCall(return_value=return_value)
    monkeypatch.setattr(rag_schema, "_retrieve_dict_http", mock)
    return mock


# ---------------------------------------------------------------------------
# 钉子：4 错误类 × 2 flag 状态 = 8 格矩阵
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag_on,transport_raise,transport_return,fallback_return,"
    "expected_transport_calls,expected_fallback_calls,"
    "expect_error_code,expect_return,cell_id",
    [
        # ── TIMEOUT × {flag off, on} ──
        # plan 决策 4：MCP_TIMEOUT 重试预算内 retry（固定 2 次，宪法 §11）；
        # 仍败 → flag off 走 fallback / flag on 显式 unavailable 上抛。
        (
            False,
            MCPBoundaryError(MCPErrorCode.MCP_TIMEOUT, "boom"),
            None,
            [{"table_name": "fallback_table"}],
            2, 1, None, [{"table_name": "fallback_table"}],
            "TIMEOUT × flag OFF",
        ),
        (
            True,
            MCPBoundaryError(MCPErrorCode.MCP_TIMEOUT, "boom"),
            None,
            [{"table_name": "fallback_table"}],
            2, 0, MCPErrorCode.MCP_TIMEOUT, None,
            "TIMEOUT × flag ON",
        ),
        # ── UNAVAILABLE × {flag off, on} ──
        # 连接/握手失败；plan 决策 4：不 retry（仅 TIMEOUT 触发），
        # flag off 走 fallback，flag on 显式上抛。
        (
            False,
            MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "boom"),
            None,
            [{"table_name": "fallback_table"}],
            1, 1, None, [{"table_name": "fallback_table"}],
            "UNAVAILABLE × flag OFF",
        ),
        (
            True,
            MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "boom"),
            None,
            [{"table_name": "fallback_table"}],
            1, 0, MCPErrorCode.MCP_UNAVAILABLE, None,
            "UNAVAILABLE × flag ON",
        ),
        # ── INVALID_RESPONSE × {flag off, on} ──
        # plan 决策 4：不 retry（_call_with_retry 仅 MCP_TIMEOUT 触发）+ 不 fallback
        # （dispatcher 显式分支直接 raise）→ 两态都是显式上抛。
        # 注意：与 UNAVAILABLE 不同——flag OFF 也不走 HTTP fallback（rag_schema:141
        # 显式分支「INVALID 直接 raise」，不读 flag）。
        (
            False,
            MCPBoundaryError(MCPErrorCode.MCP_INVALID_RESPONSE, "boom"),
            None,
            [{"table_name": "fallback_table"}],
            1, 0, MCPErrorCode.MCP_INVALID_RESPONSE, None,
            "INVALID_RESPONSE × flag OFF",
        ),
        (
            True,
            MCPBoundaryError(MCPErrorCode.MCP_INVALID_RESPONSE, "boom"),
            None,
            [{"table_name": "fallback_table"}],
            1, 0, MCPErrorCode.MCP_INVALID_RESPONSE, None,
            "INVALID_RESPONSE × flag ON",
        ),
        # ── EMPTY_RESULT × {flag off, on} ──
        # 合法零命中（matches=[]）；两态都返回 []，不触发 fallback。
        (
            False,
            None,
            '{"matches": []}',
            [{"table_name": "fallback_table"}],
            1, 0, None, [],
            "EMPTY × flag OFF",
        ),
        (
            True,
            None,
            '{"matches": []}',
            [{"table_name": "fallback_table"}],
            1, 0, None, [],
            "EMPTY × flag ON",
        ),
    ],
)
def test_failure_semantics_cell(
    monkeypatch,
    flag_on,
    transport_raise,
    transport_return,
    fallback_return,
    expected_transport_calls,
    expected_fallback_calls,
    expect_error_code,
    expect_return,
    cell_id,
):
    """8 格矩阵。每格钉：retry/fallback 计数 + 最终行为。"""
    _set_flag(monkeypatch, flag_on)
    transport_mock = _patch_transport(
        monkeypatch,
        return_value=transport_return,
        raise_value=transport_raise,
    )
    fallback_mock = _patch_fallback(monkeypatch, return_value=fallback_return)

    if expect_error_code is not None:
        with pytest.raises(MCPBoundaryError) as excinfo:
            rag_schema._retrieve_dict("q", 3)
        assert excinfo.value.code is expect_error_code, (
            f"{cell_id}: 期望 {expect_error_code}, 实得 {excinfo.value.code}"
        )
    else:
        result = rag_schema._retrieve_dict("q", 3)
        assert result == expect_return, (
            f"{cell_id}: 期望 {expect_return}, 实得 {result}"
        )

    assert transport_mock.calls == expected_transport_calls, (
        f"{cell_id}: transport 调用次数 期望 {expected_transport_calls}, "
        f"实际 {transport_mock.calls}"
    )
    assert fallback_mock.calls == expected_fallback_calls, (
        f"{cell_id}: HTTP fallback 调用次数 期望 {expected_fallback_calls}, "
        f"实际 {fallback_mock.calls}"
    )


# ---------------------------------------------------------------------------
# 钉子：retry 预算硬上限（宪法 §11：MCP 固定 2 次）
# ---------------------------------------------------------------------------


def test_retry_budget_caps_at_two_on_timeout(monkeypatch):
    """TIMEOUT 重试预算上限 = 2（宪法 §11「MCP 2」固定值，plan 决策 4）。

    把 _call_with_retry 内 `for attempt in (1, 2)` 改成 `(1, 3)` 或 `(1,)` 都
    会让本测试红——防止有人悄悄改 retry 预算（直接收紧会破 SLA，放宽会放大
    latency P99 + 在 MCP 故障时阻塞整条链路）。
    """
    _set_flag(monkeypatch, on=True)  # 锁 flag，让 MCP_TIMEOUT 一定上抛不 fallback
    transport_mock = _patch_transport(
        monkeypatch,
        raise_value=MCPBoundaryError(MCPErrorCode.MCP_TIMEOUT, "boom"),
    )

    with pytest.raises(MCPBoundaryError) as excinfo:
        rag_schema._retrieve_dict("q", 3)
    assert excinfo.value.code is MCPErrorCode.MCP_TIMEOUT

    assert transport_mock.calls == 2, (
        f"MCP retry 预算固定 2 次（宪法 §11），实际 {transport_mock.calls}"
    )
