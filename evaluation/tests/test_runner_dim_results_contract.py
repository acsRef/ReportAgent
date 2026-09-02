"""run_case error path dim_results shape 一致性测试。

P14 P3 闭环：异常路径（status='error'）的 result['dim_results'] 必须与正常路径
同形 —— 11 slot 完整（9 DIM_REGISTRY + 4 legacy - 2 重叠 = 11），每 slot 含
{pass, fail, deferred} 三个字段。regression evaluator 不应处理两套格式。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from evaluation.schema import BaselineCase, TurnSpec
from evaluation.runner import run_case


def _fake_sse_gen(_events):
    """生成一段 mock SSE 流。"""
    for ev in _events:
        yield {"event": ev["event"], "data": ev["data"]}


def test_error_path_dim_results_has_full_11_slots():
    """模拟 run_case 在 turn 中抛异常 → dim_results 仍是完整 11-slot dict。"""
    case = BaselineCase.model_validate({
        "id": "p14-error-path-dim-results",
        "category": "explicit_query",
        "description": "强制异常以触发 error path",
        "turns": [{"query": "t", "mode": "new"}],
        "expectations": [{
            "requirement": {"status": "complete"},
            "memory": {"recalled": True},  # dynamic dim key（P14 P0 闭环）
        }],
    })

    client = MagicMock()
    client.stream.side_effect = RuntimeError("simulated network error")

    result = run_case(case, client, "fake-token")

    assert result["status"] == "error", f"expected status=error, got {result.get('status')}"
    assert "dim_results" in result, "P14 P3 失守：error path 缺 dim_results"

    dim_results = result["dim_results"]
    # 期望 11 slot：9 DIM_REGISTRY + 4 legacy - 2 重叠（requirement/report）
    expected_dims = {
        "requirement", "memory", "retrieval",
        "tool_selection", "sql", "repair", "report",
        "frontend", "e2e",
        "execution", "behavior",
    }
    actual_dims = set(dim_results.keys())
    assert actual_dims == expected_dims, (
        f"dim_results shape 不一致：missing={expected_dims - actual_dims}, "
        f"extra={actual_dims - expected_dims}"
    )

    # 每个 slot 都应是 {pass: int, fail: int, deferred: int}
    for dim, slot in dim_results.items():
        assert isinstance(slot, dict), f"{dim} slot 不是 dict: {type(slot)}"
        assert set(slot.keys()) == {"pass", "fail", "deferred"}, (
            f"{dim} slot 字段不全: {set(slot.keys())}"
        )
        for k, v in slot.items():
            assert isinstance(v, int), f"{dim}.{k} 不是 int: {type(v)}"

    # 异常 path 时 sections_all 是空 → 所有 dim 都应是 0/0/0
    for dim, slot in dim_results.items():
        assert slot == {"pass": 0, "fail": 0, "deferred": 0}, (
            f"{dim} slot 应全 0，got {slot}"
        )


def test_success_path_dim_results_has_full_11_slots():
    """正常路径 dim_results 也是完整 11-slot dict（与 error path 同形）。"""
    case = BaselineCase.model_validate({
        "id": "p14-success-path-dim-results",
        "category": "explicit_query",
        "description": "正常流",
        "turns": [{"query": "t", "mode": "new"}],
        "expectations": [{
            "requirement": {"status": "complete"},
        }],
    })

    client = MagicMock()
    # 让 _stream_sse 走 mock
    fake_stream = MagicMock()
    fake_stream.__enter__ = lambda self: self
    fake_stream.__exit__ = lambda self, *args: None
    fake_stream.iter_lines = MagicMock(return_value=iter([
        "event: requirement",
        f'data: {{\"status\": \"complete\"}}',
        "",
        "event: done",
        f'data: {{\"success\": true}}',
        "",
    ]))
    fake_stream.raise_for_status = lambda: None
    client.stream.return_value = fake_stream

    # session GET 返回空 versions → 跳过 detail
    session_resp = MagicMock()
    session_resp.status_code = 200
    session_resp.json.return_value = {"session": {"report_versions": []}}
    client.get.return_value = session_resp

    result = run_case(case, client, "fake-token")

    dim_results = result["dim_results"]
    expected_dims = {
        "requirement", "memory", "retrieval",
        "tool_selection", "sql", "repair", "report",
        "frontend", "e2e",
        "execution", "behavior",
    }
    assert set(dim_results.keys()) == expected_dims, (
        f"success path dim_results shape 不一致"
    )
    # 每个 slot 都应是 {pass, fail, deferred}
    for dim, slot in dim_results.items():
        assert set(slot.keys()) == {"pass", "fail", "deferred"}
