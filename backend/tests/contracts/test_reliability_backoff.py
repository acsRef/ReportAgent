"""P9 reliability/backoff.py 契约：compute_backoff 纯函数（自 llm_resilience 抽出）。

行为逐值不变：min(base * 2**(attempt-1) + uniform(0, jitter), cap)。
"""
from __future__ import annotations

from app.reliability.backoff import compute_backoff


def test_backoff_first_attempt_equals_base():
    assert compute_backoff(1, base=1.0, cap=30.0, jitter=0.0) == 1.0


def test_backoff_grows_exponentially():
    assert compute_backoff(1, base=1.0, cap=100.0, jitter=0.0) == 1.0
    assert compute_backoff(2, base=1.0, cap=100.0, jitter=0.0) == 2.0
    assert compute_backoff(3, base=1.0, cap=100.0, jitter=0.0) == 4.0


def test_backoff_capped():
    assert compute_backoff(10, base=1.0, cap=30.0, jitter=0.0) == 30.0


def test_backoff_jitter_upper_bound():
    for _ in range(50):
        v = compute_backoff(2, base=1.0, cap=100.0, jitter=1.0)
        assert 2.0 <= v <= 3.0
