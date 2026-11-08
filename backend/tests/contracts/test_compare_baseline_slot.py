"""对比基线槽位（plan 2026-11-08-analysis-quality-cleanup D1）。

真机实测：用户说「销售额同比怎么样」→ 卡上 `dimensions=['时间']`、SQL 只是 12 个月的量，
**一点同比都没有**；说「对比一下各区域的销售额」→ 也只是分组求和（横截面互比）。
`_dim_kind` / `comparison_options` / `patch_requirement` 的分支都在，缺的是
「这一轮到底跟谁比」没人判断。
"""
from __future__ import annotations

import pytest

from app.agent.requirement_parser import _ensure_comparison_slot
from app.models.requirement import RequirementCard

pytestmark = pytest.mark.contracts


def _card() -> RequirementCard:
    return RequirementCard(
        id="c1",
        version=1,
        status="complete",
        summary="销售额",
        target_metrics=["销售额"],
        time_range="2024年",
        granularity="month",
        dimensions=["时间"],
    )


def _comparison(card: RequirementCard) -> list[str]:
    return [dim for dim in card.dimensions if "对比" in dim or "同比" in dim or "环比" in dim]


class TestExplicitBaseline:
    @pytest.mark.parametrize("query", [
        "销售额同比怎么样",
        "和去年同期比一下销售额",
        "看下同比",
    ])
    def test_明说同比就落标签不再问(self, query: str) -> None:
        card = _card()
        _ensure_comparison_slot(card, query)

        assert _comparison(card) == ["对比:同比"]
        assert not any(mf.key == "comparison" for mf in card.missing_fields)

    @pytest.mark.parametrize("query", ["销售额环比怎么样", "跟上个月比"])
    def test_明说环比(self, query: str) -> None:
        card = _card()
        _ensure_comparison_slot(card, query)
        assert _comparison(card) == ["对比:环比"]


class TestVagueCompare:
    def test_只说对比就要问基线(self) -> None:
        """「对比一下各区域的销售额」——跟谁比？同比、环比、还是干脆不对比。"""
        card = _card()
        _ensure_comparison_slot(card, "对比一下各区域的销售额")

        keys = [mf.key for mf in card.missing_fields]
        assert "comparison" in keys
        field = next(mf for mf in card.missing_fields if mf.key == "comparison")
        labels = [option.label for option in field.options]
        assert any("同比" in label for label in labels)
        assert any("不对比" in label for label in labels)   # 不能强迫用户选

    def test_已经说了基线就不问(self) -> None:
        card = _card()
        _ensure_comparison_slot(card, "对比一下和华南的差异")

        assert not any(mf.key == "comparison" for mf in card.missing_fields)


class TestNoTouch:
    @pytest.mark.parametrize("query", [
        "2023年各区域销售额排名",
        "看下销售趋势",
        "把订单列出来看看",
    ])
    def test_没有对比诉求时不动卡(self, query: str) -> None:
        card = _card()
        before = list(card.dimensions)
        _ensure_comparison_slot(card, query)

        assert card.dimensions == before
        assert not card.missing_fields

    def test_已有对比标签不重复加(self) -> None:
        card = _card()
        card.dimensions = ["时间", "对比:同比"]
        _ensure_comparison_slot(card, "销售额同比")

        assert _comparison(card) == ["对比:同比"]


class TestComparisonContract:
    """说了同比就必须真算（prompt 治不住采样，靠契约 + repair）。

    实测：同一条 prompt 下「区域对比 + 同比」模型照做，而「按月趋势 + 同比」连续两次
    都只给月度序列——所以在 validate 之后加一道契约，违规走既有 repair 环。
    """

    def test_没要求对比时不拦(self) -> None:
        from app.agent.sql_graph import _comparison_contract_violation

        assert _comparison_contract_violation("SELECT month FROM t", None) is None

    def test_只给当期序列判违规(self) -> None:
        from app.agent.sql_graph import _comparison_contract_violation

        violation = _comparison_contract_violation(
            "SELECT dim_date.month AS 月份, SUM(fact_orders.order_amount) AS 销售额 "
            "FROM fact_orders GROUP BY 1 ORDER BY 1",
            "同比（今年与去年同期同月份比）",
        )

        assert violation is not None
        assert "同比" in violation
        assert "变化率" in violation

    @pytest.mark.parametrize("sql", [
        "SELECT m, LAG(v, 12) OVER (ORDER BY m) AS 同比 FROM t",
        "SELECT c.m, c.v, l.v AS 去年同期值 FROM cur c JOIN last_year l ON l.m = c.m",
        "SELECT m, v, (v - prev_v) / prev_v AS 环比变化率 FROM t",
        "SELECT m, v AS 同比增长 FROM t",
    ])
    def test_认得出对比写法(self, sql: str) -> None:
        from app.agent.sql_graph import _comparison_contract_violation

        assert _comparison_contract_violation(sql, "同比") is None


class TestPatchTranslation:
    """澄清项回传 yoy/mom 时，落卡要变成中文标签（执行层认得出同比还是环比）。"""

    def test_yoy_落中文标签(self) -> None:
        from app.services.requirement_service import _COMPARISON_LABELS

        assert _COMPARISON_LABELS["yoy"] == "同比"
        assert _COMPARISON_LABELS["mom"] == "环比"
        assert "none" not in _COMPARISON_LABELS   # 「不对比」不落标签
