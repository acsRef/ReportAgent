"""记忆归并判定矩阵（结构优先，语义只进灰区）。

plan `2026-11-10-semantic-memory-merge`：先测后设计——真 embedding 上
「同义 0.695~0.933 / 不同的事 0.642~0.950 / 相反说法 0.906~0.962」三类**重叠**，
所以判定器不看相似度排序，而看**结构**：极性 → 数字 → 原子值冲突 → 原子值一致 → 灰区。

本文件把 `evaluation/semantic_merge_probe.py` 的标注对浓缩成可回归的矩阵。
"""
from __future__ import annotations

import pytest

from app.memory.merge import atoms, decide, numbers, polarity

pytestmark = pytest.mark.contracts


# --------------------------------------------------------------- 应当合并

@pytest.mark.parametrize(
    "a,b",
    [
        ("以后都用柱状图显示", "图表默认用柱形图"),
        ("以后都用柱状图显示", "报告里用条形图就行"),
        ("以后都用饼图", "以后报告用饼状图"),
        ("以后都用表格", "以后报告直接给表格吧"),
        ("以后按月的粒度来显示", "以后每个月看一次"),
        ("以后按季度看", "默认按季汇总"),
        ("净销售额是扣退款后的口径", "净销售额等于销售额减掉退款"),
        ("华南包含福建", "福建算在华南区域里"),
    ],
)
def test_same_meaning_merges(a, b):
    """同义说法 → `merge`，**不依赖余弦**（最低的一对只有 0.695）。"""
    assert decide(a, b)[0] == "merge"
    assert decide(a, b)[1] == "atoms-equal"


# ------------------------------------------------------------- 必须分开

@pytest.mark.parametrize(
    "a,b,reason",
    [
        # 相反说法：字面相似度最高（0.906~0.962），但极性相反
        ("以后都用柱状图", "以后不要用柱状图", "polarity"),
        ("以后报告都用表格", "以后报告别用表格", "polarity"),
        ("以后按月看", "以后不要按月看", "polarity"),
        # 不同取值（同 kind）
        ("华东区域的销售额怎么算", "华南区域的销售额怎么算", "atom-conflict:region"),
        ("以后按周看", "以后按天看", "atom-conflict:granularity"),
        ("以后都用柱状图", "以后都用饼图", "atom-conflict:chart"),
        ("净销售额是扣退款后的口径", "实收金额是实际到账的口径", "atom-conflict:metric"),
        # 数字不同（数值是硬事实）
        ("华东 2024 年的销售额", "华东 2023 年的销售额", "numbers"),
    ],
)
def test_different_meaning_stays_distinct(a, b, reason):
    decision, why = decide(a, b)
    assert decision == "distinct"
    assert why == reason


def test_cross_kind_without_conflict_is_not_structural():
    """跨 kind（粒度 vs 呈现）没有冲突，但也不构成“结构一致”——不能被当成同义。"""
    decision, _ = decide("以后按月的粒度来显示", "以后都用柱状图显示")
    assert decision == "distinct"


# ------------------------------------------------------------- 灰区 review

def test_gray_zone_requires_similarity_and_never_merges():
    """没有结构信号时：余弦够高 → 只记待复审；余弦不够 → distinct。"""
    a, b = "报告尽量简洁", "报告写得简短一些"
    assert atoms(a) == {} and atoms(b) == {}
    assert decide(a, b)[0] == "distinct", "没有余弦输入时不得自动合并"
    assert decide(a, b, similarity=0.95)[0] == "review"
    assert decide(a, b, similarity=0.80)[0] == "distinct"


# --------------------------------------------------------------- 基础件

def test_atoms_extract_four_kinds():
    out = atoms("2024 年华东的净销售额，按周看，要柱状图")
    assert out["region"] == {"east"}
    assert out["granularity"] == {"week"}
    assert out["chart"] == {"bar"}
    assert "净销售额" in out["metric"]


def test_metric_atoms_come_from_business_registry():
    """指标同义词的真源是 `metrics.yaml` 的 synonyms，不是第二份词表。"""
    # 「单量」是 订单数 的 synonym
    assert "订单数" in atoms("单量怎么统计").get("metric", set())


def test_numbers_and_polarity_helpers():
    assert numbers("2024 年 3 月的 1.5 倍") == {"2024", "3", "1.5"}
    assert numbers("没有数字") == set()
    assert polarity("以后不要用柱状图") is True
    assert polarity("以后都用柱状图") is False
