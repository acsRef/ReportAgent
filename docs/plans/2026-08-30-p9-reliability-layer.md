# P9 实施：Reliability Layer——ErrorEnvelope 统一分类 + Retry/Timeout 收编 + Background Task Timeout

> 状态: 已完成（2026-08-30，p9-reliability 分支；T1–T8 全绿 + 1 边界 freeze 修复）
> 上游: [2026-08-25-refactor-master-freeze.md](2026-08-25-refactor-master-freeze.md) §二·二目标结构（reliability/ 顶层四模块）+ §七 Reliability Layer + §391 P9 验收清单 + §450/§464/§466 迁移映射 + CLAUDE.md 宪法 §11 + [[memory:p8-landed]]（P8 衔接：DiagnosePolicy 收编 reliability/errors.py；收编 llm_resilience 语义不重写算法）
> 协作: P8 已合 master（`e4e0c42`，627 passed）；本 plan 仅 P9；完成后同对话接 P10 实施 plan

## 落地记录（2026-08-30）

- 分支 `p9-reliability`，commit 链：plan `8e7f39a` → T1 errors `f3c7a93` → T2 retry 收编 `1f528ae` → T3 预算收敛 `59a82ee` → T4 timeout `15d9150` → T5 背景超时 `77bdc37` → T6 SSE 收编 `c21a81d` → T7 DiagnosePolicy 同源 `feb46f6` → freeze 修复 `692cd9f`
- **回归**：全量 `cd backend && pytest` → **891 passed / 0 failed / 1 skipped**（e2e env-gated）；P8 同口径子集 contracts+smoke+graphs **662 passed ≥ 627 基线，零回退**
- **计划外修复（P2 边界 freeze 打回）**：初版 `reliability/errors.py` 直 import `app.tools.mcp_errors`，被 `test_mcp_boundary_freeze.py`（AST 扫描）2 例拦下——改为鸭子类型读 `exc.code.value` 查值字符串表，P2 钉原样保留不放行
- 落地偏差：T6 顺带收编了 requirement-analysis 路径的 generic `except`（原 `code="INTERNAL"` → `classify_exception`），与 confirmed 路径同源；`code` 字符串 `INTERNAL`→`INTERNAL_ERROR` 变化已确认前端 reducer 无 code 白名单依赖（仅 REQUIREMENT_INCOMPLETE 特判）

## Context

### 伞形 plan §391 P9 验收清单
- [ ] Timeout/Retry 可测试
- [ ] 错误分类完成
- [ ] 不无限 retry
- [ ] Agent 能区分 recoverable/non-recoverable
- [ ] 用户收到稳定错误信息

### 伞形 plan §七 Reliability Layer 要求
- 新增 `backend/app/reliability/`（顶层，不建 runtime/ 聚合层）：`timeout.py / errors.py / retry.py / backoff.py`
- ErrorEnvelope：`{code, kind, recoverable, failed_action, message}`；统一错误码表最小集 10 码：`LLM_TIMEOUT / LLM_UNAVAILABLE / MCP_TIMEOUT / MCP_UNAVAILABLE / MCP_INVALID_RESPONSE / SQL_SYNTAX_ERROR / SQL_EXECUTION_ERROR / SQL_TIMEOUT / REPORT_VALIDATION_ERROR / INTERNAL_ERROR`
- Retry 固定预算：SQL repair 2 / MCP 2 / LLM transient 2
- 统一 Failure Pipeline：`Error → Classify → Record Trace → Determine Recoverability → Retry/Resume/Fail → Persist State → User-visible Error`
- Background Task Timeout：超过 `MAX_TASK_DURATION` → Persist FAILED → ReportVersion(error) → Trace → 前端 error，不允许永远停在 generating

### 迁移映射（伞形 §450/§464/§466）
- `backend/app/llm_resilience.py`（`_TokenBucket` / `invoke_with_retry` / `_classify_retryable` / `LLMTimeoutError`）→ P9 收编为 `reliability/retry.py` 语义来源，**不重写算法**
- `backend/app/infra/execution/registry.py` → P9 Background Task Timeout 挂在其上

### 现状盘点（对照 master `e4e0c42`）
| 散装点 | 现状 | P9 处置 |
|---|---|---|
| LLM 限流/退避/重试 | `app/llm_resilience.py`（令牌桶 + 指数退避 + 90s 总预算），`app/llm/adapter.py` 与 `app/llm_legacy.py` import 它 | 整体搬 `reliability/retry.py`；`llm_resilience.py` 变兼容 shim；adapter 切新 import |
| 退避公式 | `invoke_with_retry` 内联 `min(base*2^(n-1)+jitter, cap)` | 抽出 `backoff.compute_backoff` 纯函数，行为逐值不变 |
| LLM 重试预算 | `LLMConfig.max_retries` 默认 **5**（`llm/config.py:13`） | 收敛为宪法契约值 **2**（env 可覆盖） |
| MCP 失败分类 | `app/tools/mcp_errors.py` `MCPErrorCode`（MCP_TIMEOUT/UNAVAILABLE/INVALID_RESPONSE）+ max_attempts=2（P2 Task 2 钉死） | 不动实现；errors.py 加 envelope 映射 |
| SQL 错误分类 | `app/tools/sql_tools.py:_classify_psycopg2_error` → 6 kind | 不动位置与行为；errors.py 消费其 kind 输出 |
| Agent recoverable 判定 | `sql_graph.py:DiagnosePolicy` 硬编码 kind 白名单 + `kind in (timeout,connection,permission)` fail 分支 | 收编为 errors.py 表驱动，决策输出逐字段不变 |
| SSE 用户错误 | `main.py:_build_sse_error` 本地 `_ERROR_FRIENDLY`/`_ERROR_CODE` + recoverable 内联集合（QUERY_* 用户码） | 三张表收编进 errors.py，SSE payload 契约不变 |
| 背景任务超时 | `registry._run` 无超时；runner 挂死 → session 永远 generating，无 ReportVersion(error) | `MAX_TASK_DURATION` + `run_with_timeout`，超时走 persist_error_run 全链 |
| 泛化异常出口 | `main.py` generic except 硬编码 `code="INTERNAL"` | `classify_exception(exc)`：LLM/MCP 异常出对应码，其余 INTERNAL 兜底 |

### 关键设计事实：两个 recoverable 语义必须显式分离
- **Agent 侧**（DiagnosePolicy，P8 三轮 review 拍板）：`timeout/connection/permission → fail`（repair 无意义）；`syntax/object/other → repair`
- **用户侧**（SSE `recoverable` 字段，前端 `analysisReducer.canRetry` 已钉）：`timeout/connection/object/other → true`（用户缩小范围重试有意义）；`syntax/permission → false`
- 统一成一张表会破坏前端契约或推翻 P8 拍板——P9 用两张显式命名的表，各自单一来源
- SSE 用户码 `QUERY_*`（QUERY_TIMEOUT 等）**不在**伞形 10 码内——它是前端已消费的稳定契约（confirmStream.test 钉 `QUERY_FAILED`），保留不改值，仅换来源

## Design

### D1 新包结构（伞形 §二·二 + §七，冻结形状）

```text
backend/app/reliability/
├── __init__.py     # 空文件（有意）
├── errors.py       # ErrorEnvelope + ErrorCode(10 码) + 两张 recoverable 表 + classify_* 入口 + 用户视图表
├── retry.py        # 收编 llm_resilience 全部算法（移动非重写）+ RetryPolicy 固定预算表
├── backoff.py      # compute_backoff 纯函数（从 invoke_with_retry 抽出）
└── timeout.py      # TimeoutPolicy 分层默认表 + MAX_TASK_DURATION + run_with_timeout
```

### D2 errors.py——分类单一来源

```python
class ErrorCode(str, Enum):
    LLM_TIMEOUT = "LLM_TIMEOUT"; LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    MCP_TIMEOUT = "MCP_TIMEOUT"; MCP_UNAVAILABLE = "MCP_UNAVAILABLE"
    MCP_INVALID_RESPONSE = "MCP_INVALID_RESPONSE"
    SQL_SYNTAX_ERROR = "SQL_SYNTAX_ERROR"; SQL_EXECUTION_ERROR = "SQL_EXECUTION_ERROR"
    SQL_TIMEOUT = "SQL_TIMEOUT"; REPORT_VALIDATION_ERROR = "REPORT_VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"

class ErrorEnvelope(BaseModel):
    code: str; kind: str; recoverable: bool; failed_action: str; message: str = ""

# SQL 6 kind（sql_tools._classify_psycopg2_error 输出域）
SQL_ERROR_KINDS = ("syntax", "object", "timeout", "connection", "permission", "other")
# Agent 侧（DiagnosePolicy 消费，P8 拍板）：syntax/object/other 可 repair
AGENT_RECOVERABLE_KINDS = ("syntax", "object", "other")
# 用户侧（SSE canRetry 消费，现状语义）：timeout/connection/object/other 用户重试有意义
USER_RECOVERABLE_KINDS = ("timeout", "connection", "object", "other")

def agent_recoverable(kind) -> bool          # DiagnosePolicy 用
def user_recoverable(kind) -> bool           # _build_sse_error 用
def kind_to_error_code(kind) -> ErrorCode    # syntax→SQL_SYNTAX_ERROR / timeout→SQL_TIMEOUT / 其余→SQL_EXECUTION_ERROR
def classify_sql_kind(kind, message="") -> ErrorEnvelope
def classify_llm_exception(exc) -> ErrorEnvelope   # APITimeoutError/budget→LLM_TIMEOUT、连接类→LLM_UNAVAILABLE、其余→INTERNAL_ERROR
def classify_mcp_error(exc: MCPBoundaryError) -> ErrorEnvelope  # 映射 P2 MCPErrorCode，不重写边界
def classify_exception(exc) -> ErrorEnvelope # 泛化兜底：openai 四类瞬时/LLMTimeoutError→LLM 家族、MCPBoundaryError→MCP 家族、其余 INTERNAL_ERROR
def user_message(kind) -> str                # 收编 main.py _ERROR_FRIENDLY（6 条中文文案原值）
def user_code(kind) -> str                   # 收编 main.py _ERROR_CODE（QUERY_TIMEOUT 等 6 值原值）
```

两套码的关系在模块 docstring 写明：`ErrorCode`（runtime 内部分类/trace/persist metadata）vs `user_code`（SSE 稳定契约，前端已消费，改动即破坏契约）。

### D3 retry.py + backoff.py——收编不重写

- `reliability/retry.py` 承接 `llm_resilience.py` 全量内容：`_TokenBucket` / `_rate_limiter` 单例 / `_classify_retryable` / `LLMTimeoutError` / `LLMRateLimitExceeded` / `invoke_with_retry`——**逐行移动，签名与行为不变**
- 退避公式抽为 `backoff.compute_backoff(attempt, base, cap, jitter) -> float`（`min(base * 2**(attempt-1) + uniform(0, jitter), cap)`）；`invoke_with_retry` 调用它——行为逐值不变，现有 `sleeps[0] < sleeps[1]` 钉住
- `llm_resilience.py` 变兼容 shim：`from app.reliability.retry import *` 式 re-export（`_TokenBucket`、`invoke_with_retry`、两个异常、`_classify_retryable`、`_rate_limiter`），保 `app/llm_legacy.py`（LEGACY 桥接区引用）不破；P3 facade 先例（兼容路径保留）
- `app/llm/adapter.py` caller 切 `from app.reliability.retry import invoke_with_retry`
- 测试迁移：`tests/smoke/test_llm_resilience.py` → `tests/contracts/test_reliability_retry.py`（import 与 `patch("app.llm_resilience.time.sleep")` 等 patch 目标同步改为 `app.reliability.retry.*`）；旧 shim 留 1 个兼容钉（`app.llm_resilience.invoke_with_retry is app.reliability.retry.invoke_with_retry` 同一对象断言）

### D4 RetryPolicy——固定预算单一来源（宪法 §11）

```python
# reliability/retry.py
RETRY_BUDGETS = {
    "sql_repair": int(os.getenv("MAX_SQL_REPAIR_RETRIES", "2")),   # 与 sql_graph 同 env 同默认
    "mcp": 2,                                                       # 与 mcp_client.max_attempts 同值
    "llm_transient": int(os.getenv("LLM_MAX_RETRIES", "2")),       # 宪法契约值；P6 遗留默认 5 收敛为 2
}
def get_budget(name: str) -> int
```
- `app/llm/config.py` `max_retries` 默认 `5` → `2`（env 可覆盖；P9 合宪修正）
- 一致性测试钉三处实现与表一致：sql_graph `_get_max_sql_retries()` 默认 2 / mcp_client `max_attempts=2` / LLMConfig 默认 2——P9 不改 sql_graph 与 mcp_client 实现，只钉「三处同一契约值」

### D5 timeout.py——分层默认表 + run_with_timeout

```python
LAYER_DEFAULTS = {
    "llm_request": float(os.getenv("LLM_TIMEOUT", "60")),
    "llm_total_budget": float(os.getenv("LLM_MAX_TOTAL_TIME", "90")),
    "mcp_request": float(os.getenv("RAGENT_MCP_TIMEOUT", "15")),
    "db_connect": 10.0,          # sql_tools.CONNECT_TIMEOUT_S 同值
    "db_statement_ms": 30_000,   # sql_tools.STATEMENT_TIMEOUT_MS 同值
    "background_task": float(os.getenv("MAX_TASK_DURATION", "600")),
}
MAX_TASK_DURATION = LAYER_DEFAULTS["background_task"]
async def run_with_timeout(awaitable, seconds) -> Any   # asyncio.wait_for 包装；超时抛 asyncio.TimeoutError
```
- 分层表是「分层超时单一来源声明」：P9 不改 llm/mcp/db 各自实现（已各自生效），只收编数值并钉一致性；**真新行为只有 background_task**
- 600s 依据：P0 基线 P95 180s；LLM 总预算 90s/次 × 最坏 repair 链 + SQL 30s，600s 富余；env 可调

### D6 Background Task Timeout 全链（伞形 §200）

`main.py:_run_confirmed_graph`：
```python
result = await run_with_timeout(graph.ainvoke(initial, config), MAX_TASK_DURATION)
```
新增 `except asyncio.TimeoutError` 分支（放在 generic Exception 之前）：
1. `update_phase(session_id, "error", failed_action=failed_action)`
2. `report_version_service.persist_error_run(session_id, user_id, requirement_draft_id=None, title="报告", error_detail={"code": "TASK_TIMEOUT", "message": f"后台任务超过 {MAX_TASK_DURATION}s 未完成", "kind": "timeout"}, query_snapshot=None, trace_id=initial.get("trace_id"))`
   - `persist_error_run` 签名 `requirement_draft_id: int` 放宽为 `Optional[int]`（`_persist` 本就接受 None）
   - graph 被 cancel 后 draft_id/query_snapshot 拿不到——传 None 诚实降级，落库行仍保证「不停在 generating」
3. error SSE 事件 `code=TASK_TIMEOUT / recoverable=False`（前端 reducer 只按 recoverable+failed_action 决定 canRetry，无 code 白名单特判——安全）；`done` 事件 `final_phase=error`

`registry.py` 本身不动：registry 是通用任务机制不感知 ReportVersion；超时保护放在知道怎么落库的 runner 层，runner 有界 ⇒ registry 不会挂死。

### D7 main.py SSE 出口收编（payload 契约不变）

- `_ERROR_FRIENDLY` / `_ERROR_CODE` / `recoverable: kind in (...)` 内联 → 删除本地表，改 `from app.reliability.errors import user_message, user_code, user_recoverable`
- `_build_sse_error` 逻辑与输出逐字段不变（`tests/test_sql_error_envelope.py` 5 钉 + `test_confirm_background.py` QUERY_TIMEOUT 钉回归）
- generic `except Exception` → `err = classify_exception(exc)`，`code=err.code`（未知异常仍 INTERNAL）、`recoverable=err.recoverable`（INTERNAL→False，现状不变）；`str(exc)[:300]` 消息保留
- `SECURITY_REJECTED / REQUIREMENT_INCOMPLETE / SESSION_NOT_FOUND` 保留 inline（session 级 API 语义错误，不在伞形 10 码表内，非 runtime failure envelope）

### D8 DiagnosePolicy 消费 errors.py（P8 衔接注记）

`sql_graph.py:DiagnosePolicy.decide`：
- kind 白名单 `("syntax","object","timeout","connection","permission","other")` → `errors.SQL_ERROR_KINDS`
- `if kind in ("timeout", "connection", "permission")` → `if not errors.agent_recoverable(kind)`
- 决策输出（action/reason/error_kind/recoverable/retry_target/confidence）**逐字段不变**——P8 41 例全量钉回归；这是「收编单一来源」不是「改决策」

## Files to change

| 路径 | 变更 |
|---|---|
| `backend/app/reliability/__init__.py` | 新建（空） |
| `backend/app/reliability/errors.py` | 新建：ErrorEnvelope/ErrorCode/两表/classify_*/user_* |
| `backend/app/reliability/backoff.py` | 新建：compute_backoff 纯函数 |
| `backend/app/reliability/retry.py` | 新建：收编 llm_resilience 全量 + RETRY_BUDGETS/get_budget |
| `backend/app/reliability/timeout.py` | 新建：LAYER_DEFAULTS/MAX_TASK_DURATION/run_with_timeout |
| `backend/app/llm_resilience.py` | 改为兼容 shim（re-export） |
| `backend/app/llm/adapter.py` | import 切 `app.reliability.retry` |
| `backend/app/llm/config.py` | `max_retries` 默认 5→2 |
| `backend/app/main.py` | `_run_confirmed_graph` 加 TimeoutError 分支 + `_build_sse_error` 换源 + generic except 走 classify_exception；删本地两表 |
| `backend/app/agent/sql_graph.py` | DiagnosePolicy 表驱动（行为不变） |
| `backend/app/services/report_version_service.py` | `persist_error_run` 的 `requirement_draft_id: int` → `Optional[int]` |
| `backend/tests/contracts/test_reliability_errors.py` | 新建：两表 + 10 码 + classify 全家 + user_* 原值钉 |
| `backend/tests/contracts/test_reliability_retry.py` | 自 `tests/smoke/test_llm_resilience.py` 迁移（patch 路径同步）+ shim 兼容钉 + RETRY_BUDGETS 钉 |
| `backend/tests/contracts/test_reliability_backoff.py` | 新建：递增/cap/jitter 上界/确定性 |
| `backend/tests/contracts/test_reliability_timeout.py` | 新建：分层表一致性 + run_with_timeout 正常/超时 + MAX_TASK_DURATION env |
| `backend/tests/contracts/test_retry_budget_consistency.py` | 新建：sql_graph/mcp_client/LLMConfig 与 RETRY_BUDGETS 三处同值钉 |
| `backend/tests/api/test_confirm_background.py` | 增 2 例：任务超时 → TASK_TIMEOUT error 事件 + persist_error_run 落库调用 |
| `backend/tests/smoke/test_llm_resilience.py` | 删除（迁至 contracts） |
| `docs/plans/2026-08-30-p9-reliability-layer.md` | 本 plan |
| `docs/plans/README.md` | 登记索引 |
| `CLAUDE.md` | §11 现状行更新（P9 已落地） |

## Reused existing utilities

| 复用 | 路径 |
|---|---|
| 限流/退避/重试算法 | `app/llm_resilience.py` —— 整体移动，不重写（伞形 §464） |
| MCP 错误边界 | `app/tools/mcp_errors.py:MCPErrorCode/MCPBoundaryError` —— P2 Task 2 钉死，只做 envelope 映射 |
| SQL kind 分类 | `app/tools/sql_tools.py:_classify_psycopg2_error` —— 原位保留，errors.py 消费输出 |
| P8 决策闭环 | `sql_graph.py:DiagnosePolicy` —— 只换数据来源，不改决策 |
| 错误落库 | `report_version_service.persist_error_run` —— 超时路径复用，仅放宽一个参数类型 |
| SSE 错误事件骨架 | `main.py:_build_sse_error` / `_run_confirmed_graph` 事件序列 —— 契约不变 |
| shim 先例 | `app/context` facade 兼容路径（P3）—— llm_resilience shim 同模式 |

## Verification

```bash
# 新增单测（TDD 逐任务）
cd backend && pytest tests/contracts/test_reliability_errors.py tests/contracts/test_reliability_retry.py tests/contracts/test_reliability_backoff.py tests/contracts/test_reliability_timeout.py tests/contracts/test_retry_budget_consistency.py -v

# P9 新旧交界回归
cd backend && pytest tests/contracts/test_diagnose_policy.py tests/test_sql_error_envelope.py tests/api/test_confirm_background.py tests/contracts/test_max_sql_repair_retries_env.py -v

# 全量回归（基线 627 passed / 0 failed，P9 后 = 627 - 迁移变动 + 新增）
cd backend && pytest --tb=short -q
```

**冒烟矩阵**：
1. 6 种 SQL kind → classify_sql_kind 出码/recoverable 两表各归各位（10 码全覆盖）
2. invoke_with_retry 行为逐值不变（迁移后既有 8 例全过 + patch 路径正确）
3. LLM_MAX_RETRIES 默认 2 且 env 真生效；RETRY_BUDGETS 三处实现同值
4. run_with_timeout：正常值透传 / 超时抛 TimeoutError
5. FakeGraph 挂死 + MAX_TASK_DURATION=0.05 → error 事件 TASK_TIMEOUT + persist_error_run 被调 + phase=error + done final_phase=error（不再永远 generating）
6. `_build_sse_error` 输出逐字段不变（5 钉 + QUERY_TIMEOUT 钉）
7. DiagnosePolicy 决策逐字段不变（P8 41 例零回归）

## Explicitly NOT doing

| 不做 | 理由 |
|---|---|
| 改 `mcp_client.py` 重试实现 / `MCPBoundaryError` | P2 Task 2 钉死；P9 只在 errors.py 加映射 |
| 移动 `_classify_psycopg2_error` | tools 域边界原位保留；移动牵连 import 面无收益 |
| 改 SSE 用户码 `QUERY_*` / 前端任何文件 | 前端已消费的稳定契约（§11「用户收到稳定错误信息」）；改动即破约 |
| adaptive retry / 动态预算 / 按用户调整 | 伞形 §194：固定预算，adaptive 等 Evaluation 数据说话 |
| Langfuse 落库 decision/envelope | P13 范围 |
| P10 Report Validator 的 REPORT_VALIDATION_ERROR 生产者 | 码表 P9 就位，生产者归 P10 |
| registry 层加超时 | 超时保护放 runner 层（main.py）——registry 是通用机制不感知 ReportVersion 落库；runner 有界即不挂死 |
| SSE Disconnect 取消后台任务 | 宪法 §11：断连 ≠ 失败，后台跑完语义保留 |
| Cancellation Contract | 伞形 §199「除非将来明确实现」——不做 |
| 引入 Prometheus/Grafana 指标 | 宪法 §12 禁止 |
| llm_legacy.py 切新 import | LEGACY 桥接区豁免；shim 保兼容 |

## TDD Tasks

### T1 errors.py——ErrorEnvelope + 两表 + classify 全家
- [ ] Step1 `test_reliability_errors.py` 红：ErrorCode 10 码齐全；AGENT/USER 两表逐 kind 断言；classify_sql_kind 6 kind→码映射；classify_llm_exception（APITimeoutError→LLM_TIMEOUT recoverable=True / AuthenticationError→INTERNAL_ERROR recoverable=False）；classify_mcp_error 三码映射；classify_exception 兜底；user_message/user_code 6 值与现状原值逐字相等（防漂移钉）
- [ ] Step2 `reliability/errors.py` 实现至绿

### T2 retry.py 收编 + backoff 抽出 + shim
- [ ] Step1 `test_reliability_backoff.py` 红：递增/cap/jitter 上界/attempt=1 → base
- [ ] Step2 `reliability/backoff.py` + `reliability/retry.py`（llm_resilience 全量移入，退避改调 compute_backoff）
- [ ] Step3 `tests/smoke/test_llm_resilience.py` → `tests/contracts/test_reliability_retry.py`（import/patch 路径改 `app.reliability.retry`）；shim 化 `llm_resilience.py`；adapter 切新 import；补 shim 兼容钉
- [ ] Step4 全部绿 + grep 确认无残留 `from app.llm_resilience import`（adapter 外）

### T3 RetryPolicy 预算表 + LLM 默认收敛
- [ ] Step1 `test_retry_budget_consistency.py` 红：RETRY_BUDGETS 三值 = 契约（2/2/2）；sql_graph `_get_max_sql_retries()` / mcp_client `max_attempts` / `LLMConfig().max_retries` 同值；`LLM_MAX_RETRIES=7` env 覆盖生效
- [ ] Step2 `retry.py` 加 RETRY_BUDGETS/get_budget；`config.py` 默认 5→2；跑 `test_llm_resilience` 原断言确认 3-call 场景在 retries=2 下仍成立

### T4 timeout.py——分层表 + run_with_timeout
- [ ] Step1 `test_reliability_timeout.py` 红：LAYER_DEFAULTS 与各层实现同值（llm config.timeout / RAGENT_MCP_TIMEOUT / sql_tools 两常量）；run_with_timeout 正常透传 + 超时抛；MAX_TASK_DURATION env 覆盖
- [ ] Step2 `reliability/timeout.py` 实现至绿

### T5 Background Task Timeout 全链
- [ ] Step1 `test_confirm_background.py` 增 2 例红：FakeGraph `await asyncio.sleep(999)` + monkeypatch `app.main.MAX_TASK_DURATION=0.05` → 断言 `[error, done]`、code=TASK_TIMEOUT、update_phase("error")、persist_error_run 以 requirement_draft_id=None 被调；另断言未超时路径不受影响
- [ ] Step2 `main.py` 接 run_with_timeout + TimeoutError 分支；`report_version_service.py` 放宽参数类型

### T6 main.py SSE 出口收编
- [ ] Step1 既有 `test_sql_error_envelope.py` 5 钉 + `test_confirm_background.py` QUERY_TIMEOUT 钉为回归基线（先跑确认绿）
- [ ] Step2 `main.py` 删 `_ERROR_FRIENDLY`/`_ERROR_CODE`/内联 recoverable，切 `errors.user_message/user_code/user_recoverable`；generic except 切 classify_exception
- [ ] Step3 全部既有钉零回归（输出逐字段不变）

### T7 DiagnosePolicy 收编
- [ ] Step1 P8 既有 41 例先跑确认绿（回归基线）
- [ ] Step2 `sql_graph.py` 两处硬编码换 errors.py 表驱动（SQL_ERROR_KINDS 白名单 + agent_recoverable fail 分支）
- [ ] Step3 41 例零回归 + 新增 1 例反向钉（errors.SQL_ERROR_KINDS 与 DiagnosePolicy normalize 同源）

### T8 收尾
- [ ] Step1 全量 `cd backend && pytest -q`：627 基线零回退 + 新增全绿
- [ ] Step2 CLAUDE.md §11 现状行更新 + plan 状态改已完成（带 commit）+ README.md 索引移已完成区
- [ ] Step3 git commit（`feat(p9): ... + plan: p9-reliability-layer`）

## 预算影响预估

新增测试约 28–32 例（errors ~12 / retry 迁移 9+1 钉 / backoff ~4 / timeout ~5 / budget ~4 / 背景超时 2）；删除 `tests/smoke/test_llm_resilience.py` 原 9 例（迁移非新增）。基线 627 → 预计 ~650。

## Open questions

无。SSE 用户码与 runtime 码双轨制（D2）若用户 review 有异议再拍板；其余按宪法 + 伞形既定。
