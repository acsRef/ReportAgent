"""长期记忆槽位（`memory_key`）契约钉子。

plan `2026-11-09-long-term-memory-table`：`memory.long_term_memory` 的唯一键是
`(user_id, memory_key)`，所以「同一维度两条矛盾偏好同时 active」在结构上就不可能发生。
槽位归类必须是**确定性**的（可测、可解释、无模型彩票），本文件钉住归类规则：
偏好按维度（chart / granularity），其余按内容指纹。
"""
from __future__ import annotations

import pytest

from app.memory.preferences import memory_key

pytestmark = pytest.mark.contracts


@pytest.mark.parametrize(
    "text",
    [
        "以后都用柱状图显示",
        "以后都用饼图显示",
        "以后报告都用表格",
        "以后都用条形图",
    ],
)
def test_chart_preferences_share_one_slot(text):
    """所有呈现形态偏好落**同一个**槽位——换图就是覆盖，不是并存。"""
    assert memory_key(text) == "pref:chart"


@pytest.mark.parametrize(
    "text",
    ["以后按月的粒度来显示", "以后每周看一下", "默认按季度"],
)
def test_granularity_preferences_share_one_slot(text):
    assert memory_key(text) == "pref:granularity"


def test_dimensions_do_not_collide():
    """图表与粒度是两个槽位：换图不该顶掉粒度偏好。"""
    assert memory_key("以后都用饼图") != memory_key("以后按月的粒度来显示")


def test_unclassified_memory_uses_content_fingerprint():
    """无维度的偏好/事实落内容指纹：同句去重、异句共存。"""
    a = memory_key("净销售额是扣退款后的口径")
    b = memory_key("净销售额是扣退款后的口径")
    c = memory_key("退货率按订单数算")
    assert a == b, "同一句必须同槽（去重）"
    assert a != c, "不同句必须不同槽（不误合并）"
    assert a.startswith("fact:")


def test_fingerprint_ignores_whitespace_and_punctuation():
    """只做「换空格/标点」这一层归一；不做同义归并（那是语义层的事）。"""
    assert memory_key("净销售额是扣退款后的口径") == memory_key("净销售额是扣退款后的口径。")
    assert memory_key("净销售额是 扣退款后的口径") == memory_key("净销售额是扣退款后的口径")


def test_slot_is_stable_across_calls():
    """槽位不能随机化——否则唯一键失效、去重与覆盖全崩。"""
    assert memory_key("以后都用柱状图") == memory_key("以后都用柱状图")
