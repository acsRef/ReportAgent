# P13 Langfuse 真实执行耗时同步

> 状态: 已完成（2026-09-01；接 master `87a3f83` + `p13-langfuse` HEAD `5ca0cf4`）
> 上游: P13 已合 master（979 passed），但 Langfuse UI 显示的 span duration 是 flush 时刻的 native 计时（不是业务真实耗时）；user 真测试（cloud key）反馈"每个节点的真实执行耗时没同步到 Langfuse"

---

## Context

### 现状（P13 落地后）

P13 plan 已合 master，分支 `p13-langfuse` 7 commit：

| commit | fix |
|---|---|
| `f17c4c6` | T1 LangfuseConfig |
| `493645f` | T2 redaction |
| `7eb5595` | T3 flush 适配器 |
| `98de42f` | T4 双 sink |
| `0b6d5f6` | UUID trace_id 转 32-hex |
| `6ab131c` | 6 个 prompt builder 接线 record_prompt_version |
| `ad6a020` | docs 收尾 |

user 真实 cloud key 实测后追加 2 commit：

| commit | fix |
|---|---|
| `f788b23` | LLMCall 加 input/output + adapter 接入 redact + flush 透传（Langfuse generation 不再空白） |
| `86f1ffc` | 4 个 P1 修复——LLM 按 span_id 嵌套到父 span + latency_ms 入 metadata + decision 聚合 redact + decision 兜底挂 root |
| `5ca0cf4` | test(adapter): LLMAdapter.generate → Tracer.add_llm_call 链路锁 |

979 passed / 1 skipped。

### 缺口

`langfuse_flush.py:124-128` 当前用 `start_as_current_observation` context manager 创建 span observation：

```python
with langfuse.start_as_current_observation(
    name=span.span_name,
    input=redact(span.input) if getattr(span, "input", None) else None,
    metadata=span_md or None,  # span_md 只有 decisions / prompt_version
):
    ...
```

后果：Langfuse UI 看到的 observation duration 是 **flush 阶段的 native 计时**（observation 进出 context manager 的瞬间），不是业务节点的真实执行耗时。Span 真实业务耗时已经在 `Span.duration_ms`（sdk.py:120 计算）但没回放到 Langfuse。

类似问题在 LLM 上不存在：`LLMCall.latency_ms` 已在 LLMCall 入库时由 adapter 测得，flush 阶段落 `metadata.latency_ms`（`f788b23` 落地），是真实业务耗时。

### user 反馈（2026-09-01）

user 真测试 cloud key 后指出：

> p13 这里需要优化一下——你应该把"每个节点的真实执行耗时"也完整同步到 Langfuse。你现在其实已经有这份数据，所以不用重新给每个 Agent 测一次，只需要把本地 Span 的时间信息正确回放到 Langfuse。

user 明确方案：

1. **不改 Tracer 计时逻辑**——已有数据准确
2. **不硬塞 `start_time=` / `end_time=` 参数**——Langfuse v4 SDK 的 `start_observation` 不暴露这俩参数（实测确认），只有 `completion_start_time` 给 TTFT 用
3. **metadata 是唯一可靠通道**：把 `duration_ms` / `start_time.isoformat()` / `end_time.isoformat()` 写到 observation metadata
4. **明确区分两层计时**：
   - `metadata.execution_duration_ms` = 业务真实耗时（可信）
   - Langfuse native duration = observation 进出 flush 上下文的时间（不可信，仅参考）
5. **LLM 不变**：`metadata.latency_ms` 已经是真业务耗时

### 验收目标

- Langfuse UI 每条 span observation 的 metadata 含 `execution_duration_ms`（真实业务毫秒）+ `original_start_time` + `original_end_time`（ISO 格式，与 PG `spans.start_time`/`end_time` 一一对账）
- root observation metadata 加 `total_duration_ms`（整个 trace 的业务总耗时）
- LLM observation 仍 `latency_ms`（行为不变）
- 全量 backend 跑过，零回归

---

## Design

### 1. metadata 字段约定

| 字段 | 来源 | 落到哪 | 含义 |
|---|---|---|---|
| `execution_duration_ms` | `Span.duration_ms` | 每个 span observation | 节点真实业务耗时（毫秒） |
| `original_start_time` | `Span.start_time.isoformat()` | 每个 span observation | 节点真实开始时刻（ISO 8601，与 PG `spans.start_time` 对账） |
| `original_end_time` | `Span.end_time.isoformat()` | 每个 span observation | 节点真实结束时刻 |
| `total_duration_ms` | `Tracer._trace.total_duration_ms` | root observation | 整个 trace 的真实业务总耗时 |
| `latency_ms` | `LLMCall.latency_ms` | 每个 llm_call observation | LLM 真实业务耗时（已有，P13 落地） |

**为什么不试图覆盖 Langfuse native duration**：Langfuse v4 Python SDK 的 `start_observation` / `start_as_current_observation` 没有 `start_time` / `end_time` 入参（实测 `inspect.signature`），即使有，OTel 内核的事件时间戳是 SDK 内部管理的，回填会引发 trace_id 与 timeline 不一致。"业务真实耗时作为一等公民的独立 metadata"是更诚实的方案。

### 2. 实现策略

**核心原则**：保持现有 `start_as_current_observation` context manager 结构不动（OTel context 嵌套依赖此机制），仅在 metadata 拼装时加入真实耗时字段。

`backend/app/observability/langfuse_flush.py` 改动范围：

```python
# 旧 span observation metadata：
span_md: dict[str, Any] = {}
# decisions / prompt_version 合并（既有逻辑）

# 新增 timing 三字段，过滤 None 避免污染 metadata：
if getattr(span, "duration_ms", None) is not None:
    span_md["execution_duration_ms"] = span.duration_ms
if getattr(span, "start_time", None) is not None:
    span_md["original_start_time"] = span.start_time.isoformat()
if getattr(span, "end_time", None) is not None:
    span_md["original_end_time"] = span.end_time.isoformat()

with langfuse.start_as_current_observation(
    name=span.span_name,
    input=...,
    metadata=span_md or None,
):
    ...
```

同理 root observation：

```python
root_md: dict[str, Any] = {"pg_trace_id": tracer.trace_id}
if getattr(tracer._trace, "total_duration_ms", None) is not None:
    root_md["total_duration_ms"] = tracer._trace.total_duration_ms
if getattr(tracer._trace, "start_time", None) is not None:
    root_md["original_start_time"] = tracer._trace.start_time.isoformat()
if getattr(tracer._trace, "end_time", None) is not None:
    root_md["original_end_time"] = tracer._trace.end_time.isoformat()
```

**LLM 不动**：`latency_ms` 已经在一等 metadata 里（`f788b23` 落地）。adapter 已测得真实 LLM 耗时。

### 3. 字段命名 vs user 提案

user 方案里字段命名有两套：

- 第一次出现：`duration_ms` / `original_start_time` / `original_end_time`
- 最后建议：`execution_duration_ms`（与 LLM 的 `latency_ms` 概念区分）

采用 user 最终建议 `execution_duration_ms`（语义更明确：这是节点整体业务耗时，不是 LLM 单独往返耗时）。

### 4. Span 数据可用性

确认 `tracer.span()` context manager（`backend/app/infra/trace/sdk.py:103-129`）必填 `start_time` / `end_time` / `duration_ms`：

```python
@contextmanager
def span(self, name: str, ...) -> Generator[Span, None, None]:
    span = Span(..., start_time=datetime.now(), ...)
    try:
        yield span
        span.status = "SUCCESS"
        span.end_time = datetime.now()
        span.duration_ms = int((span.end_time - span.start_time).total_seconds() * 1000)
    except Exception as e:
        span.status = "FAILED"
        span.end_time = datetime.now()
        span.duration_ms = int((span.end_time - span.start_time).total_seconds() * 1000)
        span.error = str(e)
        raise
    finally:
        self._stack.pop()
        self._spans.append(span)
```

正常 + 异常路径都填了三个字段。但兜底：`_summarize_state` 失败的极端情况下可能缺字段——`None` 过滤防 metadata 污染。

---

## Files to change

| 模块 | 模式 | 路径 |
|---|---|---|
| flush metadata | 修改 | `backend/app/observability/langfuse_flush.py`（`span_md` 拼装加 timing 三字段 + root `extra_md` 加 `total_duration_ms`） |
| 测试 | 修改 | `backend/tests/contracts/test_langfuse_flush.py`（新增 4 个 case） |
| 测试 fixture | 修改 | `backend/tests/contracts/test_langfuse_flush.py` `_make_tracer` 增强：Span 填 `start_time`/`end_time`/`duration_ms`（测试真值），Trace 填 `total_duration_ms`/`start_time`/`end_time` |
| plan 收尾 | 修改 | `docs/plans/2026-09-01-p13-langfuse-execution-timing.md`（状态 → 已完成 + 落地记录） |
| 索引 | 修改 | `docs/plans/README.md`（登记到「进行中」表，落地后移「已完成」） |

---

## Reused existing utilities

- `Span.start_time` / `end_time` / `duration_ms`（`backend/app/infra/trace/models.py:14-17`）—— P3 已落，sdk.py:112,119-124 context manager 真实记录
- `Trace.start_time` / `end_time` / `total_duration_ms`（`backend/app/infra/trace/models.py:45-47`）—— `Tracer.end()` 在 sdk.py:97-101 计算 `total_duration_ms`
- `LLMCall.latency_ms`（`backend/app/infra/trace/models.py:29`）—— adapter 在 LLM 调用前后 `time.monotonic()` 测得，已落 `metadata.latency_ms`
- `langfuse.start_as_current_observation` context manager 嵌套结构（既有 P13 落地）—— OTel context 自动父子嵌套，不动
- `app.observability.redaction.redact` —— metadata 拼装不涉及 PII（数字 + ISO 字符串），无需 redact

---

## Verification

### 单测（先红再绿）

新增 4 个 case，全部以 `MagicMock` 替换 Langfuse client 验证 metadata 拼装：

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/contracts/test_langfuse_flush.py -v
# 预期：现有 11 例 + 新 4 例 = 15 例全绿
```

case 列表：

1. `test_flush_writes_execution_duration_ms_to_span_metadata` —— Span observation metadata 必须含 `execution_duration_ms` / `original_start_time` / `original_end_time`，值与 Span 字段一致
2. `test_flush_writes_total_duration_ms_to_root_metadata` —— root observation metadata 含 `total_duration_ms` + `original_start_time` + `original_end_time`
3. `test_flush_skips_timing_fields_when_span_unset` —— Span `start_time` / `end_time` / `duration_ms` 缺一时，对应字段不入 metadata（不传 None 污染）
4. `test_flush_preserves_llm_call_latency_ms` —— LLMCall `latency_ms` 仍落 metadata（防本次改动回归 P13 已落地的 LLM 路径）

### 全量 backend 回归

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest -q
# 预期：979 + 4 = 983 passed / 1 skipped / 5 warnings（零回归）
```

### 真 Langfuse 自验（user 自验，可选）

```bash
# user 已设 cloud key，跑一次 chat
curl -X POST http://localhost:8100/api/v1/chat \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"user_query":"2024 年各区域销售额","session_id":"timing-test","mode":"new"}'
# 预期：Langfuse UI 看到 trace，每条 span observation metadata 含：
#   execution_duration_ms = <业务真实毫秒>
#   original_start_time = <ISO 时间>
#   original_end_time = <ISO 时间>
# root metadata 含 total_duration_ms
# LLM observation metadata.latency_ms 不变
```

冒烟矩阵：
- [ ] Span observation metadata 含三个 timing 字段且值与 PG `spans.duration_ms` / `start_time` / `end_time` 一致
- [ ] root observation metadata 含 `total_duration_ms`
- [ ] LLM observation metadata.latency_ms 仍正常（行为不变）
- [ ] Span 字段缺一时 metadata 不污染（无 None 字段）
- [ ] Langfuse native duration 仍是 flush 上下文时间（业务上不依赖，但确认没有破坏既有 OTel 嵌套）
- [ ] PII redaction 路径不破（既有用例覆盖）
- [ ] 全量 backend 983 passed / 1 skipped

---

## Explicitly NOT doing

- **不改 Tracer 计时逻辑** —— `tracer.span()` context manager 已准确记录，user 明令禁止重新计时
- **不改 `start_as_current_observation` context manager 结构** —— OTel 嵌套依赖 context；改成手动 `start_observation` + `obs.end()` 会破坏父子嵌套且无收益（native duration 仍是 flush 时间，user 已点明"obs.end() SDK 时间仍然是 flush 时间"）
- **不硬塞 `start_time=` / `end_time=` 参数** —— Langfuse v4 SDK 不暴露此参数（实测确认），硬塞会被 SDK 拒收
- **不新增 env 配置** —— 沿用 P13 既有 `LANGFUSE_*` 配置即可
- **不动 PII redaction** —— timing 字段全是数字 + ISO 字符串，无 PII，无需 redact
- **不动 LLMCall `latency_ms` 字段** —— 既有 P13 路径已正确处理
- **不动 `metadata.execution_duration_ms` 之外的 timing 字段名** —— user 已拍板用此名（与 LLM `latency_ms` 区分）
- **不写 Golden Case** —— Langfuse 评测留 P14
- **不动 Plan README「进行中」外的现有 P13 plan** —— 本次补完是新 topic（execution timing），独立 plan 文件
- **不动 PG sink** —— `infra/trace/repository.py` / `Span` / `LLMCall` dataclass 不动；Langfuse 只是 metadata 加字段，PG 端零改动

---

## 落地记录（2026-09-01）

接 `p13-langfuse` 分支 HEAD `5ca0cf4`，2 commit：

| Task | commit | 内容 |
|---|---|---|
| fix | `1a14946` | `flush_to_langfuse` 给 span observation 加 `execution_duration_ms` / `original_start_time` / `original_end_time`；root observation 加 `total_duration_ms` / `original_start_time` / `original_end_time`；LLM `latency_ms` 不动（语义分层：Agent 节点总耗时 vs LLM 单独往返耗时）。Langfuse v4 SDK 不暴露 `start_time`/`end_time` 参数（实测 `inspect.signature` 确认），业务真实耗时作为一等 metadata 字段回放；不破坏 `start_as_current_observation` context manager 的 OTel context 嵌套；`_timing_fields()` helper 统一处理 None 字段过滤防 metadata 污染 |
| docs | (本 commit) | plan 状态 → 已完成 + 落地记录 + README 移已完成表 |

### 验证

- observability contract：**29/29 passed**（test_langfuse_flush 16 + test_tracer_double_sink 5 + test_langfuse_config 3 + test_redaction 5）
- 后端全量 **990 passed / 1 skipped / 5 warnings**（979 baseline + 4 新例 + 7 P13 review 后扩展；零回归）
- 新增 4 例：
  - `test_flush_writes_execution_duration_ms_to_span_metadata` — span observation metadata 三字段值与 Span 字段一致
  - `test_flush_writes_total_duration_ms_to_root_metadata` — root observation metadata 三字段 + pg_trace_id 保留
  - `test_flush_skips_timing_fields_when_span_unset` — None 字段过滤不入 metadata（防污染）
  - `test_flush_preserves_llm_call_latency_ms` — LLM `latency_ms` 仍落 metadata（防回归 P13 既有路径）

### 设计落地偏差（vs plan 原文）

1. **fixture 增强副作用**：增强 `_make_tracer` 让 Span / Trace 填 timing 字段后，既有 `test_flush_to_langfuse_transform_uuid_trace_id` 用 `metadata == {"pg_trace_id": ...}` 严格等值断言 fail。修：放宽为子集断言 `metadata["pg_trace_id"] == ...`（新增字段不属于该 case 验证范围）。这是 fixture 增强的预期副作用而非实现 bug。
2. **未采用 user 提案的 `obs.update(metadata=...)` 后置写法**：实测 Langfuse v4 `start_as_current_observation` 暴露 `end_on_exit` 参数可关闭自动 end，理论上可走「context manager 内手动 update + 手动 end」路径。但仍依赖 context manager 提供 OTel 嵌套——纯 `start_observation`（无 `_as_current`）需手动维护 trace_id / parent_observation_id 关系，破坏既有父子结构。最终采用「metadata 参数直接传」方案（创建 observation 时一并写入 timing），与 user 方案语义等价（都把 timing 作为一等 metadata 字段），但保留现有 OTel context 嵌套不动。
3. **Langfuse native duration 仍不可信**：plan §Design 1 已明确，但实测后再次确认——SDK 进出 flush 上下文的时间窗含 OTel span 创建 + HTTP 请求序列化 + 本地聚合，与业务真实耗时无对应关系。Langfuse UI 上 metadata 的 `execution_duration_ms` 才是真值。