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


# ---------------------------------------------------------------------------
# REPORT_NARRATE_V1：把「数据 + 工具结论」写成给业务看的分析叙述
# ---------------------------------------------------------------------------

REPORT_NARRATE_V1: dict[str, str] = {
    "system_contract": (
        "你是 ReportAgent 数据分析师。把查询结果写成业务方能直接读的结论。"
        "**只使用输入里出现过的数字**，一个都不许编。"
    ),
    "role": "你的读者是不看 SQL 的业务负责人：先给结论，再给证据，最后给下一步。",
    "task_contract": (
        "用户问题: {user_query}"
        "\n需求摘要: {requirement_summary}"
        "\n\n查询结果（列: {columns}；共 {row_count} 行，下面是前 {sample_size} 行）:"
        "\n{sample_rows}"
        "\n\n工具算出来的结论（可引用其数字，但不要原样粘贴）:"
        "\n{tool_results}"
        "\n\n写作要求："
        "\n1. headline：一句话结论，必须带具体数字（金额/行数/百分比），不要「整体趋势平稳」这种空话。"
        "\n2. findings：2-3 条证据，每条带数字，说清「谁高谁低 / 涨了多少 / 差多少」。"
        "\n3. why：可能原因，用「可能/或许」措辞；没有依据就写「数据不足以判断原因」，不许编。"
        "\n4. next：1-2 条具体下一步（按品类下钻 / 看环比 / 排查某月异常）。"
        "\n5. 不要罗列原始明细行；不要出现英文列名（用中文指标名）；不要 markdown 标题符号。"
        "\n6. **指标名必须用结果列里的中文名**：列是「实收金额」就写实收金额，不要写成「销售额」或别的口径。"
    ),
    "output_schema": (
        "只输出 JSON，禁止解释："
        "\n{"
        '\n  "headline": "一句话结论（带数字）",'
        '\n  "findings": ["证据1（带数字）", "证据2（带数字）"],'
        '\n  "why": "可能原因（不确定就说明数据不足）",'
        '\n  "next": ["下一步建议1", "下一步建议2"]'
        "\n}"
    ),
    "self_check": (
        "【输出前自检】"
        "\n1. headline 里的数字是否出现在输入数据里？"
        "\n2. 有没有编造原因？（没有依据要写「数据不足以判断」）"
        "\n3. 有没有把原始明细行直接搬进来？"
    ),
    "safety_policy": (
        "Do NOT fabricate numbers。Do NOT invent business causes。"
        "Do NOT output English column names。"
    ),
}

REPORT_NARRATE_META: dict[str, Any] = {
    "name": "report_narrate",
    "version": 1,
    "purpose": "把 QueryResult + 工具结论写成结论/证据/原因/建议四段叙述",
    "input": ["user_query", "requirement_summary", "columns", "sample_rows", "tool_results"],
    "output": "{headline, findings[], why, next[]}",
}


def build_report_narrate_prompt(
    user_query: str,
    requirement_summary: str,
    columns: list[str],
    row_count: int,
    sample_rows: list[dict],
    tool_results: str,
    intent: str = "overview",
) -> str:
    import json as _json

    from app.infra.trace.sdk import record_prompt_version

    record_prompt_version(REPORT_NARRATE_META["name"], REPORT_NARRATE_META["version"])
    sections = [
        REPORT_NARRATE_V1["system_contract"],
        REPORT_NARRATE_V1["role"],
        REPORT_NARRATE_V1["task_contract"].format(
            user_query=user_query or "（未提供）",
            requirement_summary=requirement_summary or "（未提供）",
            columns=", ".join(columns) if columns else "（无）",
            row_count=row_count,
            sample_size=len(sample_rows),
            sample_rows=_json.dumps(sample_rows, ensure_ascii=False, default=str)[:4000],
            tool_results=(tool_results or "（无）")[:2000],
        ),
        _INTENT_GUIDE.get(intent, _INTENT_GUIDE["overview"]),
        REPORT_NARRATE_V1["output_schema"],
        REPORT_NARRATE_V1["self_check"],
        REPORT_NARRATE_V1["safety_policy"],
    ]
    return "\n\n".join(s for s in sections if s)


# 分析角度 → 叙述重点（2026-11-03：同一套摘要套所有问题的写法没有价值）
_INTENT_GUIDE: dict[str, str] = {
    "trend": (
        "【本次是趋势分析，叙述要回答】"
        "\n- 方向：整体上升/下降/平稳，幅度多少（用起止值或首尾对比）"
        "\n- 拐点：哪个月/哪一周出现转折，前后差多少"
        "\n- 不要只说「整体趋势平稳」，要说清「从 X 到 Y 变化了 Z%」"
    ),
    "compare": (
        "【本次是对比/排名分析，叙述要回答】"
        "\n- 排序：最高的 2 名与最低的 1 名，各是多少"
        "\n- 差距：第一名是最后一名的几倍/高多少"
        "\n- 集中度：前几名合计占总量多少（能算就算，算不了就别编）"
    ),
    "anomaly": (
        "【本次是异常分析，叙述要回答】"
        "\n- 异常点：哪几个时间点异常，偏离多少（不要罗列全部）"
        "\n- 是否连续：单点抖动还是连续几天"
        "\n- 若数据点太少（如按月只有 12 个点），要明确说「粒度太粗，建议按日看」"
    ),
    "detail": (
        "【本次是明细数据，叙述要回答「这批数据长什么样」】"
        "\n- 分布：金额/数量主要集中在什么区间，大致占比"
        "\n- 极端值：最大一笔多少、最小一笔多少"
        "\n- **绝对不要**逐行罗列订单"
    ),
    "overview": (
        "【本次是总览，叙述要回答】"
        "\n- 规模：总量/合计是多少"
        "\n- 结构：主要由谁贡献（前 1-2 名占比）"
        "\n- 一个值得注意的点（异常、缺口、集中度过高）"
    ),
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