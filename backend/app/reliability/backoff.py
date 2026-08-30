"""退避工具（P9，自 llm_resilience.invoke_with_retry 内联公式抽出为纯函数）。

公式逐值不变：min(base * 2**(attempt-1) + uniform(0, jitter), cap)。
"""
from __future__ import annotations

import random


def compute_backoff(attempt: int, *, base: float, cap: float, jitter: float) -> float:
    """第 attempt 次重试（从 1 计）前的等待秒数：指数增长 + 抖动，封顶 cap。"""
    return min(base * (2 ** (attempt - 1)) + random.uniform(0, jitter), cap)
