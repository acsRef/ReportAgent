# 故障注入基线：4 个占位用例接进真实 runner + 期望改写为架构真实契约

> 状态: 已完成

## Context

`evaluation/baseline_cases.json` 里 4 条 `requires_fault_injection=true` 的用例（`sql-failure-fault-injection` / `sql-repair-recovers` / `mcp-failure-unavailable` / `mcp-failure-timeout`）自 2026-08-25 建基线起**从未真正执行过**：`evaluation/runner.py` 的 `run_case` 见到 flag 就直接 return skip，而 runner 本身从不发送 `X-E2E-Fault` / `X-E2E-McpDown` 请求头。结果是基线报告里 SQL 失败、SQL 修复、MCP 失败三类**错误路径零覆盖**，而 `skip` 在报告里看起来像「跑过了」。

2026-11-02 侦查时发现两类问题，性质不同：

1. **Runner 侧（真缺口）**：不发病 inject 头 → 不管后端 seam 多完整都验不到。
2. **期望侧（过期契约）**：两条 MCP 用例期望 `sse_error_code = MCP_TIMEOUT / MCP_UNAVAILABLE`，但架构是 **MCP 失败 → 工具层 graceful 降级 → 本地 schema 兜底 → 主链照常成功**。`rag_schema.search_tables_from_rag` 捕获 `MCPBoundaryError` 后直接 `return []`，错误码在工具层就被吞掉，**永远到不了 SSE**。这两条用例按原样永远红——它们写的是「MCP 挂了必须报错」，而设计要求是「MCP 挂了不许阻塞、也不许伪装成空结果」。

更正一处侦查时的误判：`backend/app/reliability/mcp_down.py` 的 `X-E2E-McpDown` seam **已经接进 main.py**（chat 655/713、adjust 810、confirm 1191），不是死代码。第一次 grep 的正则写错（`mcp_down_scope` ≠ `mcp_down.scoped`）导致误判。

## Design

三件事，按「先让 runner 说真话，再让期望说真话，最后让注入与真实同形」排序。

### 1. 数据集显式声明注入规格（`evaluation/schema.py`）

新增 `FaultSpec`，把「这条 case 靠什么 header 造故障」写进数据集，而不是散在 runner 的 if 里：

```python
class FaultSpec(BaseModel):
    seam: Literal["fault", "mcp_down"] = "fault"
    kind: Literal["object_not_found", "permission"] = "permission"
    mode: Literal["once", "persistent"] = "persistent"
    on: Literal["chat", "confirm", "both"] = "both"

    def header(self) -> dict[str, str]        # X-E2E-Fault / X-E2E-McpDown
    def headers_for(request) -> dict[str, str]  # on 不覆盖该请求时返回 {}
```

`BaselineCase.fault` 存在时自动置 `requires_fault_injection=True`（validator），避免「标了故障却没规格」的静默 skip。

### 2. Runner 真发头（`evaluation/runner.py`）

- `_stream_sse(..., extra_headers=None)`：头与 `Authorization` 合并。
- `run_case`：`chat` 用 `fault.headers_for("chat")`，`confirm` 用 `fault.headers_for("confirm")`；**只有「声明了故障但没有规格」才 skip**（`reason=requires_fault_injection_without_spec`），语义从「有 flag 就跳过」改成「规格缺失才跳过」。
- 新增 `--only-id`：单车例迭代（跑一次真 LLM 链路 1~2 分钟，全量 20 例 ~10 分钟）。

### 3. 期望改写为「实测到的真实契约」

先跑真链路探针（`.e2elogs/probe_fault_cases.py`，只读）拿到事实，再写期望：

| case | 注入 | 实测 | 期望 |
|---|---|---|---|
| `sql-failure-fault-injection` | `kind=permission;mode=persistent` on confirm | FAILED | `verdict=FAILED` + `sse_error_code=QUERY_PERMISSION` |
| `sql-repair-recovers` | `kind=object_not_found;mode=once` on confirm | SUCCESS / 6 行 | `verdict=SUCCESS` + `sql_nonempty` + `rows_gt=0` + `table_present` |
| `mcp-failure-unavailable` | `X-E2E-McpDown` on both | SUCCESS / 6 行 / chart | `verdict=SUCCESS` + `rows_gt=0` + `table_present`（降级不伪装空结果） |
| `mcp-failure-timeout` → **`mcp-down-requirement-scope`** | `X-E2E-McpDown` on chat only | 卡片照出、执行 SUCCESS | `requirement.status=complete` + `verdict=SUCCESS` |

`mcp-failure-timeout` 改名：`MCP_TIMEOUT` 在 seam 里没有注入形态（`fault_inject` 白名单只有 `object_not_found` / `permission`，`mcp_down` 只造 `UNAVAILABLE`），留一个叫 timeout 却测不了 timeout 的 id 是误导。改名后语义准确，原 id 的消失记在本文件。

### 4. 注入与真实失败同形（`backend/app/agent/sql_graph.py`）

探针抓到的真缺陷：注入 `permission` 后 SSE 报的是 **通用 `QUERY_FAILED` / kind=`other`**，而不是「权限不足」。根因是 `_evaluate` 的注入分支只写了 `evaluate_result`，没写 `state["error"]`；下游 `_diagnose` 的 fail 分支 `state.get("error")` 取不到 err → `persist_error_run` 用兜底 `{code: QUERY_FAILED, kind: other}`。

真实失败路径（同一函数下方）会同时写 `evaluate_result` 与 `"error"`。修复方式就是让注入分支与它同形：

```python
_injected_err = ErrorDetail(code="EXECUTION_ERROR", message="权限不足，无法执行该查询", kind=_fk)
return {
    "evaluate_result": EvaluateResult(status="FAILED", kind=_fk, error=_injected_err).model_dump(),
    "execution_status": "FAILED",
    "error": _injected_err,
}
```

只影响 `REPORTAGENT_E2E=1` 的注入分支，生产行为零变化。

## Files to change

| 文件 | 变更 |
|---|---|
| `evaluation/schema.py` | 新增 `FaultSpec` + `BaselineCase.fault` + 自动置 flag 的 validator |
| `evaluation/runner.py` | `extra_headers` / 按请求发头 / 只有无规格才 skip / `--only-id` |
| `evaluation/baseline_cases.json` | 4 条用例补 `fault` 规格，期望改写为实测契约，`mcp-failure-timeout` → `mcp-down-requirement-scope` |
| `evaluation/tests/test_dataset.py` | 钉 4 个新 id、钉「故障用例必须有规格」，钉两种 header 形态 |
| `backend/app/agent/sql_graph.py` | 注入分支补 `state["error"]`（与真实失败同形） |
| `backend/tests/contracts/test_fault_inject.py` | 新增 1 例：注入的 error 带 kind=permission → `QUERY_PERMISSION` / 「权限不足」 |

## Reused existing utilities

- `reliability/fault_inject.py`（`X-E2E-Fault`，双 gate fail-closed）与 `reliability/mcp_down.py`（`X-E2E-McpDown`）——**不新增 seam**
- `reliability/errors.py` 的 `user_code` / `user_message`——断言的用户视图复用同一张表
- `evaluation/checker.py` 既有 `ObservedTurn` / `check_turn`——不扩观测字段
- runner 既有 `_login` / `_stream_sse` / `_fill_missing_fields` / `_observe_turn`——只加一个 `extra_headers` 参数

## Verification

```bash
# 单例真跑（需 PG + ragent MCP :8000 + backend :8100 且 REPORTAGENT_E2E=1）
python -m evaluation.runner --only-id sql-failure-fault-injection
python -m evaluation.runner --only-id sql-repair-recovers
python -m evaluation.runner --only-id mcp-failure-unavailable
python -m evaluation.runner --only-id mcp-down-requirement-scope

cd backend && pytest            # 离线回归
pytest evaluation/tests -q      # 数据集契约
```

真跑结果（2026-11-02，全部 pass，逐维）：

```
sql-failure-fault-injection | pass | execution.verdict=pass, execution.sse_error_code=pass
sql-repair-recovers         | pass | execution.verdict/sql_nonempty/rows_gt=pass, report.table_present=pass
mcp-failure-unavailable     | pass | execution.verdict/sql_nonempty/rows_gt=pass, report.table_present=pass
mcp-down-requirement-scope  | pass | requirement.status=pass, execution.verdict=pass
```

`sql-failure-fault-injection` 的 `sse_error_code=pass` 就是「权限不足」真落地的证据——修复前同一条用例只能拿到 `QUERY_FAILED`。

离线回归：backend **1222 passed / 1 skipped**（+1 契约例）；evaluation **80 passed / 25 skipped**（skipped 全是 env-gated 的真后端用例）。

## Explicitly NOT doing

- **不造 `MCP_TIMEOUT` 注入形态**：需要给 MCP 客户层加 timeout seam（选项 C），本次按用户决议走 A——MCP 侧只验「降级不伪装」契约
- **不改产品行为**：MCP 失败仍是 graceful 降级，不改成 fail-fast（那是架构变更，需单独 plan）
- **不扩 seam kind**：`object_not_found` / `permission` 两种够用，加新 kind 属新需求
- **不跑全量 20 例基线**：本次只验证 4 条故障用例 + 离线回归；全量基线重跑是独立动作（≈10 分钟真 LLM）

## 落地记录（2026-11-02）

按设计落地，偏差 2 项：

1. **侦查误判已更正**：`mcp_down` seam 早已接进 main.py，不是死代码；上一版口头结论撤回，本文件已写明。
2. **`mcp-failure-timeout` 改名而非保留**：原 id 承载的是「timeout 必须明确报 MCP_TIMEOUT」，与本仓库的降级契约相反且无法注入，保留会持续误导；改为 `mcp-down-requirement-scope`，原 id 不再存在。

额外收获：探针抓到 `permission` 注入的 **error kind 丢失**（SSE 落 `other/QUERY_FAILED`），已在 `sql_graph._evaluate` 修掉并加契约钉死——这正是「错误用例必须真跑」的价值：写 10 个单测也发现不了这条两跳链路。
