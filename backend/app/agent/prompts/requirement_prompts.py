from __future__ import annotations

"""Requirement Parser prompt。

源：原 `app/agent/requirement_parser.py:36 _PARSE_PROMPT` 模块化常量。P7 迁移到
6 段 + META + build 函数，文案等价（plan NOT doing：删现有文案 / 改温度）。
"""

from typing import Any

REQUIREMENT_PARSE_V1: dict[str, str] = {
    "system_contract": (
        "你是 ReportAgent 需求解析器。职责：理解意图、检测歧义、决定是否澄清、"
        "产出 RequirementCard。**禁止生成 SQL**——SQL 生成在后续 Execution 阶段。"
    ),
    "role": "你是 ReportAgent 需求解析器。",
    "task_contract": (
        "给定用户的中文业务问题与可用表结构，判断每个维度（time / scope / metric / "
        "granularity / comparison）是否明确，并输出结构化 JSON。"
        "\n\n用户问题: {user_query}"
        "\n\n可用表结构:"
        "\n{schema_text}"
        "\n\n{dictionary_block}"
        "\n\n字段释义规则："
        "\n- 「数据字典参考」中给出释义的字段，直接采用其含义，不要再生成对应假设"
        "\n- **关键**：data_source_type=stream 的字段（接口/长连接/实时推送）**不在任何 "
        "fact_* 事实表里**——严禁生成「该字段可能存在于 fact_orders.order_amount 这类事实表」"
        "这种假设！必须生成一个 data_source assumption："
        "\n  key 固定为 \"data_source:<接口名>\"，text 写「该字段来自实时流接口，未在当前分析"
        "数据库中；如需聚合查询需先接入数据通道」，"
        "\n  alternatives 给「实时流聚合服务 / 离线 ETL 同步表（需先建）/ 用户提供的其他查询路径」之类。"
        "\n  释义本身的 field_meaning 假设仍可生成（用户可采用），但 data_source assumption 与 "
        "field_meaning 是两个独立概念。"
        "\n- 用户提及的字段在字典中无释义或释义歧义时，输出 assumption："
        "\n  key 固定为 \"field_meaning:<字段名>\"，text 写你的最佳猜测释义（注明「请确认」），"
        "\n  alternatives 给候选释义（可为空数组）。用户确认前该字段含义不得用于 SQL 生成"
        "\n- **点名对象缺失（禁止静默替换）**：用户明确点名要查的表/对象/「xxx表」若不在"
        "可用表结构（或数据字典）里——**禁止**把它当噪声丢弃，也**禁止**悄悄替换成别的"
        "指标/表后把需求当作已确认。必须生成 assumption："
        "\n  key 固定为 \"requested_object:<对象名>\"，text 写「用户点名的「<对象名>」不在当前"
        "可用表结构中；请确认改为可用对象或补充该数据源」，"
        "\n  alternatives 给最相近的可用对象/指标选项（可为空数组）。用户确认/否决前该对象"
        "不得被当作已满足。summary 须保留该对象的原意，不得写成已替换后的指标。"
        "\n\n维度判断规则："
        "\n- time_range: 是否包含明确的时间范围或可推断的相对时间词（本月/上月/今年/最近30天 等）"
        "\n- scope: 是否包含明确的区域/产品/客户范围；可缺省（默认 ALL）"
        "\n- metric: 是否包含明确指标（销售额/订单数/退款率 等）"
        "\n- granularity: 日/周/月/季度；不指定时默认月"
        "\n- comparison: 是否要对比（同比/环比/指定基线）；不需要则 none"
    ),
    "tool_policy": (
        "如可用表结构已包含回答所需的全部字段信息，不重复调用 search_schema。"
        "若 schema 信息明显不足，可在 assumption 里提议 search_schema，但本 Agent 不直接调工具。"
    ),
    "output_schema": (
        "输出 JSON（禁止解释，禁止 markdown，禁止换行）："
        "\n{{"
        '\n  "summary": "一句话业务目标",'
        '\n  "target_metrics": ["指标1", "指标2"],'
        '\n  "time_range": "今年" | null,'
        '\n  "scope": ["华东"] | [],'
        '\n  "dimensions": ["时间", "区域"],'
        '\n  "analysis_methods": ["trend_analysis", "group_compare"],'
        '\n  "confidence": 0.85,'
        '\n  "missing_fields": ["time_range", "metric"],'
        '\n  "assumptions": ['
        '\n    {{"key": "scope_default", "text": "未指定范围，使用全部区域", "alternatives": [...]}}'
        "\n  ]"
        "\n}}"
    ),
    "safety_policy": (
        "Do NOT invent tables/columns（任何引用的表/字段都必须在可用表结构或字典里）。"
        "Do NOT fabricate query results（绝不预先生成数字）。"
        "Do NOT assume unavailable schema（未知字段走 assumption 走 confirmation）。"
        "Do NOT call search_schema when schema is already known（本步骤 schema 已注入）。"
        "Do NOT generate SQL（SQL 在 Execution 阶段）。"
    ),
}

REQUIREMENT_PARSE_META: dict[str, Any] = {
    "name": "requirement_parse",
    "version": 1,
    "purpose": "理解意图 + 维度判定 + 缺失检测 + 产出 RequirementCard",
    "input": ["user_query", "schema_text", "dictionary_block"],
    "output": "RequirementCard (summary / metrics / time_range / scope / dimensions / methods / confidence / missing / assumptions)",
}


def build_requirement_parse_prompt(
    user_query: str,
    schema_text: str,
    dictionary_block: str,
    prior_block: str = "",
) -> str:
    from app.infra.trace.sdk import record_prompt_version

    record_prompt_version(REQUIREMENT_PARSE_META["name"], REQUIREMENT_PARSE_META["version"])
    sections = [
        REQUIREMENT_PARSE_V1["system_contract"],
        REQUIREMENT_PARSE_V1["role"],
        REQUIREMENT_PARSE_V1["task_contract"].format(
            user_query=user_query,
            schema_text=schema_text,
            dictionary_block=dictionary_block,
        ),
    ]
    # P15 e2e T2：supplement 轮把上一轮已确认约束作为第 4 输入槽。空（mode=new）不加
    # 任何指令——行为与无 prior 完全一致。presence 语义：上轮已确认字段本轮未改写时
    # 输出留空（absence=继承信号），仅真正改写的字段输出非空（presence=覆盖信号）。
    if prior_block:
        sections.append(
            "【已确认需求（承上轮，mode=supplement）】\n"
            f"{prior_block}"
            "\n规则：以上已确认的维度字段默认沿用，本轮 query **未改写**时输出留空"
            "（不要臆造、不要照抄），由系统继承；只有本轮**明确改写**的字段才输出新值。"
            "缺失判定（missing_fields）只针对本轮新提出/改写的字段——上轮已确认且未改写的"
            "字段一律不得标为 missing。"
            "\n相对时间：相对时间词（今年/本月/最近 N 天 等）若没有绝对锚点，是「继续沿用"
            "上一轮已确认的时间范围」的续接表达，不得据此另起新时间范围；只有出现绝对时间"
            "（具体年份/日期区间）或明确的改写词（改成/换成/只看今年 等）才算改写 time_range。"
        )
    sections.extend([
        REQUIREMENT_PARSE_V1["tool_policy"],
        REQUIREMENT_PARSE_V1["output_schema"],
        REQUIREMENT_PARSE_V1["safety_policy"],
    ])
    return "\n\n".join(s for s in sections if s)