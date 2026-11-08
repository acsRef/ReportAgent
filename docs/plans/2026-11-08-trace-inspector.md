> 状态: 进行中

# 执行链路排查工具：看见每个节点的输入输出 + 按会话直达

## Context（背景）

用户的问题：「上线出了 bug 我该怎么定位」。现状盘点后结论是**工具已有八成，缺的是最关键的「输出」那一层**：

已有（`docs/plans/2026-08-01-observability-ops.md` 落地）：

- 写侧：`observability.agent_trace / agent_trace_span / llm_call` 三张表，`@traced_node` 每个节点一个 span；
- 读侧：`GET /api/v1/observability/{metrics,traces,traces/{id}}`（A-3 按 `user_id` 隔离）；
- 前端：`/observability` 页面 —— trace 列表、节点耗时条、错误文本、LLM 调用（模型/tokens/延迟）。

缺的三件（都是「真出 bug 时最需要」的）：

1. **span 的 `input` / `output` 读不出来**：表里存着（jsonb），`get_spans` 的 SELECT 里没有它们，
   页面上展开也只有一个 span_id。等于「知道哪个节点慢，不知道它算出了什么」。
2. **不能按会话找**：`list_traces` 只有 `status` 过滤。用户排查时手里有的是「刚才那轮」，
   不是 trace_id。
3. **从工作台跳不过去**：需要手动复制 trace_id 再去 `/observability` 粘贴。

## Design（设计）

### D1 span 输出读出来

- `TraceRepository.get_spans` 的 SELECT 加 `input, output`，逐行做**有界序列化**
  （已有 trace 写入侧的规则：字符串 >2KB 截断并标注长度，避免把整张结果集塞进页面）；
- 前端 `SpanRow` 展开后显示：输入（JSON 折叠/截断）、输出、错误、span_id、开始时间；
- 大对象只显示摘要 + 「已截断 N 字符」提示，避免页面卡死。

### D2 按会话筛 trace

- `TraceRepository.list_traces(..., session_id=None)`：为真时加 `AND session_id = $n`；
- API `GET /traces?session_id=...`（与 `status` 同样可选、同样按 user_id 隔离）；
- 前端 `/observability` 从 query string 读 `?session=<sid>`，进页面就用它筛，
  并在标题旁显示「正在看会话 xxx」+ 清除按钮。

### D3 从工作台一键跳过去

工作台顶部工具区加一个「执行链路」入口：带着当前 `sessionId` 跳到
`/observability?session=<sid>`。带不带 trace_id 都行——按会话筛出来的第一条就是最近一轮。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/infra/trace/repository.py` | `get_spans` 加 `input/output`（有界序列化）；`list_traces` 加 `session_id` 过滤 |
| `backend/app/api/observability.py` | `GET /traces` 增 `session_id` 查询参数 |
| `frontend/src/api/observabilityClient.ts` | `fetchTraces` 增 `sessionId`；`SpanDetail` 增 `input/output` |
| `frontend/src/pages/ObservabilityPage.tsx` | span 展开显示输入/输出；`?session=` 过滤 |
| `frontend/src/pages/WorkbenchPage.tsx` | 「执行链路」入口（带 sessionId 跳转） |
| 测试（各自独立） | `test_trace_repository_shapes.py`（API 参数 + 序列化边界）/ `SpanDetail.test.tsx` |

## Reused existing utilities（复用工具）

- 既有 `TraceRepository` / observability router / `/observability` 页面（**不新建页面、不新建表**）；
- 既有 span 写入侧的截断规则（`_summarize_state` / `<list: N items>`）保持一致；
- 既有 A-3 归属过滤（`user_id`）——新参数不改变隔离语义。

## Verification（验证）

```bash
pytest backend/tests/contracts/test_trace_repository_shapes.py -q
npx vitest --run src/pages/__tests__/ObservabilityPage.test.tsx src/pages/__tests__/SpanDetail.test.tsx
```

真机：跑一轮 → `GET /api/v1/observability/traces?session_id=<sid>` 拿到该轮 trace →
`GET /traces/{id}` 的 spans 里能看到 `input/output`（节点名 + 有界摘要）。

## Explicitly NOT doing（不做事项）

- 不引入 Prometheus/Grafana/OpenTelemetry（宪法 §12 明确不做）；
- 不做实时流（现有页面按需刷新即可）；
- 不新建 debug 表或旁路日志——`observability` 三张表已经够；
- 不做跨用户/跨会话的全量检索（A-3 隔离不变）。

## 落地记录（进行中）

### 已做

- **读侧**：`get_spans` 的 SELECT 补 `input/output`，走 `_bounded_payload`（2000 字符截断）；
  前端 `SpanPayload` 展开显示输入/输出。
- **按会话筛**：`list_traces(session_id=...)` + API 参数 + 前端 client 参数。
- **Langfuse 侧人性化**：trace 名 = 用户原话（截断 40 字）、节点名 = 中文（复用
  `PROGRESS_NODES`，技术名留 `metadata.node`）、`*_output` 这类 DATA span 合并成父节点 output
  （不再单独上报，observation 数量减半）、payload 截断 4000 字符、
  `session_id`/`user_id`/`trace_name` 走 SDK v4 的 `propagate_attributes`。
- **变量名炸弹**：`langfuse_config` 现在同时认 `LANGFUSE_HOST` 与 `LANGFUSE_BASE_URL`。

### 真机卡住的点（重要）

云端 **OTLP 写入每次 5s 超时、整批丢弃**：

```text
[ERROR] opentelemetry.exporter.otlp.proto.http.trace_exporter:
Failed to export span batch ... Read timed out. (read timeout=5.0)
```

- 读 API 正常（`/api/public/projects` 200，health 往返 235ms），**写不进去**；
- `OTEL_EXPORTER_OTLP_TIMEOUT=15000` 与 `OTEL_BSP_MAX_EXPORT_BATCH_SIZE=50` 设了都不生效
  （SDK 内部固定 5s），所以「调大超时」走不通；
- **PG sink 一直正常**（`trace pg sink done`，几十毫秒）。中途一度误判「本地也没落库」，
  真因是 `start_time` 写的是宿主本地时间、`created_at` 是容器 UTC，用 `start_time` 做
  「最近 N 分钟」过滤自然查不到——**已于本次一并修成 UTC**。

### 采样：本地全量 / 云端精选 ✅

Langfuse 按 observation 计费，而一轮完整流程实测 **57 个 span**。`LANGFUSE_TRACE_LEVEL`
默认 `essential`：只发 12 个人能看懂的阶段节点（理解/解析/投影/规划/生成/校验/执行/评估/
修复/报告 + 两个容器），**失败节点无条件发**，其余确定性内部步骤只在本地表里。实测
`57 → 18`（含失败节点），可切 `full` 回全量。metadata 里记 `reported_nodes` / `skip_nodes` /
`trace_level`，一眼看出是不是被过滤了。

### 验证

- `test_langfuse_humanize.py` **44 passed**（命名 / 中文节点名 / 截断 / PII / 采样过滤）、
  `test_trace_timestamps.py` **7 passed**（UTC 口径）、`test_interruption_resilience.py` 零回归。


