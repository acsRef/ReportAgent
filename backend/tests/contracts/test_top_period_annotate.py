"""标注 ≠ 只看（plan 2026-11-08-top-period-annotate-and-timeout-copy A1）。

用户说「最高的那两个月，标注一下」时，要的是**全量 + 标出哪两期**。
此前卡上只有 `direction/unit` 一个语义，下游只能按「只取那两期」实现，
`_top_period_contract_violation` 还会把不带该期过滤的 SQL 判成校验失败 → repair 预算耗尽。

本组用例钉住三条线：
1. 判定是**确定性**的（词表 + 产卡后原话归一），模型给什么都不算数；
2. 生成指令与契约校验在 annotate 下**反向**：必须有标记列、不许退化成「只看」；
3. `select` 的既有行为一字不变（旧卡零迁移）。
"""
from __future__ import annotations

import pytest

from app.agent import confirmed_execution_graph, requirement_parser, sql_graph
from app.models.requirement import RequirementCard, RequirementTopPeriod

pytestmark = pytest.mark.contracts


def _card(*, purpose: str = "select", detail: bool = True, direction: str = "max") -> RequirementCard:
    return RequirementCard(
        id="draft-test",
        version=1,
        status="complete",
        summary="2023 年销售额最高的那两个月的明细",
        target_metrics=["销售额"],
        time_range="2023",
        granularity="month",
        detail_level="detail" if detail else "aggregate",
        top_period=RequirementTopPeriod(
            direction=direction, unit="month", metric="销售额", purpose=purpose,
        ),
    )


# ── 1. 判定 ───────────────────────────────────────────────────────────────


class TestPurposeDetection:
    @pytest.mark.parametrize("query", [
        "2023年最高的那两个月的明细，标注一下",
        "销售额最高的那个月标出来",
        "把最低的那几周标记一下",
        "最高的月份注明一下",
    ])
    def test_带标注词判成_annotate(self, query: str) -> None:
        spec = requirement_parser._top_period_from_query(query)
        assert spec is not None
        assert spec["purpose"] == "annotate"

    @pytest.mark.parametrize("query", [
        "2023年里销售额最高的那个月的明细",
        "销售额最高的那个月是哪个月",
        "看下最低的那一周",
    ])
    def test_没提标注保持_select(self, query: str) -> None:
        spec = requirement_parser._top_period_from_query(query)
        assert spec is not None
        assert spec["purpose"] == "select"

    def test_原话归一覆盖卡上的值(self) -> None:
        """模型把 purpose 给成 select，也要按用户原话纠正成 annotate。"""
        from app.agent.requirement_analysis_graph import _normalize_card_top_period

        card = _card(purpose="select")
        fixed = _normalize_card_top_period(card, "2023年最高的那两个月的明细，标注一下")

        assert fixed is not None
        assert fixed.top_period is not None
        assert fixed.top_period.purpose == "annotate"
        # 卡其余字段不动
        assert fixed.id == card.id and fixed.time_range == "2023"

    def test_没有_top_period_时不动卡(self) -> None:
        from app.agent.requirement_analysis_graph import _normalize_card_top_period

        card = RequirementCard(id="x", version=1, status="complete", summary="s")
        # 原话里没有任何「极值 + 期」的迹象 → 不凭空造一个 top_period
        assert _normalize_card_top_period(card, "帮我看看销售情况") is card

    def test_模型漏给时按原话补建(self) -> None:
        """同一句两次跑、一次给一次不给（采样波动）——确定性识别要能兜住。"""
        from app.agent.requirement_analysis_graph import _normalize_card_top_period

        card = RequirementCard(
            id="x", version=1, status="complete", summary="s",
            target_metrics=["销售额"], time_range="2023", detail_level="detail",
        )
        fixed = _normalize_card_top_period(
            card, "2023年销售额最高的那两个月的明细，标注一下",
        )

        assert fixed is not card
        assert fixed.top_period is not None
        assert fixed.top_period.direction == "max"
        assert fixed.top_period.unit == "month"
        assert fixed.top_period.purpose == "annotate"
        assert fixed.top_period.metric == "销售额"


# ── 2. 生成指令 ───────────────────────────────────────────────────────────


class TestHint:
    def test_annotate_要求标记列且禁止过滤(self) -> None:
        resolved = type("R", (), {
            "ok": True,
            "label": "2023年11月",
            "filters": ("dim_date.year = 2023", "dim_date.month = 11"),
            "entries": (),
            "period_value": "11",
            "period_column": "dim_date.month",
        })()
        hint = confirmed_execution_graph._top_period_hint(_card(purpose="annotate"), resolved)

        assert hint is not None
        assert "是否最高期" in hint          # 标记列名固定
        assert "CASE WHEN" in hint
        assert "全区间" in hint
        assert "不要用它过滤数据" in hint.replace("**", "")  # 明确禁止只取该期
        assert "不要按标记列排序" in hint.replace("**", "")   # 排序会把未标记的行挤掉
        assert "LIMIT 1" not in hint         # 已算出期就不该再让模型自己选

    def test_select_行为不变(self) -> None:
        resolved = type("R", (), {
            "ok": True,
            "label": "2023年11月",
            "filters": ("dim_date.year = 2023", "dim_date.month = 11"),
            "entries": (),
            "period_value": "11",
            "period_column": "dim_date.month",
        })()
        hint = confirmed_execution_graph._top_period_hint(_card(purpose="select"), resolved)

        assert hint is not None
        assert "dim_date.month = 11" in hint
        assert "禁止" in hint                # 仍禁止把全区间汇总
        assert "是否最高期" not in hint       # 不出现标注列

    def test_annotate_算不出期时走两段式并强调回到全区间(self) -> None:
        hint = confirmed_execution_graph._top_period_hint(_card(purpose="annotate"), None)

        assert hint is not None
        assert "LIMIT 1" in hint
        assert "是否最高期" in hint
        assert "不 JOIN 过滤" in hint


# ── 3. 契约校验 ───────────────────────────────────────────────────────────


_ANNOTATED_SQL = """
SELECT dim_date.month AS "月份",
       SUM(fact_orders.order_amount) AS "销售额",
       CASE WHEN dim_date.month IN (4, 11) THEN '是' ELSE '否' END AS "是否最高期"
  FROM fact_orders JOIN dim_date ON dim_date.date_key = fact_orders.date_key
 WHERE dim_date.year = 2023
 GROUP BY dim_date.month
"""

_ANNOTATED_DETAIL_SQL = """
SELECT fact_orders.order_id, dim_date.date_key,
       fact_orders.order_amount,
       CASE WHEN dim_date.month = 11 THEN '是' ELSE '否' END AS "是否最高期"
  FROM fact_orders JOIN dim_date ON dim_date.date_key = fact_orders.date_key
 WHERE dim_date.year = 2023
 ORDER BY dim_date.date_key DESC
 LIMIT 200
"""

_SPEC = {
    "direction": "max", "unit": "month", "resolved": True, "purpose": "annotate",
    "period_column": "dim_date.month", "period_value": "11", "period_values": ["11"],
    "filters": ["dim_date.year = 2023", "dim_date.month = 11"], "label": "2023年11月",
}


class TestContract:
    def test_带标记列算通过(self) -> None:
        assert sql_graph._top_period_contract_violation(_ANNOTATED_SQL, _SPEC) is None
        assert sql_graph._top_period_contract_violation(_ANNOTATED_DETAIL_SQL, _SPEC) is None

    def test_没有标记列判违规(self) -> None:
        sql = "SELECT dim_date.month, SUM(order_amount) FROM fact_orders GROUP BY 1"
        violation = sql_graph._top_period_contract_violation(sql, _SPEC)

        assert violation is not None
        assert "标记列" in violation
        assert "是否最高期" in violation

    def test_case_when_但别名不约定判违规(self) -> None:
        sql = (
            'SELECT dim_date.month, CASE WHEN dim_date.month = 11 THEN 1 ELSE 0 END AS "flag" '
            "FROM fact_orders"
        )
        violation = sql_graph._top_period_contract_violation(sql, _SPEC)

        assert violation is not None
        assert "列名不合约定" in violation

    def test_select_仍要求期过滤(self) -> None:
        """回归：select 的契约不能因为 annotate 分支被放宽。"""
        spec = dict(_SPEC, purpose="select")
        no_filter = "SELECT dim_date.month, SUM(order_amount) FROM fact_orders GROUP BY 1"
        violation = sql_graph._top_period_contract_violation(no_filter, spec)

        assert violation is not None
        assert "过滤条件" in violation
        assert sql_graph._top_period_contract_violation(_ANNOTATED_DETAIL_SQL, spec) is None

    def test_默认_purpose_是_select(self) -> None:
        """缺 purpose 的老数据必须走老路径（零迁移）。"""
        spec = {k: v for k, v in _SPEC.items() if k != "purpose"}
        no_filter = "SELECT dim_date.month FROM fact_orders"
        assert sql_graph._top_period_contract_violation(no_filter, spec) is not None


class TestConstantColumnGuard:
    """标注列不能被「常量列清理」删掉（2026-11-08 真机踩到）。

    明细按时间倒序只取最近 200 条，若这 200 条里一条「是」都没有，
    「是否最高期」就是常量列 → 被 `_drop_constant_columns` 删掉 →
    用户在界面上完全看不到自己要的标注。
    """

    def test_标注列恒定也保留(self) -> None:
        from app.agent.confirmed_execution_graph import _drop_constant_columns

        columns = [{"key": "日期", "title": "日期"}, {"key": "是否最高期", "title": "是否最高期"}]
        rows = [
            {"日期": "2023-12-31", "是否最高期": "否"},
            {"日期": "2023-12-30", "是否最高期": "否"},
        ]

        kept = _drop_constant_columns(columns, rows, keep={"是否最高期"})

        assert [c["key"] for c in kept] == ["日期", "是否最高期"]

    def test_其它常量列仍被删(self) -> None:
        from app.agent.confirmed_execution_graph import _drop_constant_columns

        columns = [
            {"key": "区域", "title": "区域"},
            {"key": "日期", "title": "日期"},
            {"key": "是否最高期", "title": "是否最高期"},
        ]
        rows = [
            {"区域": "华南", "日期": "2023-12-31", "是否最高期": "否"},
            {"区域": "华南", "日期": "2023-12-30", "是否最高期": "否"},
        ]

        kept = _drop_constant_columns(columns, rows, keep={"是否最高期"})

        assert [c["key"] for c in kept] == ["日期", "是否最高期"]

    def test_只有_annotate_才保护标记列(self) -> None:
        from app.agent.confirmed_execution_graph import _annotation_columns

        assert _annotation_columns({"top_period": {"purpose": "annotate"}}) == {"是否最高期"}
        assert _annotation_columns(
            {"top_period": {"purpose": "annotate", "direction": "min"}}
        ) == {"是否最低期"}
        assert _annotation_columns({"top_period": {"purpose": "select"}}) == set()
        assert _annotation_columns({}) == set()

    def test_state_断掉时按列名形状兜底(self) -> None:
        """真机踩到：state 里的 top_period 没传到报告节点 → 标注列被当常量删掉。

        所以除了读 state，还要按「是否…期」这个固定形状认出来。
        """
        from app.agent.confirmed_execution_graph import _annotation_columns

        columns = [{"key": "日期", "title": "日期"}, {"key": "是否最高期", "title": "是否最高期"}]

        assert _annotation_columns({}, columns) == {"是否最高期"}          # state 空也要认出
        assert _annotation_columns({"top_period": {"purpose": "select"}}, columns) == {"是否最高期"}
        assert _annotation_columns({}, [{"key": "日期", "title": "日期"}]) == set()


class TestPeriodBounds:
    """期值算错的真机 bug（2026-11-08 探针抓到的）。

    卡上 `time_range` 被规范化成纯年份「2023」，而 `_YEAR` 要求带「年」字 →
    `resolve_period_bounds` 返回 None → 选期按全库算 → 用户问 2023，
    SQL 里标出的却是 `year = 2024 AND month = 6`（全库峰值年）。
    """

    @pytest.mark.parametrize("text", ["2023", "2023年", " 2023 ", "2023 年"])
    def test_纯年份与带年字等价(self, text: str) -> None:
        import datetime

        from app.services.metric_preview_service import resolve_period_bounds

        assert resolve_period_bounds(text) == (
            datetime.date(2023, 1, 1), datetime.date(2024, 1, 1),
        )

    def test_年月区间仍优先(self) -> None:
        import datetime

        from app.services.metric_preview_service import resolve_period_bounds

        assert resolve_period_bounds("2023年11月") == (
            datetime.date(2023, 11, 1), datetime.date(2023, 12, 1),
        )

    def test_带后缀的年份数字不被当成整年(self) -> None:
        from app.services.metric_preview_service import resolve_period_bounds

        # 「2023-11」这种半残写法不能被宽松匹配吞成整年（交给上层按未识别处理）
        assert resolve_period_bounds("202311") is None
