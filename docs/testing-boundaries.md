# 测试边界模型（Testing Boundaries）

> 状态: 进行中（2026-10-28 建立）
> 用途：先定义"系统对外的承诺边界"，再据此判断每条测试是否**证伪了一个边界上的错误假设**。
> 不在任何边界上的测试 = 边界外测试（可删/可合并），不再作为新增依据。

## 一、判据（一条测试为什么值得留）

> **如果这条测试变红，能说出「哪条生产承诺被破坏」吗？** 说不出 → 边界外。

反例（今天实际踩到的）：
- `assert "field_meaning:<字段名>" in prompt` —— 变红只说明改了文案，不说明任何承诺被破坏。
- `test_awaits_session_then_read_cm_in_order` —— 断言内部调用顺序，重写实现就红。

## 二、12 条边界（B1–B12）

| # | 边界 | 必须永远为真的一句话 | 用例数 | 状态 |
|---|---|---|---|---|
| B1 | **输入信任** | 用户输入永远被当作不可信数据：注入拦截、PII 脱敏、越权拒绝 | ~64 | ✅ 充分 |
| B2 | **数据出口** | LLM 生成的 SQL 只能读、只能读白名单表、有超时与行数上限 | ~38 | ✅ 强 |
| B3 | **需求契约** | 模糊输入 → 结构化 RequirementCard；缺失/假设必须显式表态才能执行 | ~36 | ⚠️ 有 2 个洞（见 §四） |
| B4 | **执行路由与预算** | 失败必须分类 → 决策 → 有限 repair，绝不盲重试、绝不无限循环 | ~57 | ✅ 最强 |
| B5 | **持久化** | 三态全部 append-only 落库，永不覆盖、永不伪造成功；版本并发不重号 | ~67 | ✅ 强 |
| B6 | **外部依赖** | MCP / embedding / LLM 不可用时显式降级或报错，不伪装成"没结果" | **~231 (26%)** | ⚠️ 过度 + 含死用例 |
| B7 | **恢复与生命周期** | 断连/超时/重启后任务有终态，不重复执行，不停在 generating | ~43 | ✅ 幂等已补（2026-10-28） |
| B8 | **上下文与记忆** | 召回有触发条件与档位隔离；candidate/过期/他人记忆不得进入上下文 | ~108 | ✅ 强（有金标指标） |
| B9 | **可观测** | 一次失败可被完整复现：span / llm / prompt / decision 四类都落 | ~58 | ⚠️ 缺成本 |
| B10 | **前端契约** | SSE → schema → reducer 单写者；三态渲染不伪造 | 308（前端） | ⚠️ 错误态有洞 |
| B11 | **报告真实性** | 报告里每个数值都能回溯到 QueryResult，编造即 FAILED | ~40 | ✅ 强 |
| B12 | **评估** | 今天比昨天更聪明能被量化（有金标 + 指标） | 78（evaluation） | ❌ 4/9 维度空转 |

## 三、边界外测试（2026-11-02 已执行：11 文件 246 → 72）

> **口径提醒**：本文件早期写的是**函数个数**，而 pytest 收集的是**用例数**（parametrize 展开后）。例如 `test_prompt_policies.py` 是 5 个函数 / **43** 个用例。以后只记用例数。
>
> **作废的结论**：早期版本说 `test_rag_schema.py` 有「2 条 shadowed 死用例（同名覆盖）」。AST 实测三个文件（mcp_client 74 / rag_schema 30 / schema_faq 20）**零重名**，该结论作废，不要再去找。

| 文件 | 原用例 | 现用例 | 处理 |
|---|---|---|---|
| `contracts/test_prompt_equivalence.py` | 8 | 1 | 7→1：钉 `output_schema` 键，并显式钉 DuckDB 否定极性 |
| `contracts/test_prompt_layering.py` | 22 | 3 | 15→1；删 7 条无消费者的段序断言；**新增** `KIND_MARKERS` guard |
| `contracts/test_prompt_policies.py` | 43 | 4 | 28→1（4 条基线 × 7 prompt 是同一段 boilerplate）；删 12 条恒真断言 |
| `contracts/test_prompt_repair_context.py` | 5 | 3 | 2 组 2→1；保留 dict 形态（checkpoint 反序列化后就是 dict） |
| `contracts/test_schema_grounding_alignment.py` | 16 | 4 | 12→1（同一断言 × 12 文件）；删负向文案钉 |
| `contracts/test_mcp_client.py` | 74 | 28 | 46 条合并：classify 14→3、transport 5→2、validate 17→6、cleanup 6→2、ModuleBoundary 2→0 |
| `smoke/test_rag_schema.py` | 30 | 12 | HTTP fallback 的 5 份重复合成 1 条（其中 3 份纯重复） |
| `smoke/test_schema_faq.py` | 20 | 10 | 工具 JSON 契约 4 次重复合成 1 条 |
| `contracts/test_state_blocks_contract.py` | 16 | 4 | 15→4；**补上缺失的 `pytestmark`**（此前 `-m contracts` 会 deselect 全部 16 条） |
| `contracts/test_tool_contract_14_fields.py` | 6 | 3 | 4→1；删恒真断言；**新增**「注册即校验」守卫 |
| `test_tool_descriptions.py` | 6 | 1 | 只留「菜单工具必须可被 `_run_step` 分发」 |
| **合计** | **246** | **72** | 净减 174，全套 1223 → 1050 收集 |

### 顺带修掉的「假覆盖」

| 用例 | 问题 | 处理 |
|---|---|---|
| `test_search_tables_from_rag_unavailable_returns_empty` | 名字说返回空，body 断言 fallback 成功 | 合并并按真实行为改名 `test_mcp_unavailable_falls_back_to_http` |
| `test_timeout_cancels_inflight_...` | 尾部建了 session 只 `sleep(0.05)`，零断言 | 并入 cancel+drain，删死尾巴与时间阈值 |
| `test_empty_result_does_not_retry` | 重言式：分类在 `_call_with_retry` 之外，还 monkeypatch 掉了唯一可回归的 `_do_call` | 删除 |

## 四、边界内但**没有覆盖**（这才是真正该补的）

| 边界 | 缺什么 | 该有的断言 |
|---|---|---|
| ~~B7~~ | ~~幂等 / 重复执行率~~ | ✅ **已补（2026-10-28）**：`test_idempotent_confirm.py` + `test_idempotency_guard_pg.py`；真链路实测 confirm#2 只回同一版本、版本表仍 1 行 |
| ~~B3~~ | ~~粒度不丢失~~ | ✅ **已补（2026-10-28）**：`RequirementCard.granularity` + `_apply_confirmed_granularity` 确定性改写 plan.dimensions；当月退化按年已被真链路验证 |
| ~~B3~~ | ~~alternatives 一键选~~ | ✅ **已补（2026-10-28）**：`field_meaning` 强制 alternatives，前端一键写回已接受口径 |
| B1 | **行级权限** | 只允许华东的用户查「各区域销售额」→ SQL 被注入 region 过滤且只返回华东；注入「忽略权限」仍被挡 |
| B9 | **成本** | 一次 confirm 的 `llm_call.cost` = Σ(token×价)，与 trace 对账（现在恒为 0） |
| B12 | **行为指标** | `clarification_rounds` / `param_completion_rate` / `recovery_success_rate` / `repeated_execution_rate` |
| B12 | **LLM-as-Judge** | 20 条金标问答 + 规则双校验（数值必须对，叙述由 judge 打分） |
| B10 | **错误会话恢复** | 快照恢复后 `phase=error` 必须能重建 `error` 并给出重试入口 |
| B10 | **非可恢复错误** | `recoverable=false` 时必须渲染错误卡片（`ErrorCard` 现在恒显「重试」，权限拒绝也会给重试按钮） |
| B5 | **`original_query` immutable** | 契约写在 `state-contract.md` §二，但**现役 v2 链路根本不携带该字段**（只有 legacy 图有 `original_query`/`current_query`）。要么补回 v2，要么把契约标注为 v1-only——不要为它写一条永远绿的测试 |

## 五、执行顺序（重要）

1. **先补 §四 的空边界**（尤其 B7 幂等、B1 行级权限）——这是"生产级证据"，比删测试有价值。
2. **再删 §三 的边界外测试**——收益是维护成本（改文案不再误红），不是速度。
3. **速度靠分层**：日常 `-k` / `--lf`；改契约 `-m contracts`；提交前全量（38s）；`evaluation.runner` 只在演示前跑（10 分钟，真 LLM）。

## 六、现状数据（2026-11-02 实测）

```
backend  1050 用例 / 全量 60s（1049 passed / 1 skipped）
  ├─ contracts   309
  ├─ smoke       285
  ├─ graphs      110
  ├─ persistence  87  ← 真 PG 往返，时间大头
  ├─ api          30
  └─ 其余 marker 重叠 / 未标
frontend 48 文件 / 317 用例 / 14s
evaluation 80 passed / 25 skipped（skipped 全是 env-gated 真后端用例）
```

变化轨迹：2026-10-28 实测 896 →（主线修复 + 幂等 + 边界补齐）1223 →（2026-11-02 边界外收敛）**1050**。

**结论：时间是 persistence（真库）吃的，不是用例数。删 174 条只省个位数秒。**
