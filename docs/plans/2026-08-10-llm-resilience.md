# 2026-08-10 LLM 韧性：限流 + 指数退避重试 + 90s 总超时

> 状态: 已完成（commit `b0f2a1e`）

## Context

用户转投的 GitHub issue「[P0] LLM API 限流+重试机制」指出：MiniMax 高并发时常返回 429，SQL 生成失败率 >5%，用户需手动重试。

现状（查证属实）：所有 LLM 调用都走 `backend/app/llm.py` 的同步 `call_llm`（`llm.invoke(prompt)`），调用点 7 处（`parent_graph` / `report_graph` / `requirement_parser` / `sql_graph`×3 / `context`）。`get_chat_llm` 未设 `max_retries`，langchain 默认内建重试 2 次，但无限流、无指数退避抖动、无 90s 总超时——429 仍是裸失败。

## Design

新建 `backend/app/llm_resilience.py`，提供进程级、线程安全的 LLM 调用韧性层；`llm.py` 的 `call_llm` 只把 `llm.invoke(prompt)` 这一行改成走该层，其余（trace 记录、返回 text）不动。

### 1. 令牌桶限流（10 req/s）

- `_TokenBucket`：`rate`（`LLM_RATE_LIMIT` 默认 10/s）、`capacity`（`LLM_RATE_BURST` 默认 10）。进程级单例 `_rate_limiter`，`threading.Lock` 保护——`call_llm` 是同步且在事件循环/执行器线程都可能被调，限流必须跨线程一致。
- `acquire(timeout) -> bool`：容量不足时计算补齐所需等待，**释放锁再 `time.sleep(min(wait, 0.1))`**，避免持锁睡眠阻塞其他线程；超时返回 `False`。
- 每次实际 API 调用（含重试）前消费一个 token——重试是真实请求，同样受限流约束。

### 2. 指数退避重试 + Jitter

- `_classify_retryable(exc) -> bool`：可重试 = `RateLimitError`(429) / `InternalServerError`(5xx) / `APIConnectionError` / `APITimeoutError`；不可重试（`AuthenticationError` / `BadRequestError` 等）立即重抛。
- 退避 `min(BASE_BACKOFF * 2^(attempt-1) + uniform(0, JITTER), MAX_BACKOFF)`，`BASE_BACKOFF` 默认 1s、`MAX_BACKOFF` 默认 30s、`JITTER` 默认 1.0——1s→2s→4s 递增 + 抖动，避免多请求同时重试打爆 API。
- `LLM_MAX_RETRIES` 默认 5。

### 3. 90s 总超时

- `invoke_with_retry(operation, *, max_total_time=90.0, max_retries=5)`：`deadline = start + max_total_time`。
- **预算含限流等待**：每轮先算 `remaining = deadline - now`，`<=0` 或 `acquire(timeout=remaining)` 失败 → `raise LLMTimeoutError`（避免「限流等 30s + 调用 60s」超预算）。
- 重试前再算 `remaining`，`sleep = min(backoff, remaining)`；预算耗尽 → `raise LLMTimeoutError`；重试次数超 `max_retries` → 重抛**最后一个原始异常**（让调用方看到真实的 `RateLimitError` 等，便于诊断）。
- 自定义异常：`LLMTimeoutError`（预算耗尽）、`LLMRateLimitExceeded`（限流等待超预算）。

### 4. 接线 `call_llm`

- `llm.py` 顶部 `from app.llm_resilience import invoke_with_retry`；`call_llm` 内 `resp = llm.invoke(prompt)` 改为 `resp = invoke_with_retry(lambda: llm.invoke(prompt))`。
- `get_chat_llm` 的 `_LLM_CONFIG` 加 `max_retries=0`：**关闭 langchain 内建重试**，让本层成为唯一重试，避免双重退避叠加。

## Files to change

- `backend/app/llm_resilience.py`（新建）：`_TokenBucket`、`_classify_retryable`、`invoke_with_retry`、`LLMTimeoutError` / `LLMRateLimitExceeded`、进程级 `_rate_limiter`。
- `backend/app/llm.py`：`call_llm` 走 `invoke_with_retry`；`_LLM_CONFIG` 加 `max_retries=0`。
- `docs/plans/2026-08-10-llm-resilience.md`（本文件）。

## Reused existing utilities

- `llm.py` 既有 `call_llm` 的 trace 记录与返回清理：只改 `invoke` 一行，其余原样。
- `openai` 异常层级（与 embedding 侧同源）：`RateLimitError` / `InternalServerError` / `APIConnectionError` / `APITimeoutError` 直接 `isinstance` 判定，不造解析。
- `get_chat_llm` 的 `**kwargs` 透传：`invoke_with_retry` 只包 `llm.invoke(prompt)`，`max_tokens` 等仍由 `get_chat_llm(**kwargs)` 注入。

## Verification

```bash
cd backend && pytest tests/smoke/test_llm_resilience.py -v
```

新增 `backend/tests/smoke/test_llm_resilience.py`（离线，假 operation，不打真实 API）：

1. **限流**：`rate=10` 下并发/连续 11 次 `acquire`，第 11 次需等待（降低 `rate` 到 0.1 断言等待）；`acquire(timeout=0)` 在无 token 时返回 `False`。
2. **可重试分类**：`RateLimitError`/`InternalServerError`/`APIConnectionError` → True；`AuthenticationError`/`BadRequestError` → False。
3. **指数退避重试**：patch `time.sleep`，前 2 次抛 `RateLimitError`、第 3 次成功，断言 `operation` 调 3 次、`sleep` 调 2 次且递增。
4. **不可重试立即抛**：抛 `AuthenticationError`，`operation` 只调 1 次。
5. **预算耗尽**：`max_total_time=0.05` + 每次失败 sleep 被 patch 掉，断言抛 `LLMTimeoutError`。
6. **重试次数耗尽**：一直抛 `RateLimitError`，断言最终抛出的异常是 `RateLimitError`（最后一个原始异常）。
7. **call_llm 接线**：patch `get_chat_llm` 返回假 llm（`invoke` 前 2 次抛 `RateLimitError`、第 3 次返回假 resp），断言 `call_llm` 返回文本、重试生效。

回归：

```bash
cd backend && pytest -q
```

## Explicitly NOT doing

- **不做** async 化 `call_llm`——封锁事件循环是既有架构怪癖，改成 async 是大改（backend-async-refactor 范畴），本 issue 只加韧性层。
- **不做** 流式（`stream`）LLM 调用——当前 7 个调用点全走 `call_llm` 同步 `invoke`，无流式路径。
- **不做** 分布式限流（Redis 等）——单进程令牌桶已覆盖当前部署形态。
- **不改** 7 个调用点本身——韧性收敛在 `call_llm` 一层，调用方无感知。