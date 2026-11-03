"""P7 T5 等价性钉（2026-11-02 收敛为 1 条）。

原来 8 条用例逐条断言「prompt 全文含某个中文子串」。其中 `DuckDB` / `EXTRACT`
在 prompt 里是**否定句**（"不用 DuckDB 专属语法"、"不要使用 EXTRACT()"）——
钉词不钉极性：把否定句改成肯定句（真的教坏模型）测试照样绿。

现在只钉一件事：**prompt 的 output_schema 段必须声明解析器真正会读的键**，
另外对 sql_generate 显式钉住「禁止 DuckDB 方言」这个极性。
"""
from __future__ import annotations

import pytest

from app.agent.prompts import (
    INTENT_CLASSIFY_V1,
    REPORT_PLAN_V1,
    REQUIREMENT_PARSE_V1,
    SQL_GENERATE_V1,
    SQL_INTENT_ANALYZE_V1,
    SQL_PLAN_V1,
)
from app.memory.prompts import CONVERSATION_SUMMARIZE_V1

pytestmark = pytest.mark.contracts

# prompt → 解析器真正读取的顶层输出键（各 *_parser / *_graph 的 model_validate 字段）
REQUIRED_OUTPUT_KEYS: dict[str, tuple[str, ...]] = {
    "intent_classify": ("kind", "confidence", "reason"),
    "requirement_parse": (
        "summary", "target_metrics", "time_range", "granularity",
        "scope", "dimensions", "analysis_methods", "confidence",
        # 2026-11-03：澄清项取代固定 key 列表——由模型按 schema 现场生成
        "clarifications", "assumptions",
    ),
    "sql_intent_analyze": ("options", "needs_options_group", "confidence", "reasoning"),
    "sql_plan": (
        "target_metric", "dimensions", "filters", "aggregation",
        "time_range", "clarify_decision",
    ),
    "sql_generate": ("SELECT",),
    "report_plan": ("steps", "reasoning"),
    "conversation_summarize": ("summary", "extracted_schemas", "extracted_preferences"),
}

PROMPTS = {
    "intent_classify": INTENT_CLASSIFY_V1,
    "requirement_parse": REQUIREMENT_PARSE_V1,
    "sql_intent_analyze": SQL_INTENT_ANALYZE_V1,
    "sql_plan": SQL_PLAN_V1,
    "sql_generate": SQL_GENERATE_V1,
    "report_plan": REPORT_PLAN_V1,
    "conversation_summarize": CONVERSATION_SUMMARIZE_V1,
}


def test_output_schema_declares_parser_keys() -> None:
    """output_schema 段必须声明解析器会读的键；sql_generate 必须保持否定极性。"""
    problems: list[str] = []
    for name, prompt in PROMPTS.items():
        section = prompt["output_schema"]
        for key in REQUIRED_OUTPUT_KEYS[name]:
            if key not in section:
                problems.append(f"{name}.output_schema 缺 {key!r}")

    # 极性钉子：这版 prompt 允许 DuckDB 方言就等于教模型写错库。
    sql_section = PROMPTS["sql_generate"]["output_schema"]
    for phrase in ("PostgreSQL", "不用 DuckDB"):
        if phrase not in sql_section:
            problems.append(f"sql_generate.output_schema 丢了否定极性: 缺 {phrase!r}")

    assert problems == [], "\n".join(problems)
