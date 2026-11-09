"""召回排序契约：语义优先，热度只做**同分裁决**（`infra/memory/scoring.py`）。

背景（plan `2026-11-09-memory-recall-scoreboard` 追加）：gold scoreboard 的 `--warm others`
量出真问题——只把竞争者预热到 8 次访问，`Recall@1` 84%→36%、`Recall@3` 96%→56%（10/25 被挤掉）。
根因是权重结构允许辅助项跨语义带比较：`0.1×LFU + 0.1×LRU ≈ 0.12`，而 `0.12 / 0.6 = 0.33`，
即「竞争者语义不比金标低 0.33 也能反超」。

修法 B：把 primary（语义 + 重要性）切带宽 `BAND_WIDTH` 的带，带内才比热度。
本文件钉的是**纯函数行为**，不碰 DB。
"""
from __future__ import annotations

import pytest

from app.infra.memory.scoring import BAND_WIDTH, band_index, lfu, lru, rank_key
from math import log1p

pytestmark = pytest.mark.contracts


def _primary(semantic: float, importance: float = 0.8) -> float:
    return semantic * 0.6 + importance * 0.2


def test_hot_row_wins_within_the_same_semantic_band():
    """语义打平（同一带）时，常用/最近用过的排前面——这就是「同分裁决」。"""
    cold = rank_key(_primary(0.80), aux=0.0)
    hot = rank_key(_primary(0.80), aux=0.2)
    assert hot > cold, "同带内热度必须起作用"


def test_hot_row_cannot_cross_a_semantic_band():
    """跨带时热度**完全无效**：这是 2026-11-09 那个 84%→36% 的根因修复。

    语义差 0.10 → primary 差 0.06 > BAND_WIDTH(0.05)，必须跨带；
    即使热的行拿满 aux=0.2 也不能反超。
    """
    cold_but_relevant = rank_key(_primary(0.80), aux=0.0)
    hot_but_less_relevant = rank_key(_primary(0.70), aux=0.2)
    assert band_index(_primary(0.80)) > band_index(_primary(0.70))
    assert cold_but_relevant > hot_but_less_relevant, (
        "语义差超过一个带时，热度不得改变排序"
    )


def test_band_width_is_documented_as_similarity_gap():
    """带宽度换算成相似度差要能被解释：0.05 / 0.6 ≈ 0.083（约 8 个百分点）。"""
    assert BAND_WIDTH / 0.6 == pytest.approx(0.0833, abs=5e-4)


def test_aux_is_clamped_and_orders_by_components():
    """LFU/LRU 都在 [0,1]，aux 上界 0.2；访问越多、越近，aux 越大。"""
    assert lfu(0) == 0.0
    assert lfu(10_000) == pytest.approx(log1p(10_000) / 10, abs=1e-9)
    assert lfu(10**6) == pytest.approx(1.0)  # log1p 过头后钳到 1.0
    assert lru(None) == 0.0
    assert lru(0) == pytest.approx(1.0)
    assert lru(72) == pytest.approx(0.5)  # 72h 半衰
    assert rank_key(0.3, 0.5)[1] <= 0.2, "aux 必须被钳到上界"
