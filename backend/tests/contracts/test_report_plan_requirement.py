"""报告规划必须知道「用户要什么」（2026-11-02）。

缺陷：`_plan_analysis` 只把列名 + 行数 + 工具菜单喂给规划模型。卡里明明写着
`analysis_methods=['trend_analysis','anomaly_detection']`，报告阶段收不到，
于是「分析华东销售趋势和异常」只产出一张图 + 一张表——没有趋势结论、没有异常检测。

这里钉住：需求段必须在 prompt 里，且把已确认的分析方法翻译成**必须生成的 step**。
"""
from __future__ import annotations

import pytest

from app.agent.prompts.report_prompts import build_report_plan_prompt

pytestmark = pytest.mark.contracts


def test_requirement_block_lists_required_tools():
    prompt = build_report_plan_prompt(
        column_names=["月份", "区域", "支付金额"],
        row_count=12,
        tools_block="- trend_analysis: …",
        requirement_summary="分析2024年华东地区的销售趋势和异常情况",
        analysis_methods=["trend_analysis", "anomaly_detection"],
        dimensions=["时间", "区域"],
    )

    assert "原始诉求：分析2024年华东地区的销售趋势和异常情况" in prompt
    assert "分析维度：时间、区域" in prompt
    assert "trend_analysis" in prompt
    assert "detect_anomaly" in prompt, "anomaly_detection 必须映射到 detect_anomaly"
    assert "**必须**" in prompt, "必须显式要求模型产出这些 step"
    assert "chart_advisor 放最后" in prompt


def test_prompt_without_requirement_has_no_requirement_block():
    prompt = build_report_plan_prompt(
        column_names=["区域", "销售额"], row_count=6, tools_block="- chart_advisor: …",
    )
    assert "【用户需求】" not in prompt
    # 原有结构不受影响
    assert "行数: 6" in prompt


def test_unknown_methods_do_not_produce_fake_tools():
    """未知方法名不得凭空造 tool（只映射白名单内的 5 个工具）。"""
    prompt = build_report_plan_prompt(
        column_names=["区域", "销售额"],
        row_count=6,
        tools_block="- chart_advisor: …",
        analysis_methods=["predict_the_future"],
    )
    assert "**必须**" not in prompt
