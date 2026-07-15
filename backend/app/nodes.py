from __future__ import annotations

import json
import time
from typing import Any

from app.state import AgentState, TraceStep
from app.tools import run_sql, validate_sql, chart_advisor, insight_analyst
from app.llm import get_chat_llm
from app.mcp_client import schema_client
from app.memory import search_memories, add_memory, format_memory_context


def _make_trace(step: str, status: str, detail: str, start: float) -> TraceStep:
    return TraceStep(
        step=step,
        status=status,
        detail=detail,
        duration=f"{time.time() - start:.1f}s",
    )


def classify_intent(state: AgentState) -> dict:
    start = time.time()
    query = state["user_query"]
    user_id = state.get("session_id", "default_user")
    memories = search_memories(query, user_id)
    memory_context = format_memory_context(memories)

    keywords_report = [
        "销售", "排名", "趋势", "增长", "利润", "退货",
        "多少", "统计", "哪个", "分析", "最高", "最低",
        "占比", "比较", "去年", "本月", "上月", "季度",
    ]
    keywords_chitchat = ["你好", "hi", "hello", "你是谁", "你能做什么"]

    q = query.lower()
    if any(k in q for k in keywords_chitchat):
        intent = "闲聊"
    elif any(k in q for k in keywords_report):
        intent = "报表"
    else:
        intent = "报表"

    trace = _make_trace("意图分类", "success", f"识别为: {intent}", start)
    return {
        "intent": intent,
        "memory_context": memory_context,
        "trace_log": [trace],
    }


async def gen_sql_llm(state: AgentState) -> dict:
    start = time.time()

    if state["intent"] != "报表":
        return {"trace_log": [_make_trace("SQL 生成", "success", "非报表模式，跳过", start)]}

    llm = get_chat_llm()
    tool_defs = schema_client.get_tool_definitions() + [
        {
            "name": "ask_clarification",
            "description": "当用户问题缺少关键信息时调用此工具向用户追问。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "追问问题"},
                },
                "required": ["question"],
            },
        },
    ]

    memory_ctx = state.get("memory_context", "")
    mem_block = memory_ctx if memory_ctx else "（无历史记忆）"

    system_prompt = f"""你是一个数据分析助手，工作语言是中文。
请根据用户的问题，使用可用工具发现数据库表结构，然后生成合适的 SQL 查询语句。

{mem_block}

工作流程：
1. 先调 list_tables() 看看有哪些表
2. 如果用户问题缺少关键信息，调 ask_clarification 追问
3. 调 search_tables("关键词") 语义搜索相关表
4. 调 get_table_ddl("表名") 查看具体字段
5. 综合信息后生成 SQL

规则：
- 只生成 SELECT 语句
- 表名和列名不加反引号
- 使用 SUM/COUNT/AVG/GROUP BY/ORDER BY
- 返回纯 SQL，不加额外解释"""

    llm_with_tools = llm.bind_tools(tool_defs, tool_choice="auto")

    human = f"用户问题: {state['user_query']}\n\n请先探索表结构，然后生成 SQL。"
    result = llm_with_tools.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": human},
    ])

    sql = result.content or ""
    sql = _extract_sql(sql)

    trace = _make_trace("SQL 生成", "success", f"已生成 SQL:\n{sql}", start)
    return {"generated_sql": sql, "trace_log": [trace]}


def validate_sql_step(state: AgentState) -> dict:
    start = time.time()
    sql = state.get("generated_sql", "")

    if not sql:
        return {
            "sql_valid": False,
            "sql_error": "无 SQL 语句",
            "trace_log": [_make_trace("SQL 验证", "error", "无 SQL 语句", start)],
        }

    result = json.loads(validate_sql(sql))
    valid = result.get("valid", False)

    if valid:
        return {
            "sql_valid": True,
            "trace_log": [_make_trace("SQL 验证", "success", "语法检查通过", start)],
        }
    else:
        error = result.get("error", "未知错误")
        return {
            "sql_valid": False,
            "sql_error": error,
            "trace_log": [_make_trace("SQL 验证", "error", error, start)],
        }


def execute_sql_step(state: AgentState) -> dict:
    start = time.time()
    sql = state.get("generated_sql", "")

    if not sql:
        return {"trace_log": [_make_trace("SQL 执行", "error", "无 SQL 语句", start)]}

    result = run_sql(sql)
    trace = _make_trace("SQL 执行", "success", "查询已执行", start)
    return {"sql_result": result, "trace_log": [trace]}


async def self_correct(state: AgentState) -> dict:
    start = time.time()
    error = state.get("sql_error", "")
    old_sql = state.get("generated_sql", "")

    llm = get_chat_llm()
    prompt = f"""你是一个 SQL 修复助手。之前的 SQL 执行出错。

错误信息: {error}

原始 SQL:
{old_sql}

请修正这条 SQL 语句。如果需要查看表结构，可以使用 get_table_ddl 工具。
只返回修正后的 SQL 本身："""

    llm_with_tools = llm.bind_tools(schema_client.get_tool_definitions(), tool_choice="auto")
    response = llm_with_tools.invoke([{"role": "user", "content": prompt}])

    new_sql = response.content or ""
    new_sql = _extract_sql(new_sql)

    retry_count = state.get("retry_count", 0) + 1
    trace = _make_trace(
        f"自我纠错(第{retry_count}次)", "success",
        f"原始 SQL 出错: {error}\n已修正", start,
    )

    return {
        "generated_sql": new_sql,
        "retry_count": retry_count,
        "sql_error": "",
        "trace_log": [trace],
    }


async def clarify(state: AgentState) -> dict:
    from langgraph.types import interrupt

    start = time.time()
    question = state.get("clarification_question", "")

    if not question:
        llm = get_chat_llm()
        error_info = state.get("sql_error", "")
        user_query = state["user_query"]

        prompt = f"""用户的问题是: "{user_query}"
{'SQL 执行出错: ' + error_info if error_info else ''}

经过分析，这个问题缺少关键信息无法完成查询。
请生成一个简短的追问（一句话），引导用户补充以下信息之一：
1. 具体的时间范围
2. 具体的区域/维度
3. 明确的需求指标

只返回追问问题本身，不要多余的解释："""

        response = llm.invoke(prompt)
        question = response.content.strip()

    trace = _make_trace("主动澄清", "clarify", f"需要用户补充信息: {question}", start)

    user_response = interrupt({
        "type": "clarify",
        "question": question,
    })

    trace = _make_trace("主动澄清", "success", f"用户回复: {user_response}", start)

    return {
        "need_clarification": False,
        "clarification_answer": str(user_response),
        "user_query": str(user_response),
        "generated_sql": "",
        "sql_valid": False,
        "sql_result": "",
        "sql_error": "",
        "retry_count": 0,
        "trace_log": [trace],
    }


def assemble_planner(state: AgentState) -> dict:
    start = time.time()
    result_json = state.get("sql_result", "{}")
    data = json.loads(result_json)

    if "error" in data:
        return {"trace_log": [_make_trace("报表组装", "error", data["error"], start)]}

    columns = data.get("columns", [])
    row_count = len(data.get("rows", []))

    llm = get_chat_llm()
    prompt = f"""你是一个数据分析规划师。根据以下数据特征，制定一个分析计划。

数据特征：
- 列: {', '.join(columns)}
- 行数: {row_count}

可用的分析工具：
1. recommend_chart(data) — 推荐可视化图表类型
2. trend_analysis(data) — 数据趋势分析
3. group_compare(data, group_col, value_col) — 按维度分组对比
4. detect_anomaly(data, value_col) — 检测异常值

请输出一个 JSON 格式的分析计划：
{{
  "steps": [
    {{"tool": "recommend_chart", "args": {{}}, "description": "推荐图表类型"}},
    {{"tool": "trend_analysis", "args": {{}}, "description": "分析整体趋势"}}
  ],
  "reasoning": "为什么这样分析"
}}
只返回 JSON，不要额外解释。"""

    response = llm.invoke(prompt)
    plan_text = response.content.strip()
    plan_text = _extract_code_block(plan_text)

    try:
        plan = json.loads(plan_text)
        steps = plan.get("steps", [])
    except json.JSONDecodeError:
        steps = [{"tool": "recommend_chart", "args": {}, "description": "推荐图表"}]

    trace = _make_trace(
        "报表规划", "success",
        f"规划了 {len(steps)} 步分析: {', '.join(s['description'] for s in steps)}", start,
    )

    return {
        "assemble_plan": steps,
        "assemble_step_idx": 0,
        "assemble_results": [],
        "trace_log": [trace],
    }


async def assemble_executor(state: AgentState) -> dict:
    start = time.time()
    plan = state.get("assemble_plan", [])
    step_idx = state.get("assemble_step_idx", 0)

    if not plan or step_idx >= len(plan):
        return _finalize_report(state, start)

    current_step = plan[step_idx]
    tool_name = current_step.get("tool", "")
    result_json = state.get("sql_result", "{}")

    result_text = _run_analysis_tool(tool_name, result_json, current_step.get("args", {}))

    partial_results = list(state.get("assemble_results", []))
    partial_results.append({
        "step": current_step.get("description", tool_name),
        "result": result_text,
    })

    llm = get_chat_llm()
    check_prompt = f"""分析步骤 '{current_step.get('description', tool_name)}' 已完成。
结果: {result_text[:500]}

这个结果是否正常？回复 '继续' 或给出调整建议。"""
    check_response = llm.invoke(check_prompt)
    check_text = check_response.content.strip()

    trace_status = "success"
    trace_detail = f"执行: {current_step.get('description', tool_name)}"
    if "调整" in check_text or "修改" in check_text:
        trace_status = "retry"
        trace_detail += f" → 计划调整: {check_text[:100]}"

    trace = _make_trace(
        f"报表分析({step_idx + 1}/{len(plan)})", trace_status, trace_detail, start,
    )

    return {
        "assemble_step_idx": step_idx + 1,
        "assemble_results": partial_results,
        "trace_log": [trace],
    }


def _finalize_report(state: AgentState, start: float) -> dict:
    partial_results = state.get("assemble_results", [])
    chart_config = {}
    insight_text = ""

    for pr in partial_results:
        if "推荐图表" in pr["step"] or "图表" in pr["step"]:
            try:
                chart_config = json.loads(pr["result"])
            except json.JSONDecodeError:
                chart_config = json.loads(chart_advisor(state.get("sql_result", "{}")))
        else:
            insight_text += pr["result"] + "\n"

    if not chart_config:
        chart_config = json.loads(chart_advisor(state.get("sql_result", "{}")))

    llm = get_chat_llm()
    results_summary = "\n".join(
        f"- {pr['step']}: {pr['result'][:200]}" for pr in partial_results
    )
    insight_prompt = f"""基于以下分析结果，用一句话总结核心洞察（中文）：
{results_summary}"""
    insight_response = llm.invoke(insight_prompt)
    insight_text = insight_response.content.strip() or insight_text

    user_id = state.get("session_id", "default_user")
    add_memory(f"用户查询: {state['user_query']} | 洞察: {insight_text[:200]}", user_id)

    trace = _make_trace(
        "报表组装", "success",
        f"综合 {len(partial_results)} 步分析结果，生成最终洞察", start,
    )

    return {
        "chart_config": chart_config,
        "insight_text": insight_text,
        "assemble_plan": [],
        "assemble_step_idx": 0,
        "trace_log": [trace],
    }


def _extract_sql(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n", 1)
        if len(lines) > 1:
            text = lines[1]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _extract_code_block(text: str) -> str:
    if text.startswith("```"):
        lines = text.split("\n", 1)
        if len(lines) > 1:
            text = lines[1]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _run_analysis_tool(tool_name: str, result_json: str, args: dict) -> str:
    if tool_name == "trend_analysis":
        return _trend_analysis(result_json)
    elif tool_name == "group_compare":
        return _group_compare(result_json, args.get("group_col", ""), args.get("value_col", ""))
    elif tool_name == "detect_anomaly":
        return _detect_anomaly(result_json, args.get("value_col", ""))
    elif tool_name == "recommend_chart":
        return chart_advisor(result_json)
    else:
        return f"未知分析工具: {tool_name}"


def _trend_analysis(data_json: str) -> str:
    data = json.loads(data_json)
    rows = data.get("rows", [])
    if len(rows) < 2:
        return "数据量不足，无法进行趋势分析"

    first = rows[0]
    numeric_keys = [k for k, v in first.items() if isinstance(v, (int, float))]
    if not numeric_keys:
        return "没有数值列，无法分析趋势"

    val_col = numeric_keys[0]
    values = [r[val_col] for r in rows if r.get(val_col) is not None]
    if len(values) >= 2:
        half = len(values) // 2
        first_avg = sum(values[:half]) / half
        second_avg = sum(values[half:]) / (len(values) - half)
        if second_avg > first_avg * 1.1:
            return f"整体呈上升趋势，后半段增长 {((second_avg / first_avg) - 1) * 100:.1f}%"
        elif first_avg > second_avg * 1.1:
            return f"整体呈下降趋势，后半段下降 {((first_avg / second_avg) - 1) * 100:.1f}%"
        else:
            return "整体趋势平稳"
    return "趋势分析完成"


def _group_compare(data_json: str, group_col: str = "", value_col: str = "") -> str:
    data = json.loads(data_json)
    rows = data.get("rows", [])
    if not rows:
        return "无数据"

    first = rows[0]
    if not group_col or group_col not in first:
        cat_keys = [k for k in first if not isinstance(first[k], (int, float))]
        group_col = cat_keys[0] if cat_keys else list(first.keys())[0]
    if not value_col or value_col not in first:
        num_keys = [k for k in first if isinstance(first[k], (int, float))]
        value_col = num_keys[0] if num_keys else list(first.keys())[-1]

    groups: dict[str, list[float]] = {}
    for r in rows:
        g = str(r.get(group_col, "未知"))
        v = r.get(value_col, 0) or 0
        groups.setdefault(g, []).append(float(v))

    summary = [
        f"{g}: 合计={sum(vals):,.2f}"
        for g, vals in sorted(groups.items(), key=lambda x: sum(x[1]), reverse=True)
    ]
    return "\n".join(summary)


def _detect_anomaly(data_json: str, value_col: str = "") -> str:
    import statistics

    data = json.loads(data_json)
    rows = data.get("rows", [])
    if not rows:
        return "无数据"

    first = rows[0]
    if not value_col or value_col not in first:
        num_keys = [k for k in first if isinstance(first[k], (int, float))]
        value_col = num_keys[0] if num_keys else ""
    if not value_col:
        return "没有数值列"

    values = [r[value_col] for r in rows if r.get(value_col) is not None]
    if len(values) < 3:
        return "数据量不足"

    try:
        mean = statistics.mean(values)
        stdev = statistics.stdev(values)
        threshold = 2 * stdev
        anomalies = []
        for r in rows:
            v = r.get(value_col, 0) or 0
            if abs(v - mean) > threshold:
                cat_keys = [k for k in first if not isinstance(first[k], (int, float))]
                label = str(r.get(cat_keys[0], "")) if cat_keys else ""
                anomalies.append(f"{label}: {v:,.2f}")
        if anomalies:
            return f"发现 {len(anomalies)} 个异常值: " + "; ".join(anomalies[:5])
        return "未发现明显异常值"
    except statistics.StatisticsError:
        return "无法计算标准差"
