from __future__ import annotations

import json
from typing import Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.llm import call_llm
from app.models.contracts import ErrorDetail, QueryPlan, QueryResult, SchemaContext
from app.utils.text import strip_markdown_fence
from app.tools.sql_tools import validate_sql, execute_sql


class SQLAgentState(TypedDict):
    schema_context: Optional[SchemaContext]
    user_query: str
    query_plan: Optional[QueryPlan]
    generated_sql: str
    validation_result: dict
    sql_result: str
    execution_status: str
    error: Optional[str]
    retry_counters: dict
    query_result: Optional[QueryResult]


def _plan(state: SQLAgentState) -> dict:
    schema = state.get("schema_context")
    schema_text = "无可用表结构" if not schema else "\n".join(
        f"- {t.name}: {t.description}" for t in schema.tables
    )

    prompt = f"""你是一个数据分析专家。根据用户问题和可用表结构，制定查询计划。

用户问题: {state["user_query"]}

可用表结构:
{schema_text}

请输出一个JSON格式的查询计划：
{{
  "target_metric": "目标指标",
  "dimensions": ["维度1", "维度2"],
  "filters": [{{"field": "字段", "operator": "=", "value": "值"}}],
  "aggregation": "sum/count/avg",
  "time_range": "时间范围或null"
}}
只返回JSON，不要额外解释。"""

    plan_text = call_llm(prompt)
    plan_text = strip_markdown_fence(plan_text)

    try:
        plan_dict = json.loads(plan_text)
        plan = QueryPlan(**plan_dict)
    except (json.JSONDecodeError, Exception):
        plan = QueryPlan(target_metric=state["user_query"])

    return {
        "query_plan": plan,
        "retry_counters": {"plan": 0, "sql_generation": 0},
    }


def _generate_sql(state: SQLAgentState) -> dict:
    plan = state.get("query_plan")
    schema = state.get("schema_context")

    schema_text = "无可用表结构" if not schema else "\n".join(
        f"表 {t.name} ({t.description}):\n"
        + "\n".join(f"  {c.name} ({c.type})" for c in t.columns)
        for t in schema.tables
    )

    prompt = f"""你是一个SQL生成专家。根据查询计划生成SQL语句。

查询计划:
- 目标指标: {plan.target_metric if plan else ''}
- 维度: {plan.dimensions if plan else []}
- 过滤条件: {plan.filters if plan else []}
- 聚合方式: {plan.aggregation if plan else ''}
- 时间范围: {plan.time_range if plan else '未指定'}

可用表结构:
{schema_text}

规则:
- 数据库是 DuckDB，使用 DuckDB 兼容的 SQL 语法
- 日期函数使用 strftime() 或 extract()，不要用 DATE_FORMAT
- 只生成SELECT语句，WHERE条件必须完整
- 表名和列名必须严格使用上面列出的名称
- JOIN 条件使用外键关联（如 fact_sales.region_id = dim_region.region_id）
- 使用中文别名
- 返回纯SQL，不加解释，不要markdown代码块"""

    sql = call_llm([{"role": "user", "content": prompt}])

    sql = strip_markdown_fence(sql)
    sql = sql.strip()

    retry = state.get("retry_counters", {})
    retry["sql_generation"] = retry.get("sql_generation", 0) + 1
    return {"generated_sql": sql, "retry_counters": retry}


def _validate(state: SQLAgentState) -> dict:
    sql = state.get("generated_sql", "")
    if not sql:
        return {"validation_result": {"valid": False, "error": "无SQL语句"},
                "execution_status": "SQL_SYNTAX_ERROR"}

    validation = json.loads(validate_sql(sql))
    return {"validation_result": validation}


def _execute(state: SQLAgentState) -> dict:
    sql = state.get("generated_sql", "")
    if not sql:
        return {"sql_result": json.dumps({"error": "无SQL语句"})}

    result = execute_sql(sql)
    return {"sql_result": result}


def _evaluate(state: SQLAgentState) -> dict:
    raw = state.get("sql_result", "")
    if not raw:
        # SQL was never executed (validation failure or empty)
        sql_retries = state.get("retry_counters", {}).get("sql_generation", 0)
        if sql_retries < 3:
            return {"execution_status": "SQL_SYNTAX_ERROR"}
        else:
            return {"execution_status": "NEED_CLARIFICATION",
                    "error": "SQL生成失败: 验证不通过"}
    result = json.loads(raw)
    if "error" in result:
        retry = state.get("retry_counters", {})
        sql_retries = retry.get("sql_generation", 0)
        plan_retries = retry.get("plan", 0)

        if sql_retries < 3:
            return {"execution_status": "SQL_SYNTAX_ERROR"}
        elif plan_retries < 1:
            retry["plan"] = plan_retries + 1
            return {"execution_status": "SCHEMA_ERROR", "retry_counters": retry}
        else:
            return {"execution_status": "NEED_CLARIFICATION",
                    "error": f"SQL执行失败: {result['error']}"}

    return {"execution_status": "SUCCESS"}


def _build_output(state: SQLAgentState) -> dict:
    raw = state.get("sql_result", "")
    if not raw:
        return {"query_result": QueryResult(sql="", status="EMPTY")}
    try:
        result_data = json.loads(raw)
    except json.JSONDecodeError:
        return {"query_result": QueryResult(sql="", status="FAILED",
            error=ErrorDetail(code="INVALID_RESULT", message=f"Invalid JSON: {raw[:100]}"))}
    has_error = "error" in result_data and result_data["error"]
    columns_raw = result_data.get("columns", [])
    columns = [c if isinstance(c, dict) else {"name": c, "type": ""} for c in columns_raw]
    qr = QueryResult(
        sql=state.get("generated_sql", ""),
        columns=columns,
        rows=result_data.get("rows", []),
        row_count=len(result_data.get("rows", [])),
        status="FAILED" if has_error else "SUCCESS",
        error=ErrorDetail(code="EXECUTION_ERROR", message=str(result_data["error"])) if has_error else None,
    )
    return {"query_result": qr}


def _route_after_validate(state: SQLAgentState) -> Literal["execute", "evaluate"]:
    v = state.get("validation_result", {})
    if v.get("valid"):
        return "execute"
    return "evaluate"


def _route_after_evaluate(state: SQLAgentState) -> Literal["plan", "generate_sql", "build_output", "__end__"]:
    status = state.get("execution_status", "")
    if status == "SUCCESS":
        return "build_output"
    elif status == "SCHEMA_ERROR":
        return "plan"
    elif status == "SQL_SYNTAX_ERROR":
        return "generate_sql"
    return "__end__"


def build_sql_graph():
    workflow = StateGraph(SQLAgentState)

    workflow.add_node("plan", _plan)
    workflow.add_node("generate_sql", _generate_sql)
    workflow.add_node("validate", _validate)
    workflow.add_node("execute", _execute)
    workflow.add_node("evaluate", _evaluate)
    workflow.add_node("build_output", _build_output)

    workflow.set_entry_point("plan")

    workflow.add_edge("plan", "generate_sql")
    workflow.add_edge("generate_sql", "validate")
    workflow.add_conditional_edges("validate", _route_after_validate)
    workflow.add_edge("execute", "evaluate")
    workflow.add_conditional_edges("evaluate", _route_after_evaluate)
    workflow.add_edge("build_output", END)

    return workflow.compile()
