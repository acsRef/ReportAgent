"""兼容 shim（P9 收编）：实现已整体移至 app.reliability.retry——算法未重写。

仅保 LEGACY 桥接区（app/llm_legacy.py）与历史外部 import 不断；
新代码一律 `from app.reliability.retry import ...`。
"""
from app.reliability.retry import (  # noqa: F401
    LLMRateLimitExceeded,
    LLMTimeoutError,
    _TokenBucket,
    _classify_retryable,
    _rate_limiter,
    invoke_with_retry,
)
