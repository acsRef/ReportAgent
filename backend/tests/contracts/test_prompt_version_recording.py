from __future__ import annotations

import pytest

pytestmark = pytest.mark.contracts

from app.agent.prompts.intent_prompts import INTENT_CLASSIFY_META, build_intent_classify_prompt
from app.agent.prompts.requirement_prompts import (
    REQUIREMENT_PARSE_META,
    build_requirement_parse_prompt,
)
from app.agent.prompts.report_prompts import REPORT_PLAN_META, build_report_plan_prompt
from app.agent.prompts.sql_prompts import (
    SQL_GENERATE_META,
    SQL_INTENT_ANALYZE_META,
    SQL_PLAN_META,
    build_sql_generate_prompt,
    build_sql_intent_analyze_prompt,
    build_sql_plan_prompt,
)
from app.infra.trace import sdk
from app.infra.trace.sdk import Tracer

# (builder, META, 最小调用参数)
_BUILDERS = [
    (build_intent_classify_prompt, INTENT_CLASSIFY_META, ("2024年销售额",)),
    (build_report_plan_prompt, REPORT_PLAN_META, (["区域"], 5, "---")),
    (build_requirement_parse_prompt, REQUIREMENT_PARSE_META, ("2024年销售额", "schema", "dict")),
    (build_sql_intent_analyze_prompt, SQL_INTENT_ANALYZE_META, ("2024年销售额", "---")),
    (build_sql_plan_prompt, SQL_PLAN_META, ("2024-09-01", "2024年销售额", "schema")),
    (build_sql_generate_prompt, SQL_GENERATE_META, ("2024-09-01", "销售额", ["区域"], [], "sum", "2024", "schema")),
]


@pytest.mark.parametrize(
    "builder,meta,args", _BUILDERS, ids=[m["name"] for _, m, _ in _BUILDERS]
)
def test_prompt_builder_records_version(builder, meta, args):
    """每个 build_xxx_prompt 调用时把自身 META 版本记录到当前 tracer（P13 prompt version 可追踪）。"""
    t = Tracer(trace_id="t-pv")
    token = sdk._current_tracer.set(t)
    try:
        builder(*args)
    finally:
        sdk._current_tracer.reset(token)

    assert t._prompt_versions, f"{meta['name']} 未记录 prompt version"
    recorded = t._prompt_versions[-1]
    assert recorded["name"] == meta["name"]
    assert recorded["version"] == meta["version"]


def test_record_prompt_version_without_tracer_is_noop():
    """无 current tracer 时静默跳过（工具 / 纯函数路径不崩、不记）。"""
    token = sdk._current_tracer.set(None)
    try:
        sdk.record_prompt_version("some_prompt", 1)  # 不抛
    finally:
        sdk._current_tracer.reset(token)