# 2026-08-10 Embedding 韧性：超时 + 重试 + 缓存 + trace 埋点

> 状态: 已完成（commit `249ed50`）

## Context

用户转投的 GitHub issue「[P1] Embedding API 超时和重试优化」指出三个现实问题：

1. `backend/app/embedding/service.py` 的 OpenAI 客户端 `timeout=15.0` 太短——SiliconFlow 的 OpenAI 兼容接口 P95 可达 20s，超时后直接抛错。
2. `max_retries=1` 无错误分类：认证失败（401/403）与网络抖动（connection/timeout/5xx/429）被同等对待，前者重试毫无意义、反而拖慢失败路径。
3. 无缓存：`query_memory.save_query` / `search_similar`、`user_memory.save` / `search` 会对同一段文本反复 embed；无缓存每次都打真实 API。
4. 无 trace 埋点：embedding 调用时长、命中缓存与否在 observability 里看不到。

现状（查证属实）：`embed_or_none` 失败降级关键字搜索（AGENTS.md Known Quirks）、启动降级非阻断，但单次调用既慢又无重试。

## Design

在 `backend/app/embedding/service.py` 这个单文件内完成全部改动，不新增模块，不触碰调用方。

### 1. 超时延长（可配置）

客户端 timeout 从硬编码 `15.0` 改为 `EMBEDDING_TIMEOUT` 环境变量（默认 `30.0`），监听 OpenAI S2 协议 P95 20s 的现实。

### 2. 错误分类重试

新增私有 `_classify_retryable(exc) -> bool`：

- **可重试**：`APIConnectionError`、`APITimeoutError`、`InternalServerError`（5xx）、`RateLimitError`（429）。
- **直接失败（不重试）**：`AuthenticationError`（401）、`PermissionDeniedError`（403）、`BadRequestError`（400）、`NotFoundError`（404）、`UnprocessableEntityError`（422）。

> 分类依据：认证/参数类错误重试必复现，纯浪费；连接/限流/服务端错误是瞬时抖动，值得退避重试。

新增私有 `_call_with_retry(operation)`：

- 最多重试 `EMBEDDING_RETRIES` 次（默认 3，即首次 + 2 次重试）。
- 指数退避 `1s → 2s → 4s` + 抖动（`random.uniform(0, backoff*0.1)`），避免多请求同时重试打爆 API。
- 只对 `_classify_retryable` 为 True 的异常重试；不可重试异常立即 `raise`。
- 重试耗尽后抛最后一个异常，由调用方 `embed_or_none` 捕获降级关键字。

`embed` / `embed_batch` 都走 `_call_with_retry`。

### 3. 结果缓存

实例级 LRU 缓存（`OrderedDict`，`EMBEDDING_CACHE_SIZE` 默认 1024），键为**原文 text**，值为 `list[float]`。

- 只缓存成功结果，不缓存 `None`（失败降级路径每次重试，避免把失败也固化）。
- `embed(text)` 命中缓存直接返回，不再打 API；`embed_batch` 逐条查缓存，未命中的批量请求。
- 缓存是纯内存、进程内、无失效时间——文本→向量是确定性映射，冷启动后稳定复现，无需 TTL。

### 4. trace span 埋点

`embed` 内若 `current_tracer()` 存在，用 `with tracer.span("embedding", span_type="TOOL", input=…)` 包裹真实 API 调用：

- span 记录 `duration_ms`（命中缓存的调用不记 span，避免噪声）。
- 失败时 span.status 由 CM 置 `FAILED` + `error`。
- 复用 `app.infra.trace.sdk.Tracer.span` 与 `current_tracer`——不新增 trace 类型、不改 flush。

## Files to change

- `backend/app/embedding/service.py`（唯一改动文件）：超时配置、`_classify_retryable`、`_call_with_retry`、LRU 缓存、trace span。
- `backend/app/infra/trace/sdk.py`：**不改**（复用 `current_tracer` / `span`）。
- `docs/plans/2026-08-10-embedding-resilience.md`（本文件）。

## Reused existing utilities

- `app.infra.trace.sdk.current_tracer` / `Tracer.span`：现有 trace 埋点，不新建。
- `app.embedding.service.get_embedder` 单例：缓存挂在单例实例上，天然跨调用共享。
- `openai` 客户端自带异常层级（`APIConnectionError` / `AuthenticationError` / `RateLimitError` / `InternalServerError` 等）：直接用，不造轮子解析错误串。

## Verification

```bash
cd backend && pytest tests/smoke/test_embedding_resilience.py -v
```

新增 `backend/tests/smoke/test_embedding_resilience.py`（纯离线，用 `unittest.mock` 假 client，不打真实 API）：

1. 超时配置：`EMBEDDING_TIMEOUT` 默认 30.0，env 覆盖生效。
2. 错误分类：`AuthenticationError` → 不重试（`create` 只调 1 次）；`APIConnectionError` → 重试（`create` 调次 > 1）。
3. 退避重试：连续 `APIConnectionError` 抛 `RateLimitError` 后重试耗尽，`embed_or_none` 返回 `None`（降级）。
4. 缓存：同文本 `embed` 两次，第二次 `create` 不再被调；失败结果不入缓存（`embed_or_none` 失败后再次调用仍会打 API）。
5. trace span：有 `current_tracer` 时 `embed` 产生 span；无 tracer 不报错。

回归（不降级不回归）：

```bash
cd backend && pytest -q
```

## Explicitly NOT doing

- **不做** embedding 结果持久化缓存（DB 表）——进程内 LRU 已覆盖热点，跨进程共享收益低、复杂度高。
- **不做** 批量请求合批（把多个 `embed` 合并成一个 `embed_batch`）——调用方是串行语义，合批会引入并发编排，超出本 issue 范围。
- **不改** `embed_or_none` 的降级契约（失败 → `None` → 关键字搜索）——上游行为保持不变。
- **不改** `query_memory.py` / `user_memory.py` 两个调用方——本 issue 收敛在 service.py 单文件内。