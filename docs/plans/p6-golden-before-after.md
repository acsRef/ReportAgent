# P6 Golden Before/After（离线 proxy）

> 状态: 冻结（P6，2026-08-29）
> 对比 `call_llm` 旧路径 vs `LLMAdapter` 新路径在 5 场景下剥 think 后文本/JSON 等价。
> 关联 plan: [2026-08-29-p6-llm-adapter.md](2026-08-29-p6-llm-adapter.md) §D5

## 方法

- `LLMAdapter.generate(prompt)`：内部 `invoke_with_retry(ChatOpenAI.invoke) → strip_think_tags` → 返回字符串。
  - `invoke_with_retry` 现接 `LLMConfig.max_retries / max_total_time`（M1）；trace 埋点失败 `logger.warning`（M3）。
- `LLMAdapter.generate_structured(prompt, schema=None)`：内部 `generate → _extract_json → (失败则 safe_json_parse 兜底) → (有 schema 则 pydantic.model_validate)` → 返回 dict（H1）。
- `LLMAdapter.generate_structured_safe(prompt, parser=None, schema=None)`：Adapter 内统一 fallback（未来 caller 选用；当前 caller 仍走简化 `call_llm → safe_json_parse` 路径以兼容旧测试 mock 边界，见 §收口现状）。

测试文件: `backend/tests/contracts/test_llm_adapter.py`（7 钉：reasoning strip 2 + settings alias 3 + generate{_structured} 2）。

## 5 用例（call_llm 旧路径 vs Adapter 新路径）

### 用例 1 — intent classify

- prompt: `classify_intent("帮我查华东销售")` 构造的 prompt（含 `_format_tools_for_prompt` 块）
- LLM mock 原始输出（before/after 共用）:

```
<think>用户问华东销售，属于报表查询，时间未指定但默认今年</think>
{"kind": "report", "confidence": 0.86, "reason": "销售+区域维度命中"}
```

- 旧路径: `call_llm → strip_think_tags → safe_json_parse` → `{"kind":"report","confidence":0.86,"reason":"销售+区域维度命中"}`
- 新路径: `get_llm_adapter().generate_structured(prompt)` → `_extract_json` 拿首尾 `{...}` → 同 dict
- diff: think 标签剥离后 JSON 等价；新路径在 Adapter 内完成解析

### 用例 2 — requirement parse

- prompt: `_PARSE_PROMPT.format(user_query, schema_text)` + 字典块 + `format_context_block(assembled_context)` 前置
- LLM mock:

```
<think>需要判断 time/scope/metric ...</think>
```json
{"summary":"查询华东今年销售额","target_metrics":["销售额"],"time_range":"今年","scope":["华东"],"dimensions":["时间","区域"],"analysis_methods":["trend_analysis"],"confidence":0.9,"missing_fields":[],"assumptions":[]}
```

- 旧路径: `call_llm → raw with <think> + markdown fence → safe_json_parse` → 同 dict
- 新路径: `generate_structured` → `_extract_json` 失败则 `safe_json_parse` 兜底 → 同 dict；若传 `schema=RequirementCard` 则 `pydantic.model_validate` 再校验
- diff: fence/think 均被归一化；新路径可选 schema 校验（H1）

### 用例 3 — sql plan

- prompt: `_plan`（含 `_PLAN_TABLE_HINTS + _FK_CHAIN_HINTS + _PLAN_FEWSHOT + today`）
- LLM mock:

```
<think>time=今年 region=华东 metric=销售 → run_direct</think>
{"target_metric":"销售额","dimensions":["时间"],"filters":[{"field":"region","operator":"=","value":"华东"}],"aggregation":"sum","time_range":"今年","clarify_decision":{"action":"run_direct","missing_dimensions":[],"predicted_table":"fact_sales","confidence":0.9,"reasoning":"三维度均明确"}}
```

- 旧路径: `safe_json_parse(call_llm(prompt))` → 同 dict
- 新路径: `generate_structured` → 同 dict
- diff: 等价

### 用例 4 — sql generate

- prompt: `_generate_sql`（`_FK_CHAIN_HINTS + _SQL_GENERATION_RULES + faq_block + confirmed_requirement + format_context_block`）
- LLM mock:

```
<think>需要 JOIN dim_date 过滤 full_date ...</think>
SELECT dim_region.region_name AS "区域", SUM(fact_sales.amount) AS "销售额" FROM fact_sales LEFT JOIN dim_date ON fact_sales.date_id = dim_date.date_id LEFT JOIN dim_region ON fact_sales.region_id = dim_region.region_id WHERE dim_date.full_date >= '2024-01-01' AND dim_date.full_date < '2025-01-01' GROUP BY dim_region.region_name
```

- 旧路径: `call_llm → extract_sql` → 同 SQL
- 新路径: `get_llm_adapter().generate([{"role":"user","content":prompt}]) → extract_sql` → 同 SQL
- diff: think 剥离不影响 SQL；两者 `extract_sql` 结果字符级一致

### 用例 5 — memory compress

- prompt: `compress_and_extract`（`old_digest + batch_text + {L2_MAX_CHARS} 指令`）
- LLM mock:

```
<think>提炼摘要与事实</think>
{"summary":"用户关注华东销售趋势与退货分析","extracted_schemas":[{"type":"field_mapping","user_term":"销售额","db_field":"total_amount","table":"fact_sales"}],"extracted_preferences":["偏好柱状图"]}
```

- 旧路径: `safe_json_parse(call_llm(prompt))` → 同 dict
- 新路径: `generate_structured` → 同 dict
- diff: 等价

## 数字出处（实测 2026-08-29 master b9d73aa + 10596cf + 579382c 之后）

| 指标 | 数值 | 来源 |
|------|------|------|
| 全量离线 | **697 collected / 696 passed / 1 skipped / 4 warnings**（207s）| `pytest --collect-only -q` → 697 tests collected；`pytest -q` → 696 passed + 1 skipped (e2e env) |
| contracts | 164 tests | `pytest -m contracts --collect-only -q` → 164/697 tests collected |
| smoke | 293 tests | `pytest -m smoke --collect-only -q` → 293/697 tests collected |
| graphs | 102 tests（697 - 164 - 293 - 其他标记 + deselected）| 由 `pytest -m graphs --collect-only -q` 验证 |
| P6 新增 contract | 7 tests（reasoning strip 2 + settings alias 3 + generate{_structured} 2）| `tests/contracts/test_llm_adapter.py` |
| LLM resilience smoke | 11 tests（TokenBucket 2 + classify 2 + retry 5 + rate-limit 1 + `call_llm deprecated alias` 1）| `tests/smoke/test_llm_resilience.py` |
| Warnings | 4（facade DeprecationWarning × 1 + `app.llm.call_llm deprecated` × 3）| by design：facade 兼容（§六）+ call_llm deprecated alias 是 §十四 P15 删除前的兼容入口 |
| P4c 基线 | 41 new 钉 / 84 contract / 40 baseline schema | `docs/plans/2026-08-29-p4c-context-runtime-graph-integration.md` §Verification |

以上均为在 repo 根 `backend/` 下 `pytest -q` / `--collect-only -q` 可复现的离线基线。

## 收口现状（与 plan §D1/D2/D4 对齐）

| Plan 项 | 实际 |
|---------|------|
| D1 `generate_structured(prompt, schema/pydantic)` | ✅ [adapter.py:76-105](backend/app/llm/adapter.py#L76-L105) — `schema` 可选，BaseModel class/instance 都接受，`model_validate + model_dump(mode="json")` |
| D1 `strip_think_tags` 归一化 | ✅ [adapter.py:20-25](backend/app/llm/adapter.py#L20-L25) — `<think>` + `<reasoning>` 双正则 |
| D1 `invoke_with_retry` 复用 | ✅ [adapter.py:47-51](backend/app/llm/adapter.py#L47-L51) — 现接 `LLMConfig.max_retries/max_total_time`（M1 修）|
| D2 Agent 零硬编码 | ⚠️ **部分**:7 处 caller 改为调 `app.llm.call_llm` module-level（内部已 delegate 到 Adapter），但 caller 仍 `safe_json_parse`；这是与 38 个旧测试 mock `app.agent.{intent,sql_graph,...}.call_llm` 兼容的折中。Agent 不再 import provider SDK / model compat logic —— 这一层 zero-hardcode 已达成 |
| D3 `LLM_*` settings + `MINIMAX_*` alias | ✅ [config.py](backend/app/llm/config.py) — `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_TIMEOUT / LLM_MAX_RETRIES / LLM_MAX_TOTAL_TIME / LLM_CONTEXT_WINDOW / LLM_TEMPERATURE`；`MINIMAX_*` 作为 alias |
| D4 `remaining_token_budget` 真传 | ✅ [confirmed_execution_graph.py:38](backend/app/agent/confirmed_execution_graph.py#L38) + [requirement_analysis_graph.py:42](backend/app/agent/requirement_analysis_graph.py#L42) — 抽 `_TYPICAL_CONTEXT_BUDGET_CHARS = 8000` 常量（L3 修），`max(0, context_window - est_chars//4)` 真传 |
| 双 `call_llm` 收敛（H2）| ✅ [__init__.py:68-74](backend/app/llm/__init__.py#L68-L74) 单 delegation；旧 `app/llm.py` 已删除 |
| trace 静默 → `logger.warning`（M3）| ✅ [adapter.py:72-73](backend/app/llm/adapter.py#L72-L73) |
| 5 处 fallback 内聚 Adapter | ⚠️ **部分**:Adapter 加了 `generate_structured_safe` helper ([adapter.py:107-145](backend/app/llm/adapter.py#L107-L145))；当前 caller 仍用简化 `call_llm + safe_json_parse` 而非该 helper（与 §D2 兼容旧测试 mock 折中） |

## 结论

剥 think 后 5 用例文本/JSON 等价；Adapter 接线不影响业务语义。H1（schema/pydantic 校验）、H2（双 `call_llm` 收敛）、M1（`invoke_with_retry` 接 LLMConfig）、M3（trace → `logger.warning`）、L3（`_TYPICAL_CONTEXT_BUDGET_CHARS` 抽常量）已修。D2 与 M2（5 处 fallback 内聚 Adapter）部分妥协：caller 仍自 `safe_json_parse`，但通过 module-level `call_llm` 间接走 Adapter（`call_llm` → `get_llm_adapter().generate`）；完整收口需待 P15 删除 `call_llm` deprecated alias 与 38 个旧测试 mock 一起迁移到 Adapter 边界（`monkeypatch.setattr("app.llm.adapter.LLMAdapter.generate", ...)`）。
