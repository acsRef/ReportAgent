"""P16 D3：apply_chart_preference 纯函数契约（离线，无 DB）。

chart 偏好是「记忆 → Report 行为」的确定性通道：ContextRuntime 召回的 preference
（source=memory_preference）经本函数落到 chart_config。钉死：
- 词表交集 {pie, bar, table}，词表外偏好诚实忽略（产品不支持的类型不产生效果）
- 软约束 + 数据硬约束（§七 Conflict Priority：数据不支持可视化时偏好让位）
- 跨类型 override 同步归一 dimensions 键（bar 用 x/y、pie 用 category/value）
- 纯函数：不 mut 输入
"""
from __future__ import annotations

import copy

import pytest

from app.report.preference import apply_chart_preference


def _pie(rows=None):
    rows = rows if rows is not None else [
        {"region": "华东", "sales": "100"},
        {"region": "华南", "sales": "90"},
    ]
    return {
        "type": "pie",
        "config": {"data": rows, "dimensions": {"category": "region", "value": "sales"}},
    }


def _bar(rows=None):
    rows = rows if rows is not None else [
        {"region": "华东", "sales": "100"},
        {"region": "华南", "sales": "90"},
    ]
    return {
        "type": "bar",
        "config": {"data": rows, "dimensions": {"x": "region", "y": "sales"}},
    }


def _table():
    return {"type": "table", "config": {"data": [{"a": "1"}]}}


class TestApplyChartPreference:
    def test_pie_to_bar_override_and_normalize_dims(self):
        cfg = apply_chart_preference(_pie(), ["用户偏好：报告优先使用柱状图"])
        assert cfg["type"] == "bar"
        assert cfg["config"]["dimensions"] == {"x": "region", "y": "sales"}
        # data rows 原样保留（provenance 不丢）
        assert cfg["config"]["data"][0]["region"] == "华东"

    def test_bar_to_pie_override_and_normalize_dims(self):
        cfg = apply_chart_preference(_bar(), ["以后图表都用饼图"])
        assert cfg["type"] == "pie"
        assert cfg["config"]["dimensions"] == {"category": "region", "value": "sales"}

    def test_no_preferences_identity(self):
        cfg = _pie()
        assert apply_chart_preference(cfg, []) is cfg
        assert apply_chart_preference(cfg, None) is cfg

    def test_out_of_vocab_preference_ignored(self):
        # 折线图 = chart_advisor 词表外；金额格式 = 非类型偏好——都不产生效果
        for pref in ["用户喜欢折线图", "金额保留两位小数", "以后都按月汇总"]:
            cfg = _pie()
            assert apply_chart_preference(cfg, [pref]) is cfg

    def test_wide_substring_not_matched(self):
        # P16-LOW：单字「表」不 alias table——「图表更直观」这类偏好不得误判成表格
        for pref in ["以后图表展示尽量简洁", "图表可以更直观一些", "报告中的图表需要简洁"]:
            cfg = _pie()
            assert apply_chart_preference(cfg, [pref]) is cfg
        # 「表格」完整词仍应命中
        cfg = apply_chart_preference(_pie(), ["报告只要表格"])
        assert cfg["type"] == "table"

    def test_english_chart_words_recognized(self):
        cfg = apply_chart_preference(_pie(), ["prefer bar charts"])
        assert cfg["type"] == "bar"
        assert cfg["config"]["dimensions"] == {"x": "region", "y": "sales"}

    def test_table_preference_downgrades_visual(self):
        cfg = apply_chart_preference(_pie(), ["报告只要表格"])
        assert cfg["type"] == "table"
        assert cfg["config"]["data"][0]["region"] == "华东"  # rows 保留
        assert "dimensions" not in cfg["config"]

    def test_visual_preference_yields_to_undrawable_data(self):
        # chart_advisor 给 table = 数据无分类+数值组合，不支持可视化——偏好让位（§七）
        cfg = apply_chart_preference(_table(), ["以后都用柱状图"])
        assert cfg["type"] == "table"

    def test_agreement_keeps_type_and_other_fields(self):
        cfg = apply_chart_preference(_pie(), ["喜欢用饼图展示"])
        assert cfg["type"] == "pie"
        assert cfg["config"]["dimensions"] == {"category": "region", "value": "sales"}

    def test_first_matching_preference_wins(self):
        # recall 以 score 降序传入；先命中 = 最相关偏好
        cfg = apply_chart_preference(_bar(), ["报告优先饼图", "另外用柱状图也行"])
        assert cfg["type"] == "pie"
        cfg2 = apply_chart_preference(_pie(), ["以后用柱状图", "偶尔也可以饼图"])
        assert cfg2["type"] == "bar"

    def test_input_not_mutated(self):
        original = _pie()
        before = copy.deepcopy(original)
        apply_chart_preference(original, ["以后都用柱状图"])
        assert original == before

    def test_malformed_chart_config_defensive(self):
        assert apply_chart_preference({}, ["柱状图"]) == {}
        assert apply_chart_preference({"type": "bar", "config": None}, ["柱状图"])["type"] == "bar"
        assert apply_chart_preference({"type": "sankey", "config": {}}, ["柱状图"])["type"] == "sankey"

    def test_dims_extra_keys_preserved(self):
        cfg = _pie()
        cfg["config"]["dimensions"]["extra"] = "keep-me"
        out = apply_chart_preference(cfg, ["柱状图"])
        assert out["config"]["dimensions"] == {"x": "region", "y": "sales", "extra": "keep-me"}
