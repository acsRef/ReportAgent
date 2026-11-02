"""report_graph 工具菜单与执行路由的一致性测试（B4 执行路由承诺）。

report_graph 的规划 prompt 列出可选工具 → LLM 选一个 → _run_step 分发执行。
菜单里能选的工具必须真的能被 _run_step 分发；否则模型选中后会静默落入
chart_advisor 兜底，报告内容悄悄变错却没有任何报错。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke

ANALYSIS_TOOLS = [
    "chart_advisor",
    "insight_analyst",
    "trend_analysis",
    "group_compare",
    "detect_anomaly",
]


async def test_report_graph_menu_tools_are_dispatchable(monkeypatch) -> None:
    """report_graph 的规划 prompt 必须列出全部 5 个工具（含 insight_analyst），
    且 _run_step 能分发 insight_analyst——菜单与执行不允许漂移。"""
    import app.agent.report_graph as rg

    captured: list[str] = []

    def fake_call_llm(prompt, *a, **k):
        captured.append(prompt)
        return '{"steps": [{"tool": "insight_analyst", "args": {}, "description": "摘要"}], "reasoning": ""}'

    monkeypatch.setattr(rg, "call_llm", fake_call_llm)

    qr = {
        "sql": "SELECT 1",
        "columns": [{"name": "region"}, {"name": "total"}],
        "rows": [{"region": "华东", "total": 100}, {"region": "华北", "total": 80}],
        "row_count": 2,
        "status": "SUCCESS",
    }
    result = await rg._plan_analysis({"query_result": qr, "user_query": "x"})  # P16: 节点含 async（ContextRuntime 接入）
    assert captured, "plan prompt 未捕获"
    prompt = captured[0]
    for name in ANALYSIS_TOOLS:
        assert f"- {name}:" in prompt, f"report 菜单缺少 {name}"

    # 菜单里能选的工具必须能执行：insight_analyst 分发不落入兜底 chart_advisor
    called: list[str] = []
    fallback: list[str] = []
    monkeypatch.setattr(rg, "insight_analyst", lambda data: called.append("insight") or "销售额: 合计=180")
    monkeypatch.setattr(rg, "chart_advisor", lambda data: fallback.append("chart") or "{}")
    state = {
        "query_result": qr,
        "assemble_plan": result["assemble_plan"],
        "assemble_step_idx": 0,
        "assemble_results": [],
    }
    rg._run_step(state)
    assert called == ["insight"], "insight_analyst 未被分发执行"
    assert not fallback, "insight_analyst 静默落入 chart_advisor 兜底"
