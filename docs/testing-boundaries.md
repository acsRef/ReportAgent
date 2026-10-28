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

## 三、边界外测试（可删/合并，约 150–200 个）

### 3.1 结构性钉（变红只说明改了文案）

| 文件 | 用例 | 处理 |
|---|---|---|
| `contracts/test_prompt_equivalence.py` | 3 | 删（marker 逐条断言） |
| `contracts/test_prompt_layering.py` | 4 | 合并为 1（六段齐全即可） |
| `contracts/test_prompt_policies.py` | 5 | 合并为 1 |
| `contracts/test_prompt_repair_context.py` | 5 | 合并为 1 |
| `contracts/test_state_blocks_contract.py` | 16 | 合并为 3（round-trip 即可，不逐行钉映射表） |
| `contracts/test_schema_grounding_alignment.py` | 5 | 删（断言 prompt 含某表名） |
| `contracts/test_tool_contract_14_fields.py` | 6 | 合并为 2（字段齐全 + 一处四问，不逐 tool 断言中文子串） |
| `tests/test_tool_descriptions.py` | 6 | 同上，与上一条合并 |

### 3.2 内部实现细节（重写实现即红，不对应承诺）

| 文件 | 用例 | 处理 |
|---|---|---|
| `contracts/test_mcp_client.py` | 74 | → 25：删 reset 顺序 3 条、`classify_*` 同义 4 条、`_extract_text` 5 条合并为 2、retry 组合去重 |
| `smoke/test_rag_schema.py` | 30 | → 12：删 2 条 shadowed 死用例（同名覆盖），MCP/fallback 双份合并 |
| `smoke/test_schema_faq.py` | 20 | → 10：与 rag_schema 重叠路径合并 |

## 四、边界内但**没有覆盖**（这才是真正该补的）

| 边界 | 缺什么 | 该有的断言 |
|---|---|---|
| ~~B7~~ | ~~幂等 / 重复执行率~~ | ✅ **已补（2026-10-28）**：`test_idempotent_confirm.py` + `test_idempotency_guard_pg.py`；真链路实测 confirm#2 只回同一版本、版本表仍 1 行 |
| B1 | **行级权限** | 只允许华东的用户查"各区域销售额" → SQL 被注入 region 过滤且只返回华东；注入"忽略权限"仍被挡 |
| B9 | **成本** | 一次 confirm 的 `llm_call.cost` = Σ(token×价)，与 trace 对账（现在恒为 0） |
| B12 | **行为指标** | `clarification_rounds` / `param_completion_rate` / `recovery_success_rate` / `repeated_execution_rate` |
| B12 | **LLM-as-Judge** | 20 条金标问答 + 规则双校验（数值必须对，叙述由 judge 打分） |
| B3 | **粒度不丢失** | 「每个月的趋势」→ 生成 SQL 的 GROUP BY 必须含 month（今天实测退化成按年，1 行） |
| B3 | **alternatives 一键选** | 选中候选 → `accepted=true` + 口径进入已接受假设 text → SQL prompt 可见 |
| B10 | **错误会话恢复** | 快照恢复后 `phase=error` 必须能重建 `error` 并给出重试入口 |
| B10 | **非可恢复错误** | `recoverable=false` 时必须渲染错误卡片（现在只有 if，无 else） |

## 五、执行顺序（重要）

1. **先补 §四 的空边界**（尤其 B7 幂等、B1 行级权限）——这是"生产级证据"，比删测试有价值。
2. **再删 §三 的边界外测试**——收益是维护成本（改文案不再误红），不是速度。
3. **速度靠分层**：日常 `-k` / `--lf`；改契约 `-m contracts`；提交前全量（38s）；`evaluation.runner` 只在演示前跑（10 分钟，真 LLM）。

## 六、现状数据（2026-10-28 实测）

```
backend  131 文件 / 896 用例 / 全量 37.7s
  ├─ smoke 10 文件 104 用例 / 10.2s（其中 ~8s 是 import 固定成本）
  ├─ contracts 61 文件 495 用例 / 9.7s
  ├─ graphs 17 文件 93 用例 / 12.0s
  ├─ persistence 19 文件 86 用例 / 25.6s  ← 真 PG 往返，时间大头
  └─ api 4 文件 27 用例 / 9.2s
frontend 47 文件 / 308 用例 / 14s
evaluation 78 passed / 25 skipped
```

**结论：时间是 persistence（真库）吃的，不是用例数。删一半单测只省 ~10s。**
