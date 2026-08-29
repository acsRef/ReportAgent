from __future__ import annotations

"""SQL Agent prompts（intention / plan / generate）。

源：原 `app/agent/sql_graph.py:167,311,442` 三处裸 f-string。P7 重构为 6 段 +
META + build 函数，文案等价不动。

注意：
- Dynamic Context 注入（`assembled_context` 前置）由 caller 处理，不进 build。
- Repair feedback（prev_sql + error）由 caller 拼接在 build 输出末尾，
  build 不感知——避免污染 6 段结构。
"""

from typing import Any, Optional

# ---------------------------------------------------------------------------
# SQL_INTENT_ANALYZE_V1：5 工具中选 3-4 个推荐
# ---------------------------------------------------------------------------

SQL_INTENT_ANALYZE_V1: dict[str, str] = {
    "system_contract": (
        "你是 ReportAgent 意图分析器。职责：从 5 个分析工具中选 3-4 个推荐给用户，"
        "不生成 SQL、不调 search_schema、不调 data tool——这是 SQL 前的 cheap "
        "pre-flight step。"
    ),
    "role": "你是 ReportAgent 意图分析器。",
    "task_contract": (
        "用户的问题要匹配下面的分析工具。"
        "请选最合适的 3-4 个工具推荐给用户，输出 JSON。"
        "\n\n用户问题: {user_query}"
        "\n\n可用工具:"
        "\n{tools_block}"
        "\n\n── 工具选择指南 ──"
        "\n\n【chart_advisor vs insight_analyst】"
        "\n  chart_advisor → 数据已有，想要一个图来展示（自动判断用饼图还是柱状图）"
        "\n  insight_analyst → 数据已有，想要数值摘要（合计、平均、最大、最小）"
        "\n  区别：需要的是「图」还是「数」。两者不互斥，但一次只推荐一个。"
        "\n\n【group_compare vs trend_analysis】"
        "\n  group_compare → 对比不同组的数值高低（哪个区域最高、哪个产品最畅销）"
        "\n  trend_analysis → 观察单一维度的变化方向（这个月比上个月涨了还是跌了）"
        "\n  区别：横向对比 vs 纵向趋势。想比高低用 group_compare，想看走势用 trend_analysis。"
        "\n\n【detect_anomaly】"
        "\n  只想看「哪里不正常」（异常高或异常低）时用。数据量小于 3 行时不可用。"
    ),
    "tool_policy": (
        "tool 名必须逐字使用提供的 5 个之一（chart_advisor / insight_analyst / "
        "group_compare / trend_analysis / detect_anomaly）。"
        "不要发明工具名，不要调用 search_schema——表结构不在本步骤需要。"
    ),
    "output_schema": (
        "仅输出 JSON，禁止 markdown，禁止解释："
        "\n{{"
        '\n  "options": ['
        "\n    {{"
        '\n      "label": "📊 各区域销售对比",'
        '\n      "description": "按区域汇总销售额并排名",'
        '\n      "tool": "group_compare",'
        '\n      "params_preview": {{"group_col": "region_name", "value_col": "total_amount"}}'
        "\n    }}"
        "\n  ],"
        '\n  "needs_options_group": true/false,'
        '\n  "confidence": 0.85,'
        '\n  "reasoning": "用户想看不同区域的销售横向对比"'
        "\n}}"
        "\n\n格式约束:"
        "\n- options 数量: 3-4 个"
        "\n- 每个 option.tool 必须是上面 5 个之一"
        "\n- confidence: 0.7-0.95，越匹配越高"
        "\n- needs_options_group: 用户没指定时间/区域范围等细节时 true，完全明确时 false"
    ),
    "safety_policy": (
        "Do NOT invent tables/columns。"
        "Do NOT fabricate query results。"
        "Do NOT assume unavailable schema。"
        "Do NOT call search_schema（本步骤不需要表结构）。"
        "Do NOT generate SQL。"
    ),
}

SQL_INTENT_ANALYZE_META: dict[str, Any] = {
    "name": "sql_intent_analyze",
    "version": 1,
    "purpose": "从 5 个分析工具选 3-4 个推荐给用户，输出 options JSON",
    "input": ["user_query", "tools_block"],
    "output": "{options: [{label, description, tool, params_preview}], needs_options_group, confidence, reasoning}",
}


def build_sql_intent_analyze_prompt(user_query: str, tools_block: str) -> str:
    sections = [
        SQL_INTENT_ANALYZE_V1["system_contract"],
        SQL_INTENT_ANALYZE_V1["role"],
        SQL_INTENT_ANALYZE_V1["task_contract"].format(
            user_query=user_query, tools_block=tools_block,
        ),
        SQL_INTENT_ANALYZE_V1["tool_policy"],
        SQL_INTENT_ANALYZE_V1["output_schema"],
        SQL_INTENT_ANALYZE_V1["safety_policy"],
    ]
    return "\n\n".join(s for s in sections if s)


# ---------------------------------------------------------------------------
# SQL_PLAN_V1：查询计划 + 澄清决策
# ---------------------------------------------------------------------------

SQL_PLAN_V1: dict[str, str] = {
    "system_contract": (
        "你是 ReportAgent SQL 规划器。职责：根据用户问题+可用表结构，"
        "一次性产出查询计划与澄清决策。**不在本阶段写 SQL 字符串**——SQL 字符串"
        "在 generate 阶段。"
    ),
    "role": "你是一个SQL规划器。",
    "task_contract": (
        "任务：根据用户问题、可用表结构，一次性产出查询计划与澄清决策。"
        "\n\n当前日期: {today}"
        "\n\n用户问题: {user_query}"
        "\n{tool_hint}"
        "\n{confirmed_block}"
        "\n可用表结构:"
        "\n{schema_text}"
        "\n\n{plan_table_hints}"
        "\n\n决策策略(必须逐项执行，不要遗漏):"
        "\n- 第一步:列出用户问题里关于 time / region / metric 三维度的明确程度"
        "\n- 第二步:严格按下列规则判断 action:"
        "\n  · 三维度全明确 → action=\"run_direct\"，confidence ≥ 0.85，missing_dimensions: []"
        "\n  · 1个维度缺但可推断(例:\"今年\"→当前年、\"上月\"→上月) → "
        'action="run_direct"，confidence ≈ 0.75，missing_dimensions 列出唯一不可推断的维度名'
        "\n  · 2个及以上维度缺、无法安全推断 → action=\"clarify\"，confidence ≤ 0.60，"
        "missing_dimensions 列出所有缺维度的名字(从 time/region/metric 中选)"
        "\n\n必须字段:missing_dimensions 必须是 [\"time\"] / [\"region\"] / [\"metric\"] / "
        "任意组合 / [] 之一,绝不能为空字符串或乱写。"
        "\n当 action=\"clarify\" 时,missing_dimensions 至少包含 1 个元素。"
        "\n\n只输出JSON，禁止解释，禁止markdown，禁止思考过程。"
    ),
    "tool_policy": (
        "本步骤依赖可用表结构已注入 prompt，不重复调 search_schema。"
        "如 schema 明显不足（无可用表），应在 task_contract 给出 clarification 信号，"
        "而不是自己脑补表结构。"
    ),
    "output_schema": (
        "输出格式:"
        "\n{{"
        '\n  "target_metric": "目标指标",'
        '\n  "dimensions": ["维度1", "维度2"],'
        '\n  "filters": [{{"field": "字段", "operator": "=", "value": "值"}}],'
        '\n  "aggregation": "sum/count/avg",'
        '\n  "time_range": "时间范围或null",'
        '\n  "clarify_decision": {{'
        '\n    "action": "clarify" | "run_direct",'
        '\n    "missing_dimensions": ["time"|"region"|"metric"],'
        '\n    "predicted_table": "fact_sales"|null,'
        '\n    "confidence": 0.85,'
        '\n    "reasoning": "简短理由"'
        "\n  }}"
        "\n}}"
        "\n\n{plan_fewshot}"
    ),
    "safety_policy": (
        "Do NOT invent tables/columns（predicted_table 必须是可用表结构里真实存在的）。"
        "Do NOT fabricate query results（不在 plan 阶段输出数字）。"
        "Do NOT assume unavailable schema。"
        "Do NOT call search_schema（schema 已注入）。"
        "Do NOT generate SQL 字符串（本阶段是 plan，SQL 在 generate 阶段）。"
    ),
}

SQL_PLAN_META: dict[str, Any] = {
    "name": "sql_plan",
    "version": 1,
    "purpose": "查询计划 + 澄清决策（run_direct / clarify + missing_dimensions）",
    "input": ["today", "user_query", "tool_hint", "confirmed_block", "schema_text",
              "plan_table_hints", "plan_fewshot"],
    "output": "{target_metric, dimensions, filters, aggregation, time_range, clarify_decision}",
}


def build_sql_plan_prompt(
    today: str,
    user_query: str,
    schema_text: str,
    tool_hint: str = "",
    confirmed_block: str = "",
    plan_table_hints: str = "",
    plan_fewshot: str = "",
) -> str:
    sections = [
        SQL_PLAN_V1["system_contract"],
        SQL_PLAN_V1["role"],
        SQL_PLAN_V1["task_contract"].format(
            today=today,
            user_query=user_query,
            tool_hint=tool_hint,
            confirmed_block=confirmed_block,
            schema_text=schema_text,
            plan_table_hints=plan_table_hints,
        ),
        SQL_PLAN_V1["tool_policy"],
        SQL_PLAN_V1["output_schema"].format(plan_fewshot=plan_fewshot),
        SQL_PLAN_V1["safety_policy"],
    ]
    return "\n\n".join(s for s in sections if s)


# ---------------------------------------------------------------------------
# SQL_GENERATE_V1：根据 plan 生成 SQL
# ---------------------------------------------------------------------------

SQL_GENERATE_V1: dict[str, str] = {
    "system_contract": (
        "你是 ReportAgent SQL 生成专家。职责：根据查询计划生成可执行 SQL（PostgreSQL）。"
        "只生成 SELECT；DROP/UPDATE/DELETE 一律禁止。"
    ),
    "role": "你是一个SQL生成专家。",
    "task_contract": (
        "根据查询计划生成SQL语句。"
        "\n\n当前日期: {today}"
        "\n\n查询计划:"
        "\n- 目标指标: {target_metric}"
        "\n- 维度: {dimensions}"
        "\n- 过滤条件: {filters}"
        "\n- 聚合方式: {aggregation}"
        "\n- 时间范围: {time_range}"
        "\n\n可用表结构:"
        "\n{schema_text}"
        "\n\n{fk_chain_hints}"
        "\n\n{faq_block}"
    ),
    "tool_policy": (
        "表名/列名以「可用表结构」里真实名称为准——FAQ 历史案例与示例 SQL 仅作参考，"
        "若与可用表结构冲突以可用表结构为准。"
        "schema 已注入 (来自 schema_text),不要自己脑补表/字段,也不要重复调 search_schema。"
    ),
    "output_schema": (
        "规则:"
        "\n- 数据库是 PostgreSQL，使用标准 PostgreSQL 兼容的 SQL 语法（不用 DuckDB 专属语法）"
        "\n- 不要使用 EXTRACT() 类的 DuckDB 函数做日期处理"
        "\n- 只生成 SELECT 语句，WHERE 条件必须完整"
        "\n- 表名和列名必须严格使用上面列出的名称"
        "（注意 dim_date 没有 month 列，只有 year / quarter_num / quarter / "
        "week_of_year / day_name / full_date）"
        "\n- JOIN 条件使用外键关联（如 fact_sales.region_id = dim_region.region_id）"
        "\n- 使用中文别名（例如「销售额」「年份」）"
        "\n- 只输出纯 SQL，禁止解释，禁止 markdown 代码块，禁止反斜杠转义"
        "\n- 字面量规则（重要）— 见 _SQL_GENERATION_RULES 末尾「字面量与转义规则」段"
        "\n\n{sql_generation_rules}"
    ),
    "safety_policy": (
        "Do NOT invent tables/columns。"
        "Do NOT fabricate query results（不预先生成数字）。"
        "Do NOT assume unavailable schema。"
        "Do NOT call search_schema（schema 已注入 prompt）。"
        "Do NOT generate DROP/UPDATE/DELETE——本 Agent 只读 SELECT。"
    ),
}

SQL_GENERATE_META: dict[str, Any] = {
    "name": "sql_generate",
    "version": 1,
    "purpose": "根据查询计划生成 PostgreSQL SELECT 语句",
    "input": ["today", "target_metric", "dimensions", "filters", "aggregation",
              "time_range", "schema_text", "fk_chain_hints", "faq_block",
              "sql_generation_rules"],
    "output": "SQL string（pure SQL, no markdown）",
}


def build_sql_generate_prompt(
    today: str,
    target_metric: str,
    dimensions: list,
    filters: list,
    aggregation: str,
    time_range: Optional[str],
    schema_text: str,
    fk_chain_hints: str = "",
    faq_block: str = "",
    sql_generation_rules: str = "",
) -> str:
    sections = [
        SQL_GENERATE_V1["system_contract"],
        SQL_GENERATE_V1["role"],
        SQL_GENERATE_V1["task_contract"].format(
            today=today,
            target_metric=target_metric or "",
            dimensions=dimensions or [],
            filters=filters or [],
            aggregation=aggregation or "",
            time_range=time_range or "未指定",
            schema_text=schema_text,
            fk_chain_hints=fk_chain_hints,
            faq_block=faq_block,
        ),
        SQL_GENERATE_V1["tool_policy"],
        SQL_GENERATE_V1["output_schema"].format(sql_generation_rules=sql_generation_rules),
        SQL_GENERATE_V1["safety_policy"],
    ]
    return "\n\n".join(s for s in sections if s)