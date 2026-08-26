from __future__ import annotations

import json

from langchain_core.tools import tool

from app.tools.rag_schema import (
    get_table_ddl_from_rag,
    list_tables_from_rag,
    search_tables_from_rag,
)


@tool
def search_tables(query: str, top_k: int = 3) -> str:
    """根据中文业务关键词搜索数据库表，返回表名、字段列表、DDL 和描述。
    用途：不知道数据在哪个表时用来「找表」。
    场景：用户提问含销售额/退货率/库存/趋势等业务概念，需要先定位表。
    反例：已经知道表名（如 fact_sales），只是要看它的字段 → 用 get_table_ddl。
    输入：query（中文描述），top_k（返回条数，默认 3）
    输出：JSON 数组，每项 {table_name, columns[{name,type}], ddl, description, score}
    示例：search_tables('退货原因') → 优先返回 fact_returns（退货事实表）
    失败：MCP 不可达 + flag 锁定 / MCP 协议错 → 返回空数组（graceful 退化，SQL 生成不阻塞）；
          其它失败路径（HTTP fallback 异常等）→ 同样返回空数组。"""
    return json.dumps(search_tables_from_rag(query, top_k), ensure_ascii=False)


@tool
def get_table_ddl(table_name: str) -> str:
    """获取指定单张表的完整 CREATE TABLE DDL（字段名、类型、精度）。
    用途：确定了目标表后，看精确字段名和类型用于写 SQL。
    前置：不确定表名时先用 search_tables 搜索，不要猜表名。
    输入：table_name（英文下划线格式，如 'fact_sales'、'dim_product'）
    输出：CREATE TABLE 文本
    示例：get_table_ddl('dim_region') → 返回含 region_name、province、city 的建表语句
    失败：表不存在或 MCP 不可达 + flag 锁定 → 返回 'Table xxx not found'。"""
    ddl = get_table_ddl_from_rag(table_name)
    if ddl is None:
        return f"Table '{table_name}' not found"
    return ddl


@tool
def list_tables() -> str:
    """列出数据库全部可用表（表名、中文描述、字段数），用于总览数据库全貌。
    用途：首次接入系统不了解结构时调用，或 search_tables 未返回理想结果时兜底。
    输入：无参数。
    输出：JSON 数组，每项 {table_name, description, column_count}
    限制：不返回字段详情。需要字段结构用 get_table_ddl。需要语义搜索用 search_tables。
    失败：MCP / HTTP 字典库不可达时返回空数组（graceful 退化）。"""
    return json.dumps(list_tables_from_rag(), ensure_ascii=False)