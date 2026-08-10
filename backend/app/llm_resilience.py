from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

# 配置全部可被环境变量覆盖。默认取向贴合 MiniMax 现实：限流 10 req/s 防 429，
# 指数退避 1s→2s→4s + 抖动避免打爆，90s 总超时防卡死。
_RATE_LIMIT = float(os.getenv("LLM_RATE_LIMIT", "10"))
_RATE_BURST = float(os.getenv("LLM_RATE_BURST", "10"))
_MAX_TOTAL_TIME = float(os.getenv("LLM_MAX_TOTAL_TIME", "90"))
_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "5"))
_BASE_BACKOFF = float(os.getenv("LLM_BASE_BACKOFF", "1"))
_MAX_BACKOFF = float(os.getenv("LLM_MAX_BACKOFF", "30"))
_JITTER = float(os.getenv("LLM_JITTER", "1"))


class LLMTimeoutError(Exception):
    """90s 总预算耗尽（限流等待或重试耗时超过预算）。"""


class LLMRateLimitExceeded(Exception):
    """限流等待超预算，无法在预算内获得发送机会。"""


class _TokenBucket:
    """进程级令牌桶：rate 个/秒补充，capacity 突发容量。线程安全。"""

    def __init__(self, rate: float, capacity: float):
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float) -> bool:
        """消费一个令牌；不足则等待补齐，timeout 内拿不到返回 False。

        计算所需等待后**释放锁再 sleep**，避免持锁阻塞其他线程的检查。
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                wait = (1.0 - self._tokens) / self._rate
            if now + wait > deadline:
                return False
            time.sleep(min(wait, 0.1))


# 进程级单例：跨线程共享同一个限流桶。
_rate_limiter = _TokenBucket(_RATE_LIMIT, _RATE_BURST)


def _classify_retryable(exc: Exception) -> bool:
    """瞬时故障才重试；认证/参数语义错误重试必复现，直接失败。"""
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )
    return isinstance(
        exc, (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError)
    )


def invoke_with_retry(
    operation: Callable[[], str],
    *,
    max_total_time: float = _MAX_TOTAL_TIME,
    max_retries: int = _MAX_RETRIES,
) -> str:
    """带限流 + 指数退避重试 + 总超时的同步 LLM 调用。

    - 每次实际调用（含重试）前消费一个令牌。
    - 总预算 `max_total_time` 含限流等待与重试退避，耗尽抛 `LLMTimeoutError`。
    - 重试次数耗尽时重抛最后一个原始异常，让调用方看到真实错误（如 429）。
    """
    deadline = time.monotonic() + max_total_time
    attempt = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LLMTimeoutError(
                f"llm call exceeded {max_total_time}s total budget after {attempt} attempts"
            )
        if not _rate_limiter.acquire(timeout=remaining):
            raise LLMRateLimitExceeded(
                "llm rate-limit wait exceeded total budget; no token within remaining time"
            )
        try:
            return operation()
        except Exception as exc:
            if not _classify_retryable(exc):
                raise
            attempt += 1
            if attempt > max_retries:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMTimeoutError(
                    f"llm call exceeded {max_total_time}s total budget after {attempt} attempts"
                )
            backoff = min(
                _BASE_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, _JITTER),
                _MAX_BACKOFF,
            )
            sleep = min(backoff, remaining)
            if sleep <= 0:
                raise LLMTimeoutError(
                    f"llm call exceeded {max_total_time}s total budget after {attempt} attempts"
                )
            logger.warning(
                "llm call retryable error (attempt=%s); backing off %.2fs: %s",
                attempt, sleep, exc,
            )
            time.sleep(sleep)