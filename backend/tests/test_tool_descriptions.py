"""工具描述质量测试。

工具描述是模型选择工具的主要依据：_format_tools_for_prompt 必须渲染
完整五要素描述（用途/输入/输出/适用/「不要用来 → 替代工具」），
绝不能退化成首行截断的简略版。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke

from app.llm import _format_tools_for_prompt

ANALYSIS_TOOLS = [
    "chart_advisor",
    "insight_analyst",
    "trend_analysis",
    "group_compare",
    "detect_anomaly",
]


def test_default_whitelist_renders_all_analysis_tools() -> None:
    block = _format_tools_for_prompt()
    for name in ANALYSIS_TOOLS:
        assert f"- {name}:" in block


def test_descriptions_are_full_five_element_not_first_line() -> None:
    """完整描述必须含输入/输出与「不要用来」边界——首行截断会丢失这些。"""
    block = _format_tools_for_prompt()
    assert "输入" in block
    assert "输出" in block
    assert "不要用来" in block
    # 边界条件必须指向替代工具（消歧的关键）
    assert "用 get_table_ddl" in block or "用 insight_analyst" in block


def test_each_analysis_tool_carries_its_boundaries() -> None:
    """逐个工具断言五要素齐全（按行切回各工具段）。"""
    block = _format_tools_for_prompt()
    sections = {
        line.split(":", 1)[0].lstrip("- ").strip(): line
        for line in block.splitlines()
        if line.startswith("- ")
    }
    for name in ANALYSIS_TOOLS:
        desc = sections[name]
        assert "输入" in desc, f"{name} 缺输入说明"
        assert "输出" in desc, f"{name} 缺输出说明"
        assert "不要用来" in desc, f"{name} 缺「不要用来」边界"


def test_disambiguation_points_to_alternatives() -> None:
    """相近工具互相指路：trend/group/anomaly 的边界各指向另外两个。"""
    block = _format_tools_for_prompt()
    trend = next(l for l in block.splitlines() if l.startswith("- trend_analysis"))
    assert "group_compare" in trend and "detect_anomaly" in trend
    group = next(l for l in block.splitlines() if l.startswith("- group_compare"))
    assert "trend_analysis" in group and "detect_anomaly" in group


def test_whitelist_parameter_filters() -> None:
    block = _format_tools_for_prompt(whitelist={"chart_advisor"})
    assert "- chart_advisor:" in block
    assert "- trend_analysis:" not in block


def test_report_graph_prompt_menu_covers_all_five_tools(monkeypatch) -> None:
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
    result = rg._plan_analysis({"query_result": qr, "user_query": "x"})
    assert captured, "plan prompt 未捕获"
    prompt = captured[0]
    for name in ANALYSIS_TOOLS:
        assert f"- {name}:" in prompt, f"report 菜单缺少 {name}"

    # 菜单里能选的工具必须能执行：insight_analyst 分发不落入兜底 chart_advisor
    called: list[str] = []
    monkeypatch.setattr(rg, "insight_analyst", lambda data: called.append("insight") or "销售额: 合计=180")
    state = {
        "query_result": qr,
        "assemble_plan": result["assemble_plan"],
        "assemble_step_idx": 0,
        "assemble_results": [],
    }
    rg._run_step(state)
    assert called == ["insight"], "insight_analyst 未被分发执行"
