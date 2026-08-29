# P7 Golden Set Before/After 对比

> 状态: 已完成（结构等价 + 内容等价已钉）
> 上游: [2026-08-29-p7-prompt-refactor.md](2026-08-29-p7-prompt-refactor.md) §D6 + [2026-08-25-refactor-master-freeze.md](2026-08-25-refactor-master-freeze.md) §十五 P7 验收

## Context

P7 是「结构改造」不是「行为改造」（plan §Why this design）：
- 文案等价不动 → 指标不退化是底线
- 6 层分段 + Versioning → 后续 P8 Agent Loop / P10 Report Runtime / P14 Evaluation 改造时改 prompt 段定位精确，不误伤其他段

P7 落地前承诺："Before/After 指标应基本持平。任何显著退化（>5%）要回查 prompt 改写是否引入歧义。"

## Before / After 对比

### Before（P7 起点）
- 7 处裸 f-string prompt 散装在：
  - `intent.py:67` `prompt = f"..."`
  - `requirement_parser.py:36` `_PARSE_PROMPT = """..."""`
  - `sql_graph.py:167,311,442` 3 处 `prompt = f"..."`
  - `report_graph.py:61` `prompt = f"..."`
  - `memory/conversation.py:69` `prompt = f"..."`
- 7 处全部 **单段混合**（system/role/task/tool_policy/output_schema/safety 全揉在一段 f-string 里）
- **零** Versioning 元数据
- **零** 显式 Negative Instructions 段
- **零** 显式 Tool Policy 段
- dynamic context（assembled_context）混入主 prompt，靠 caller 在调用前 f-string 拼接

### After（P7 落地后）
- `app/agent/prompts/` + `app/memory/prompts/` 两个包，共 7 个模块
- 每个 prompt 由 **6 段 dict** + **META 5 字段** + **build 函数** 三件套
- 7 处 caller 全部切换到 build 函数（commit `4400cc5`）
- `app.infra.trace.sdk.Tracer.add_prompt_version(name, version)` 本地记录（P13 Langfuse 接入前可追踪钉子）
- dynamic context 注入（assembled_context / conversation_context）由 caller 在 build 输出末尾拼接，build 函数不感知

## 等价性论证

P7 NOT doing（plan §Explicitly NOT doing）明确写：
> 改 LLM Adapter / 改 Context Runtime / 改 Execution Agent Loop / 改 SQL Repair / 删现有 prompt 文案 / 改温度参数 / 引入 jinja2 等模板框架

**等价是 by construction**：
1. 每个 build 函数返回的 prompt 字符串与原裸 f-string 在「关键内容 marker」上完全一致
2. 没有引入新温度 / max_tokens / 模型参数
3. 没有引入新的 prompt 模板框架（仍 f-string + .format()）
4. retry feedback / context injection 等 caller 周边逻辑原样保留
5. Adapter 调用 `call_llm(prompt, max_tokens=...)` 不变

## 测试钉

`backend/tests/contracts/test_prompt_equivalence.py`（8 测试）钉住等价性：
- `test_prompt_preserves_key_markers[<prompt>]` × 6 — 每 prompt 含旧版关键 marker（决策规则 / 字段名 / 输出 JSON 形状）
- `test_intent_classify_preserved` — INTENT_CLASSIFY 含 "report / interface / chitchat / other" 4 类
- `test_build_functions_return_non_empty_strings` — 7 个 build 函数返回非空字符串

## 真端到端 Golden Set 对比（手动门，P12 范围内）

> P12 前保持手动门（CLAUDE.md §十五 红线）。本节为命令清单，不是当前阶段必跑项。

### 命令

```bash
# Before：当前 prompt 已迁移，git 可拿到 Before
cd /d/PyProject/ReportAgent
git show 4400cc5^:backend/app/agent/intent.py | grep -A 30 "prompt = f"

# After：跑 baseline_cases.json
cd /d/PyProject/ReportAgent/backend
REPORTAGENT_E2E=1 D:/miniConda/envs/agent/python.exe -m pytest tests/e2e/test_full_flow.py -s
# + evaluation/runner.py (P12 启动)
```

### 指标对照（P12 跑）

| 指标 | 期待结果（结构等价） |
|---|---|
| Requirement Accuracy | 与 P0 baseline 持平（误差 ≤ 5%） |
| Tool Selection Accuracy | 持平 |
| SQL Execution Success | 持平 |
| Repair Success | 持平 |
| Report Quality | 持平 |
| Latency P50 / P95 | 持平（prompt 字符数微增可忽略） |

### 退化判定

任何指标退化 >5% 要回查：
1. 是否 6 段切分时漏段 / 段顺序错？
2. 是否 build 函数漏传占位符参数（已发生过一次：plan_table_hints）？
3. 是否 caller 在 build 输出前后拼接顺序错？
4. 是否 Negative Instructions / Tool Policy 段措辞引起模型行为漂移？

排查路径在 [test_prompt_equivalence.py](../../backend/tests/contracts/test_prompt_equivalence.py) + [test_prompt_policies.py](../../backend/tests/contracts/test_prompt_policies.py) + [test_prompt_layering.py](../../backend/tests/contracts/test_prompt_layering.py) 三件套里。

## 当前 P7 阶段结论

- ✅ 结构等价：7 prompt 6 段齐全 + 顺序正确（test_prompt_layering 23 测试）
- ✅ 内容等价：旧版关键 marker 全保留（test_prompt_equivalence 8 测试）
- ✅ Negative Instructions 完备：基线 4 条 + Agent 专属（test_prompt_policies 43 测试）
- ✅ Tool Policy 显式：每个 prompt 的 tool_policy 段可执行 + SQL 三个 prompt 显式 search_schema 边界
- ✅ Versioning：每 prompt META 5 字段齐全 + 互斥（test_prompt_versioning 28 测试）
- ✅ Trace 可追踪：Tracer.add_prompt_version 本地记录 + P13 Langfuse 接入路径明确
- ⏸ 真端到端 Golden Set 跑：留 P12 手动门（CLAUDE.md §十五）

## Commit 序列

- `28b09e9` chore(p7): plan: p7-prompt-refactor + README 索引登记
- `7cbe3e9` feat(p7): T1 prompts/ 包骨架 + 7 模块 + 6 段结构 + trace sdk add_prompt_version
- `141fae4` feat(p7): T2/T3 6 段结构 + Versioning + Negative/Tool Policy 测试 + 安全策略补全
- `4400cc5` feat(p7): T4 7 处 caller 切换到新 prompt build 函数

回归基线：contracts+smoke+graphs **578 passed / 0 failed**。

## 后续 Phase 衔接

- **P8 Execution Agent Loop**：改 SQL Repair prompt 决策逻辑时，按 META.version 升到 V2；test_prompt_versioning 自动检测缺失字段
- **P9 Reliability**：RetryPolicy / 错误分类可加进 safety_policy 段，不动其他 5 段
- **P10 Report Runtime**：Report Prompt 扩 KPI / Chart spec 时，新增 output_schema 字段，task_contract 扩步骤
- **P11 Frontend/SSE**：SSE 事件描述 schema 引用 `META.output` 字段做契约校验
- **P13 Langfuse**：把 `Tracer._prompt_versions` 落库到 `observability.prompt_version` 表，按 trace_id JOIN
- **P14 Evaluation**：Baseline 对比按 `META.name` 切片，按 `META.version` 比对 V1 vs V2 指标