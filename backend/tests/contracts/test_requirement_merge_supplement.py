"""P15 e2e T2：supplement prior_card 合并矩阵（parse_requirement 纯逻辑）。

presence = 本轮明确改写（新值非空→覆盖 prior）；absence = 未提及（空→继承 prior）。
mock call_llm 固定输出，不触 DB/LLM。钉 5 行矩阵：
 再看月度趋势→继承 / 改成2025→time 覆盖 / 改看华南→scope 覆盖 /
 对比同比→comparison 新增 / 全没提→完整继承。
"""
from __future__ import annotations

import json

import pytest

from app.agent.requirement_parser import parse_requirement
from app.models.requirement import RequirementCard

pytestmark = pytest.mark.contracts


def _prior() -> RequirementCard:
    return RequirementCard(
        id="draft-prior", version=1, status="complete",
        summary="2024年华东销售额",
        target_metrics=["销售额"], time_range="2024年", scope=["华东"],
        dimensions=["时间", "区域"], analysis_methods=["group_compare"],
        missing_fields=[], assumptions=[],
    )


def _parse(monkeypatch, query: str, llm_out: dict) -> RequirementCard:
    monkeypatch.setattr(
        "app.agent.requirement_parser.call_llm",
        lambda prompt, max_tokens=0: json.dumps(llm_out, ensure_ascii=False),
    )
    return parse_requirement(user_query=query, schema_context=None, prior_card=_prior())


def _missing_keys(card: RequirementCard) -> list[str]:
    return [m.key for m in card.missing_fields]


@pytest.mark.parametrize(
    "name,query,llm_out,exp_time,exp_scope,exp_metrics,exp_missing,exp_status",
    [
        (
            "再看月度趋势→继承 time/scope/metric，granularity=月",
            "再看月度趋势",
            {
                "summary": "华东销售额按月趋势", "target_metrics": [], "time_range": None,
                "scope": [], "dimensions": ["时间", "粒度:月"],
                "analysis_methods": ["trend_analysis"], "confidence": 0.9,
                "missing_fields": [], "assumptions": [],
            },
            "2024年", ["华东"], ["销售额"], [], "complete",
        ),
        (
            "改成 2025 年→time 覆盖，scope/metric 继承",
            "改成 2025 年的月度趋势",
            {
                "summary": "2025年华东销售额按月趋势", "target_metrics": [], "time_range": "2025年",
                "scope": [], "dimensions": ["时间", "粒度:月"],
                "analysis_methods": ["trend_analysis"], "confidence": 0.9,
                "missing_fields": [], "assumptions": [],
            },
            "2025年", ["华东"], ["销售额"], [], "complete",
        ),
        (
            "改看华南→scope 覆盖，time/metric 继承",
            "改看华南",
            {
                "summary": "华南销售额", "target_metrics": [], "time_range": None,
                "scope": ["华南"], "dimensions": ["区域"],
                "analysis_methods": [], "confidence": 0.8,
                "missing_fields": [], "assumptions": [],
            },
            "2024年", ["华南"], ["销售额"], [], "complete",
        ),
        (
            "再对比去年同比→comparison 新增，其余继承",
            "再对比去年同比",
            {
                "summary": "2024年华东销售额同比", "target_metrics": [], "time_range": None,
                "scope": [], "dimensions": ["时间", "对比:同比"],
                "analysis_methods": ["group_compare"], "confidence": 0.85,
                "missing_fields": [], "assumptions": [],
            },
            "2024年", ["华东"], ["销售额"], [], "complete",
        ),
        (
            "本轮什么都没提→完整继承，无 missing",
            "继续",
            {
                "summary": "2024年华东销售额", "target_metrics": [], "time_range": None,
                "scope": [], "dimensions": [], "analysis_methods": [],
                "confidence": 0.5, "missing_fields": [], "assumptions": [],
            },
            "2024年", ["华东"], ["销售额"], [], "complete",
        ),
    ],
)
def test_supplement_prior_card_merge_matrix(
    monkeypatch, name, query, llm_out,
    exp_time, exp_scope, exp_metrics, exp_missing, exp_status,
):
    card = _parse(monkeypatch, query, llm_out)
    assert card.version == 2, "version 应 prior.version+1"
    assert card.time_range == exp_time, f"[{name}] time_range"
    assert card.scope == exp_scope, f"[{name}] scope"
    assert card.target_metrics == exp_metrics, f"[{name}] target_metrics"
    assert _missing_keys(card) == exp_missing, f"[{name}] missing_fields"
    assert card.status == exp_status, f"[{name}] status"


def test_supplement_new_metric_not_overridden_by_prior(monkeypatch):
    """presence 覆盖：本轮明确换指标 → 覆盖 prior 的 metric（不继承旧的）。"""
    card = _parse(
        monkeypatch,
        "改成看退货率",
        {
            "summary": "退货率", "target_metrics": ["退货率"], "time_range": None,
            "scope": [], "dimensions": [], "analysis_methods": [],
            "confidence": 0.9, "missing_fields": [], "assumptions": [],
        },
    )
    assert card.target_metrics == ["退货率"], "本轮明确指标应覆盖 prior 销售额"
    assert card.time_range == "2024年", "未改写的时间仍继承"
