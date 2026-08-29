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
) -> str:
    sections = [
        REPORT_PLAN_V1["system_contract"],
        REPORT_PLAN_V1["role"],
        REPORT_PLAN_V1["task_contract"].format(
            column_names=", ".join(column_names),
            row_count=row_count,
            tools_block=tools_block,
        ),
        REPORT_PLAN_V1["tool_policy"],
        REPORT_PLAN_V1["output_schema"],
        REPORT_PLAN_V1["safety_policy"],
    ]
    return "\n\n".join(s for s in sections if s)