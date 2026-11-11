"""报告层「结论别算错列、数字别误杀」（2026-11-03，plan 2026-11-03-anomaly-granularity）。

两个实测缺陷：
1. 按周聚合的结果列是 `[周(int), 销售额]`，`_fallback_headline` 取「第一个数值列」
   → 结论写成「周合计 1,484.00」（把周序号 1..53 求和）；
2. 数字锚定只收**单元格值**，于是「全年合计 2.63 亿」这种派生但真实的结论一律被判
   编造 → headline 被打回确定性兜底、日/周长序列报告的 findings 几乎全被丢掉。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.agent.report_graph import _collect_data_numbers, _fallback_headline, _numbers_valid

pytestmark = pytest.mark.graphs


class _QR:
    """最小 QueryResult 替身（_fallback_headline 只读 rows / row_count）。"""

    def __init__(self, rows: list[dict], columns: list[str] | None = None) -> None:
        self.rows = rows
        self.row_count = len(rows)
        self.columns = columns or (list(rows[0].keys()) if rows else [])


WEEKLY = [
    {"周": 1, "销售额": Decimal("100")},
    {"周": 2, "销售额": Decimal("300")},
    {"周": 7, "销售额": Decimal("50")},
]

MONTHLY = [
    {"月份": 1, "销售额": Decimal("2394")},
    {"月份": 2, "销售额": Decimal("1506")},
]


# --- 结论：度量列不许是时间维度 ---------------------------------------------


def test_fallback_headline_ignores_week_column() -> None:
    headline = _fallback_headline(_QR(WEEKLY))

    assert "周合计" not in headline, headline
    assert "销售额合计 450.00" in headline
    assert "第2周 最高" in headline  # 退回到时间维度当标签，且写成「第N周」


def test_fallback_headline_ignores_month_column() -> None:
    headline = _fallback_headline(_QR(MONTHLY))

    assert "销售额合计 3,900.00" in headline
    assert "1月 最高" in headline


def test_fallback_headline_prefers_text_dimension() -> None:
    rows = [
        {"区域": "华东", "year": 2024, "实收金额": Decimal("9040")},
        {"区域": "西北", "year": 2024, "实收金额": Decimal("652")},
    ]

    headline = _fallback_headline(_QR(rows))

    assert headline.startswith("实收金额合计 9,692.00")
    assert "华东 最高" in headline


def test_fallback_headline_empty_rows() -> None:
    assert _fallback_headline(_QR([])) == ""


# --- NULL 分组不许冒充「最高的那个月」（2026-11-11，plan sql-filter-in-join-on） ---
#
# 真机报告核心发现写着「其中 None月 最高（731,121,265.40）」——那个 7.3 亿不是某个月，
# 是 `LEFT JOIN ... ON ... AND region='华南'` 过滤失效后所有不匹配订单的 NULL 分组。
# 更坏的是它会**赢下最高**，让整段结论建在一行垃圾上。

NULL_GROUP_ROWS = [
    {"月份": 1, "销售额": Decimal("21397609.64")},
    {"月份": 2, "销售额": Decimal("14190637.08")},
    {"月份": None, "销售额": Decimal("731121265.40")},  # 外连接过滤失效的垃圾桶行
]


def test_null_group_not_rendered_as_none() -> None:
    headline = _fallback_headline(_QR(NULL_GROUP_ROWS))

    assert "None" not in headline, headline
    assert "None月" not in headline


def test_null_group_never_wins_highest() -> None:
    """最高必须在**有标签**的分组里选：7.3 亿那行是 NULL 分组，不能当冠军。"""
    headline = _fallback_headline(_QR(NULL_GROUP_ROWS))

    assert "1月 最高" in headline, headline
    assert "731,121,265.40" not in headline.split("另有")[0]


def test_null_group_disclosed_separately() -> None:
    """空分组要摊在明面上说（几行、多少钱），而不是藏进一个漂亮的结论。"""
    headline = _fallback_headline(_QR(NULL_GROUP_ROWS))

    assert "另有 1 行" in headline
    assert "731,121,265.40" in headline
    assert "过滤" in headline or "连接" in headline


def test_all_null_group_does_not_invent_a_month() -> None:
    rows = [{"月份": None, "销售额": Decimal("100")}]

    headline = _fallback_headline(_QR(rows))

    assert "最高" not in headline, headline
    assert "全部为空" in headline


# --- 数字锚定：派生值（合计/均值）也要能过 -----------------------------------


def test_collect_data_numbers_includes_column_total_and_mean() -> None:
    values = _collect_data_numbers(WEEKLY)

    assert 450.0 in values          # 合计
    assert pytest.approx(150.0) == min(
        v for v in values if abs(v - 150.0) < 1e-9
    )                               # 均值
    assert 300.0 in values          # 单元格值


def test_total_headline_passes_grounding() -> None:
    """整年合计是派生值——过去会被判「编造」，现在必须放行。"""
    values = _collect_data_numbers(WEEKLY)

    assert _numbers_valid("2024 年销售额合计 450.00 元", values)
    assert _numbers_valid("平均每周 150 元", values)


def test_fabricated_number_still_rejected() -> None:
    values = _collect_data_numbers(WEEKLY)

    assert not _numbers_valid("销售额合计 999999.00 元", values)


def test_percentages_are_not_grounded() -> None:
    """百分比是派生量，不参与锚定（一直如此，防回归）。"""
    assert _numbers_valid("环比下降 37.2%", [])
