# P6 实施：LLM Adapter 收敛 + remaining_token_budget 接通 + Golden 对比

> 状态: 已完成
> 上游: [2026-08-25-refactor-master-freeze.md](2026-08-25-refactor-master-freeze.md) §八/§十五 P6 + [2026-08-29-p5-tool-mcp-contract.md](2026-08-29-p5-tool-mcp-contract.md)（P5 已完成 b9d73aa/10596cf）+ CLAUDE.md §二/§八/§十五 + [[memory:p4c-landed]] 第9条
> 协作: B 顺序第二 plan；P5 完成后再开 P6，本 plan 仅 P6

## Context

### 宪法 §八 LLM Policy 要求
- 收敛 `backend/app/llm.py: call_llm` + `llm_resilience.py: invoke_with_retry` 到 `backend/app/llm/` Adapter（`generate` / `generate_structured` + reasoning normalization + retry/timeout 统一）
- Agent 代码零 provider 硬编码、不自解析 JSON、不带模型兼容逻辑
- provider/model/base_url/auth 走 `LLM_*` settings（从 `MINIMAX_*` 收敛，兼容旧 env）
- P4c 诚实降级顺带：`ContextRuntime.remaining_token_budget` 主图 caller 真传（Adapter 知道 `context_window` 后由 caller 用 `state["prompt_char_used"]` 等 derived var 算 remaining，接通该环）

### 现状盘点（2026-08-29）
| 项 | 实际 | 待做 |
|---|---|---|
| llm 散装 | `app/llm.py:87 call_llm` (ChatMinimax→ChatOpenAI fallback, 保留 `<think>` full response) + `llm_resilience.py:78 invoke_with_retry` (token bucket + classify retryable + 90s 总超时) | 收敛为 `app/llm/` package：`adapter.py`/`config.py`/`retry.py` + `generate`/`generate_structured` |
| Agent 直调 | 7 处现役直调 `call_llm`：`intent.py:79` / `report_graph.py:73` / `requirement_parser.py:128` / `sql_graph.py:213,357,506` / `memory/conversation.py:93`（legacy parent_graph:501 不动 P15） | 改为调 Adapter；Agent 内 JSON 解析与 `<think>` 剥离下沉至 Adapter |
| Settings | `_LLM_CONFIG` 读 `LLM_MODEL / LLM_API_KEY / MINIMAX_API_KEY / LLM_BASE_URL` 零散，`SILICONFLOW` 分离 | 收敛 `LLM_*` settings（`backend/app/config.py` 或 `app/llm/config.py`），`MINIMAX_*` 兼容别名，`context_window` 暴露 |
| Reasoning | `llm.py:92-96` 注释"keep FULL response (think+answer)" 与 §八"think 标签剥离"冲突 | Adapter 层统一 `strip_think_tags` 归一化 |
| remaining_budget | `assembler:74 _apply_token_budget` 支持 `remaining_token_budget=min(remaining,4000)` 但 4 caller 传 `None` 诚实降级 + 防护钉 `test_graph_caller_does_not_invent_remaining_budget` | P6 接通：caller 用 `context_window - prompt_char_used//4` 等 derived var 算 remaining 真传 |

## Design

### D1 LLM Adapter 收敛
新建 `backend/app/llm/` package：
- `config.py`：`LLMConfig(BaseSettings)` 读 `LLM_PROVIDER / LLM_MODEL / LLM_API_KEY / LLM_BASE_URL / LLM_TIMEOUT / LLM_MAX_RETRIES / LLM_CONTEXT_WINDOW`，兼容 `MINIMAX_API_KEY→LLM_API_KEY`、`MINIMAX_BASE_URL→LLM_BASE_URL`；`context_window` 默认 128k（MiniMax/Moonshot 通用），可 env 覆盖
- `adapter.py`：`LLMAdapter` 暴露 `generate(prompt, **kw) -> str` 与 `generate_structured(prompt, schema/pydantic) -> dict`；内部：
  - reasoning normalization：`strip_think_tags`（正则 `<think>.*?</think>` + `<reasoning>` 变体，大小写/跨行）
  - 统一 `invoke_with_retry`（复用 `llm_resilience` 的 TokenBucket + classify + 90s 总超时，`max_retries` 由 config）
  - `ChatMinimax` → `ChatOpenAI` fallback 保留但下沉至 Adapter，不在 Agent 侧
  - `generate_structured` 负责 JSON 解析 + schema 校验（Agent 不自解析）
- `retry.py`：薄封装复用 `llm_resilience.invoke_with_retry`（或内联迁移，避免循环）
- `__init__.py`：`get_llm_adapter() -> LLMAdapter` 单例 + `generate` 快捷函数（兼容旧 `call_llm` 名称做 deprecated alias）

### D2 Agent 零硬编码
7 处 `call_llm(...)` 改为 `get_llm_adapter().generate(...)` 或 `generate_structured`：
- `intent.py` / `report_graph.py` / `requirement_parser.py` / `sql_graph.py:3处` / `memory/conversation.py` 统一走 Adapter
- 删除 Agent 内 `json.loads` / `strip("<think>")` / `MINIMAX_*` 兼容逻辑；模型兼容逻辑仅 Adapter 层

### D3 Settings 收敛
- `backend/app/config.py` 或 `app/llm/config.py` 新增 `LLM_*`；`MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 映射为兼容别名（`Field(validation_alias=...)` 或启动时 env 回填）
- `context_window` 供 P4c 接通使用（`LLM_CONTEXT_WINDOW` env，默认 128k tokens）

### D4 P4c 诚实降级接通
4 caller 在 `assembled_context` 已就绪后，用 `remaining = context_window - est_prompt_tokens` 算法填 `remaining_token_budget`：
- `est_prompt_tokens ≈ prompt_char_used //4`（`assembled_context` 字符数或 `state` 中 `messages` 长度估算；P6 以 `len(assembled_context)` 为主，fallback 4000）
- `remaining_token_budget = max(0, context_window - est_prompt_tokens)`，传入 `ContextRuntime.build(..., remaining_token_budget=remaining)`
- 仅当 `assembled_context` 可得时真传，否则仍 `None` 诚实降级（防护钉更新为“当可得时必真传”）

### D5 Golden before/after
- `docs/plans/p6-golden-before-after.md`：离线 proxy 5 用例 × 2 输出对比（intent/requirement/sql/report/memory），`generate` 与 `call_llm` 文本一致性（strip_think 后 JSON 等价）
- 单测：`test_llm_adapter_reasoning_strip` + `test_llm_adapter_settings_alias` + 更新 `test_graph_caller_does_not_invent_remaining_budget` 为真传断言

## Files to change

| 路径 | 变更 |
|---|---|
| `backend/app/llm/`（新建 package） | `config.py` + `adapter.py` + `__init__.py` |
| `backend/app/llm.py` | 保留 deprecated `call_llm` → 转调 Adapter（兼容一周期） |
| `backend/app/llm_resilience.py` | 保留，Adapter 复用其 `invoke_with_retry`（或迁入 `llm/retry.py`） |
| `backend/app/agent/intent.py` | `call_llm` → Adapter |
| `backend/app/agent/report_graph.py` | 同上 |
| `backend/app/agent/requirement_parser.py` | 同上 + JSON 解析下沉 |
| `backend/app/agent/sql_graph.py` | 3 处同上 |
| `backend/app/memory/conversation.py` | 同上 |
| `backend/app/agent/requirement_analysis_graph.py` | 接通 remaining 真传 |
| `backend/app/agent/confirmed_execution_graph.py` | 同上 |
| `backend/tests/contracts/test_llm_adapter.py` | 新建：reasoning strip + settings alias + generate_structured |
| `backend/tests/contracts/test_context_assembler_real_filter_budget.py` | 更新防护钉为真传 |
| `docs/plans/p6-golden-before-after.md` | 新建：Golden 对比 |
| `docs/plans/README.md` | 登记 P6 进行中 |

## Reused

| 复用 | 路径 |
|---|---|
| `llm_resilience.invoke_with_retry` + `_TokenBucket` | `backend/app/llm_resilience.py` |
| `call_llm` 的 ChatMinimax→ChatOpenAI fallback 逻辑 | `backend/app/llm.py` 下沉 |
| `ContextRuntime`/`assembler._apply_token_budget` | `backend/app/context/` |
| `test_graph_caller_does_not_invent_remaining_budget` 结构 | `backend/tests/` |

## Verification

```bash
pytest tests/contracts/test_llm_adapter.py -v
pytest tests/contracts/test_tool_contract_14_fields.py tests/contracts/test_mcp_tool_allowlist_freeze.py -v
pytest tests/graphs/test_requirement_analysis_sqlgate.py -k dictionary -v
pytest tests/contracts -q  # 278 全绿基线不回退
pytest tests/smoke -q       # 115 全绿
pytest -q                  # 全量 686+ 不回退（persistence 缺模块 2 失败为预存环境非回归）
```

冒烟：Adapter 单例、generate 剥 think 后 JSON 等价、settings 别名、remaining 真传时 `min(remaining,4000)` 生效。

## Explicitly NOT doing

| 不做 | 理由 |
|---|---|
| 改 ContextRuntime/assembler/SelectiveRecallPolicy | P4c 已 PASS |
| 删 build_session_context 兼容路径 | facade 保留 |
| 动 legacy/agents/parent_graph.py | P15 |
| 重写 mcp_client | P2 已完成 |
| 让 Tool 没 description | Forbidden |
| 新建 generic 文件夹 | 禁 |

---

## TDD Tasks

### T1 Adapter 骨架 + reasoning strip + settings alias
- [ ] Step1 写 `test_llm_adapter_reasoning_strip` + `test_llm_adapter_settings_alias` 红 → 绿
- [ ] Step2 新建 `app/llm/config.py` + `adapter.py` + `__init__.py`

### T2 Agent 7 处迁移
- [ ] Step1 批量 `call_llm` → `Adapter.generate(_structured)`，删 Agent 内 JSON/兼容逻辑
- [ ] Step2 `llm.py:call_llm` 保留 deprecated alias

### T3 remaining_token_budget 接通
- [ ] Step1 4 caller 真传 remaining（derived `len(assembled_context)//4`）
- [ ] Step2 更新防护钉 + assembler 交互测试

### T4 Golden before/after 文档 + 全量回归

---

## Post-review Fix（2026-08-29）

用户第二轮 review（对照 commit `63ba7805f65fbff0481f72d2948e5c342b6439f6`）发现 3 个问题（2 P1 + 1 P2），已修：

### P1-1：`generate_structured_safe` 把 schema validation 当 parse failure 重试 LLM
**根因**：原实现用 `except ValueError` 兜底，无法区分 parse 失败与 schema 校验失败。

**修复**：引入两个继承 `ValueError` 的独立异常类型：
- `StructuredParseError` —— LLM 输出无法解析为 JSON（可重试/兜底）
- `SchemaValidationError` —— JSON 解析成功但不满足 schema（不可重试，直接向上抛）

`generate_structured_safe` 的 except 只 catch `StructuredParseError`；schema 校验失败不再触发第二次 LLM 调用。`_validate_against_schema()` 抽作单一校验入口供 `generate_structured` 与 `generate_structured_safe` 共用。

**文件**：[adapter.py](backend/app/llm/adapter.py)（L36-49 定义异常类，L52-64 校验入口，L107-176 generate_*）

**测试**：`test_generate_structured_schema_failure_raises_schema_validation_error`（schema 失败只 1 次 LLM）+ `test_generate_structured_safe_does_not_retry_on_schema_failure`（带/不带 parser 均不重试 schema）+ `test_generate_structured_safe_parser_fallback_on_parse_failure`（反向证明 parse 失败仍兜底）。

### P1-2：`LLMConfig.timeout` 死配置
**根因**：`LLMConfig.__init__` 读 `LLM_TIMEOUT` 环境变量，但 `to_chat_kwargs()` 未传 `timeout` 字段，配置从未进入 `ChatOpenAI` 构造。

**修复**：`to_chat_kwargs()` 增加 `"timeout": self.timeout`，真正接入执行链。

**文件**：[config.py](backend/app/llm/config.py)（L18-26）

**测试**：`test_to_chat_kwargs_includes_timeout`（`LLM_TIMEOUT=42` 真传）+ `test_to_chat_kwargs_default_timeout`（默认 60）。

### P2：remaining budget 精度（deferred，不阻塞 P6 merge）
**现状**：4 caller 真传 `remaining_token_budget`，算法为 `context_window - (len(user_query) + 8000) // 4`。

**P6 D4 原文要求**：`len(assembled_context) // 4` 为主、fallback 4000。

**差距**：当前仍使用固定常量 `_TYPICAL_CONTEXT_BUDGET_CHARS = 8000` 占位，不是真实已用 prompt tokens。

**判定**：估算精度问题，不是架构断裂；用户判定 P2，可继续作为 P15 收口项或 P9 Reliability follow-up。后续接 Unified LLM prompt accounting 时统一替换。

### Reviewer 关于 D2/D5 妥协的明示
用户明确：本次不是"P6 已完成 D2/D5"，而是：
- ✅ **D2 Adapter migration**：Adapter 基础设施 + 7 处 caller 迁移
- ⏸️ **D2 Agent zero JSON parsing**：deferred to P15（38 个旧测试 mock 边界限制）
- ⏸️ **D5 fallback centralization**：deferred to P15（待 caller 全部走 `generate_structured_safe`）

P15 收口路径：
1. 38 个测试 mock `app.agent.*.call_llm` → `app.llm.adapter.LLMAdapter.generate`
2. caller：`call_llm + safe_json_parse` → `generate_structured_safe`
3. 删除 `app.llm.call_llm`

### Commit
- `59b8f71` fix(p6-review): Adapter 异常语义拆 + LLMConfig.timeout 真接 ChatOpenAI

回归基线：contracts+smoke **406 passed** / graphs **71 passed** / llm_adapter **13 passed**（含 6 新增）。
