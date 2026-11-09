"""记忆准入判定矩阵（契约钉子）。

plan `2026-11-09-memory-admission-and-consolidation`：写入侧原本没有准入——
`policy.extract_preference` 只答「像不像偏好」，写入端把结果硬编码成
`stable_preference / scope=user / 无 expires_at`，于是：

- 「以后都用柱状图」和「这次先按周看」生命周期完全相同；
- `temporary_preference` 在生产里永不产生（读取白名单里躺着它，写入侧没人写）。

本文件钉的是**纯函数**判定：`classify_scope`（作用域 + 期限 + 是否等批处理）
与 `is_admissible`（值不值得记的最小版）。不碰 DB。
"""
from __future__ import annotations

import pytest

from app.memory.admission import (
    DEFAULT_SESSION_TTL_HOURS,
    classify_scope,
    is_admissible,
)

pytestmark = pytest.mark.contracts


# --- 闸 2：长期标记 → user / stable / 无期限 / 当轮生效 -------------------------

@pytest.mark.parametrize(
    "text",
    [
        "以后都用柱状图显示",
        "以后报告都用表格",
        "默认按月的粒度来显示",
        "每次都用饼图",
        "一直按净销售额算",
    ],
)
def test_long_term_markers_become_user_scoped_active(text):
    adm = classify_scope(text)
    assert adm.reason == "long-term-marker", text
    assert (adm.scope, adm.memory_type, adm.status) == ("user", "stable_preference", "active")
    assert adm.ttl_hours is None
    assert adm.is_long_term


# --- 闸 2：会话标记 → session / temporary / 24h / 当轮生效 ---------------------

@pytest.mark.parametrize(
    "text",
    [
        "这次先按周看",
        "本次先用表格",
        "先按月的粒度来显示",
        "暂时按净销售额算",
        "这一回用饼图",
    ],
)
def test_session_markers_become_session_scoped_with_ttl(text):
    adm = classify_scope(text)
    assert adm.reason == "session-marker", text
    assert (adm.scope, adm.memory_type, adm.status) == (
        "session",
        "temporary_preference",
        "active",
    )
    assert adm.ttl_hours == DEFAULT_SESSION_TTL_HOURS
    assert not adm.is_long_term, "会话级不能算长期"


# --- 闸 2：无标记 → candidate（不召回，等固化攒证据） --------------------------

@pytest.mark.parametrize("text", ["柱状图挺好的", "我喜欢柱状图", "净销售额更准"])
def test_no_scope_marker_lands_in_candidate(text):
    adm = classify_scope(text)
    assert adm.reason == "no-scope-marker"
    assert adm.status == "candidate"
    assert adm.writes_active is False


def test_long_term_wins_when_both_markers_present():
    """同时出现两种标记时按长期算——「以后先按周看」的语义主体是「以后」。"""
    adm = classify_scope("以后先按周看吧")
    assert adm.reason == "long-term-marker"
    assert adm.scope == "user"


def test_long_term_marker_means_immediate_write():
    """长期标记必须当轮生效，不能等批处理。"""
    assert classify_scope("以后都用柱状图").writes_active is True


# --- 闸 1（最小版）：空 / 过短 / 纯寒暄 --------------------------------------

@pytest.mark.parametrize(
    "text,reason",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("看图", "too-short"),
        ("你好", "greeting"),
        ("thanks", "greeting"),
    ],
)
def test_inadmissible_texts(text, reason):
    ok, why = is_admissible(text)
    assert ok is False
    assert why == reason


def test_admissible_normal_preference():
    ok, why = is_admissible("以后都用柱状图显示")
    assert (ok, why) == (True, "ok")


# --- 抽取与准入必须**同时**命中会话级说法 -------------------------------------

@pytest.mark.parametrize(
    "text",
    ["这次先按周看", "本次先用表格", "暂时按净销售额算", "这一回用饼图"],
)
def test_policy_extracts_session_marker_wording(text):
    """回归钉：会话级说法在 `extract_preference` 就必须能抽出来。

    2026-11-09 真机 demo 抓到的真缺陷——抽取正则只认「以后/默认/每次」和
    「我喜欢…图表/格式」，于是「这次先按周看」在抽取阶段就 None，
    后面的准入闸根本没机会执行，`temporary_preference` 自然永远不产生。
    """
    from app.infra.memory.policy import MemoryPolicy

    entry = MemoryPolicy().extract_preference(text)
    assert entry is not None, f"抽取不到 → 准入永远不会被触发：{text}"
    assert entry.value, "抽出的正文不能是空串"
    adm = classify_scope(text)
    assert adm.reason == "session-marker"
    assert adm.scope == "session"
