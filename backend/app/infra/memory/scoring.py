"""记忆召回排序：语义优先，热度（LFU/LRU）只做**同分裁决**。

2026-11-09（plan memory-recall-scoreboard 追加）：gold scoreboard 的 `--warm others`
量出一个真问题——把竞争者预热到 8 次访问后，`Recall@1` 从 84% 掉到 36%、`Recall@3`
从 96% 掉到 56%（10/25 个 case 被挤掉）。

根因是权重结构：召回分本是

    0.6×语义 + 0.2×重要性 + 0.1×LFU + 0.1×LRU

辅助项合计 0.12，而语义每 0.1 相似度只值 0.06——**0.12/0.6 ≈ 0.33**，意思是
「只要竞争者语义不比金标低 0.33 以上，热度就能让它反超」。这不是调权重能根治的
（调到 0.1 仍能逆转 0.17），因为问题不在比例，在于**辅助项被允许跨语义带比较**。

修法（方案 B）：把 primary（语义 + 重要性）切成宽度 `BAND_WIDTH` 的**带**，
带内才比 aux（LFU+LRU）。跨带的候选永远排在后面，无论它多热。
带内保留 aux 的意义：语义确实打平时，常用/最近用过的更可能是用户想起来的那个。

`score` 字段的**展示值不变**（前端/模型看到的相关度仍是原来的加权和）；
变化只发生在排序键上——这正是「辅助信号应该只影响平局」的字面含义。
"""
from __future__ import annotations

import math

# 带宽度（primary 单位）。primary = 0.6×语义 + 0.2×重要性，所以
# 0.05 / 0.6 ≈ **0.083 相似度**——语义差不超过 8 个百分点视为「打平」。
# 取值理由：真实 embedding 下同一意图的两种说法常见差 0.02~0.06；
# 差 0.083 以上通常是不同意图，此时不该让热度说话。
BAND_WIDTH = 0.05

# aux 上界（LFU 0.1 + LRU 0.1），用于归一化到 0~1，保证带内比较稳定
AUX_MAX = 0.2


def band_index(primary: float) -> int:
    """primary → 带序号。加极小量避免浮点把 0.1/0.05 算成 1.9999999。"""
    return int(math.floor(primary / BAND_WIDTH + 1e-9))


def rank_key(primary: float, aux: float) -> tuple[int, float]:
    """排序键：(语义带, 带内热度)。

    Python 的 tuple 比较天然「先带后热度」；`aux` 归一化后落在 [0, max]，
    不会因为数值范围意外压过带序号（两者在不同量纲上）。
    """
    return band_index(primary), min(max(aux, 0.0), AUX_MAX)


def lfu(access_count: int) -> float:
    """对数压缩的访问频次（0~1），沿用既有 log1p/10 口径。"""
    return min(math.log1p(max(access_count, 0)) / 10.0, 1.0)


def lru(last_access_hours: float | None) -> float:
    """指数衰减的最近性（0~1，72 小时半衰），沿用既有口径。"""
    if last_access_hours is None:
        return 0.0
    return 2.0 ** (-max(last_access_hours, 0.0) / 72.0)
