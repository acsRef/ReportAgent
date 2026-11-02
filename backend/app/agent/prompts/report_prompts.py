from __future__ import annotations

"""Report Graph prompt（assemble_plan 阶段：基于 QueryResult 制定分析步骤）。

源：原 `app/agent/report_graph.py:61` 裸 f-string。P7 重构为 6 段 + META +
build 函数，文案等价不动。
"""

from typing import Any

REPORT_PLAN_V1: dict[str, str] = {
    "system_contract": (
        "你是 ReportAgent 报告规划师。职责：根据已执行的 QueryResult（列名+行数）"
        "制定分析计划（steps 列表），每步选一个分析工具。不再做 SQL、不调 search_schema。"
    ),
    "role": "你是一个数据分析规划师。",
    "task_contract": (
        "根据以下数据特征，制定分析计划。"
        "\n\n列: {column_names}"
        "\n行数: {row_count}"
        "\n\n可用分析工具（五个工具各管一件事，按描述里的「适用/不要用来」选择，"
        "tool 名必须逐字使用）："
        "\n{tools_block}"
    ),
    "tool_policy": (
        "tool 名必须逐字使用提供的 5 个之一（chart_advisor / insight_analyst / "
        "group_compare / trend_analysis / detect_anomaly）。"
        "不要发明工具名。数据已从 QueryResult 来——不要再调 SQL tool 或 search_schema。"
    ),
    "output_schema": (
        "只输出JSON，禁止解释，禁止markdown，禁止思考过程。"
        "\n格式："
        "\n{{\"steps\": [{{\"tool\": \"...\", \"args\": {{}}, \"description\": \"...\"}}], "
        "\"reasoning\": \"...\"}}"
    ),
    "safety_policy": (
        "Do NOT invent tables/columns。"
        "Do NOT fabricate query results（数字必须来自 QueryResult）。"
        "Do NOT assume unavailable schema。"
        "Do NOT call search_schema（数据已从 QueryResult 来）。"
        "Do NOT generate SQL（SQL 在 Execution 阶段已完成）。"
    ),
}

REPORT_PLAN_META: dict[str, Any] = {
    "name": "report_plan",
    "version": 1,
    "purpose": "基于 QueryResult 列名+行数制定分析计划 steps 列表",
    "input": ["column_names", "row_count", "tools_block"],
    "output": "{steps: [{tool, args, description}], reasoning}",
}


def build_report_plan_prompt(
    column_names: list[str],
    row_count: int,
    tools_block: str,
    *,
    requirement_summary: str = "",
    analysis_methods: list[str] | None = None,
    dimensions: list[str] | None = None,
) -> str:
    """构造报告规划 prompt。

    2026-11-02：此前只喂「列名 + 行数 + 工具菜单」，规划模型**完全不知道用户在问什么**——
    「分析华东销售趋势和异常」的卡片明明带 `analysis_methods=['trend_analysis',
    'anomaly_detection']`，报告阶段收不到，于是只产出一张图 + 一张表，没有趋势结论、
    没有异常检测。现在把需求摘要与分析要求一并注入，并给出确定性排序规则。
    """
    from app.infra.trace.sdk import record_prompt_version

    record_prompt_version(REPORT_PLAN_META["name"], REPORT_PLAN_META["version"])
    sections = [
        REPORT_PLAN_V1["system_contract"],
        REPORT_PLAN_V1["role"],
        REPORT_PLAN_V1["task_contract"].format(
            column_names=", ".join(column_names),
            row_count=row_count,
            tools_block=tools_block,
        ),
    ]

    requirement_block = _requirement_block(
        requirement_summary, analysis_methods or [], dimensions or [],
    )
    if requirement_block:
        sections.append(requirement_block)

    sections += [
        REPORT_PLAN_V1["tool_policy"],
        REPORT_PLAN_V1["output_schema"],
        REPORT_PLAN_V1["safety_policy"],
    ]
    return "\n\n".join(s for s in sections if s)


_METHOD_TO_TOOL = {
    "trend_analysis": "trend_analysis",
    "trend": "trend_analysis",
    "anomaly_detection": "detect_anomaly",
    "detect_anomaly": "detect_anomaly",
    "anomaly": "detect_anomaly",
    "group_compare": "group_compare",
    "comparison": "group_compare",
    "insight_analyst": "insight_analyst",
}


def _requirement_block(
    summary: str, methods: list[str], dimensions: list[str],
) -> str:
    """需求段 + 确定性工具要求（缺这段 = 规划模型不知道用户要趋势还是异常）。"""
    if not (summary or methods or dimensions):
        return ""
    lines = ["【用户需求】"]
    if summary:
        lines.append(f"原始诉求：{summary}")
    if dimensions:
        lines.append(f"分析维度：{'、'.join(dimensions)}")
    if methods:
        lines.append(f"需求已确认的分析方法：{'、'.join(methods)}")
        required = sorted({_METHOD_TO_TOOL[m] for m in methods if m in _METHOD_TO_TOOL})
        if required:
            lines.append(
                "**必须**为下列每个分析方法生成对应 step（tool 名逐字使用）："
                + "、".join(required)
            )
            lines.append(
                "趋势分析只对时间序列有意义（时间列必须作为维度）；异常检测针对数值列。"
            )
    lines.append("steps 顺序：分析类工具在前，chart_advisor 放最后一步。")
    return "\n".join(lines)