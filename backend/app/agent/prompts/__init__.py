from __future__ import annotations

"""ReportAgent Agent Prompt 模块集中导出。

每个 prompt 由 6 段组成（system_contract / role / task_contract / tool_policy /
output_schema / safety_policy），配 META 5 字段元数据（name/version/purpose/input/output）。
Dynamic Context 注入由 Context Runtime 统一负责，build 函数只填占位符。

详见 [docs/plans/2026-08-29-p7-prompt-refactor.md](../../../../../../docs/plans/2026-08-29-p7-prompt-refactor.md) D2/D3。
"""

from app.agent.prompts.intent_prompts import (
    INTENT_CLASSIFY_META,
    INTENT_CLASSIFY_V1,
    build_intent_classify_prompt,
)
from app.agent.prompts.requirement_prompts import (
    REQUIREMENT_PARSE_META,
    REQUIREMENT_PARSE_V1,
    build_requirement_parse_prompt,
)
from app.agent.prompts.report_prompts import (
    REPORT_PLAN_META,
    REPORT_PLAN_V1,
    build_report_plan_prompt,
)
from app.agent.prompts.sql_prompts import (
    SQL_GENERATE_META,
    SQL_GENERATE_V1,
    SQL_INTENT_ANALYZE_META,
    SQL_INTENT_ANALYZE_V1,
    SQL_PLAN_META,
    SQL_PLAN_V1,
    build_sql_generate_prompt,
    build_sql_intent_analyze_prompt,
    build_sql_plan_prompt,
)

__all__ = [
    "INTENT_CLASSIFY_META", "INTENT_CLASSIFY_V1", "build_intent_classify_prompt",
    "REQUIREMENT_PARSE_META", "REQUIREMENT_PARSE_V1", "build_requirement_parse_prompt",
    "SQL_INTENT_ANALYZE_META", "SQL_INTENT_ANALYZE_V1", "build_sql_intent_analyze_prompt",
    "SQL_PLAN_META", "SQL_PLAN_V1", "build_sql_plan_prompt",
    "SQL_GENERATE_META", "SQL_GENERATE_V1", "build_sql_generate_prompt",
    "REPORT_PLAN_META", "REPORT_PLAN_V1", "build_report_plan_prompt",
]