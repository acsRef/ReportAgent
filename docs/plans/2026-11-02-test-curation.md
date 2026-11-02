# 测试收敛：删掉边界外用例，保留「变红即承诺破了」的

> 状态: 已完成

## Context

`docs/testing-boundaries.md` §一 给的判据只有一条：**这条测试变红，能不能说出「哪条生产承诺被破坏」？** 说不出 → 边界外（文案钉子 / 内部实现细节 / 同义重复）。

2026-11-02 复核发现 §三 的清单已经过期：

- 文档写的用例数大多对不上实际情况——`test_prompt_policies.py` 实际 **43**（文档写 5）、`test_prompt_layering.py` 实际 **22**（文档写 4）、`test_schema_grounding_alignment.py` 实际 **16**（文档写 5），因为大量 `parametrize` 没被算进去。
- 文档说 `test_rag_schema.py` 有「2 条 shadowed 死用例（同名覆盖）」——**核实为假**：该文件 13 个 `def test_*` 无一重名，30 个用例全部来自参数化。`test_schema_faq.py`、`test_mcp_client.py` 同样无重名。
- 后端总量从文档的 896 涨到 **1223**，§六 的分布数字全部失效。

所以本次不是照抄清单删，而是**逐条重判**，把结论写回文档。

## Design

### 判定三分类

| 类 | 特征 | 处理 |
|---|---|---|
| **文案钉** | 断言 prompt / 描述里含某个中文子串、某个字段顺序、某段模板存在 | 删；真要防回退，用「六段齐全」这类结构性 1 条概括 |
| **内部实现** | 断言私有函数调用顺序、内部 helper 的分支细节、同一映射表被反复验 | 删或合并到 1~2 条代表性断言 |
| **同义重复** | 两个文件验同一件事（如 tool 字段齐全 vs tool 描述齐全） | 合并成 1 个文件 1~2 条 |

### 安全栏杆（必须遵守）

1. **不删 B1/B2/B4/B5/B7/B11 的断言**：安全闸、SQL 只读与白名单、失败分类与预算、三态落库、幂等、报告数值回溯——这些变红都对应真实承诺。
2. **删前删后各跑一次全量**，对比：`1223 → N`，且**失败数必须仍为 0**；被删文件不得带走别处的 fixture 依赖。
3. **每次删除只动一个文件**，删完立刻跑该 marker（`-m contracts` / `-m smoke`），确认没连带炸别的测试。
4. 前端不在本次范围（317 个用例对应的都是 reducer / SSE / 渲染承诺，暂无成规模的边界外集合）。

### 合并怎么写

被合并的用例不留空壳：写一条「结构齐全」的用例，断言的是**契约形状**（例如「14 个 Metadata 字段全部存在且非空」「六段 prompt 段段齐全」），不是逐个数中文子串。

## Files to change

> 本轮只做删除（用户拍板：守卫与文档另开一轮）。下表「已落地」列为实际执行结果。

| 文件 | 现用例 | 建议留 | 删 | 合并 | 已落地 |
|---|---|---|---|---|---|
| `contracts/test_prompt_equivalence.py` | 8 | 1 | 0 | 7→1 | ✅ 8→1 |
| `contracts/test_prompt_layering.py` | 22 | 3 | 7 | 15→1 | ✅ 22→3（含新增 `KIND_MARKERS` guard） |
| `contracts/test_prompt_policies.py` | 43 | 4 | 12 | 28→1 | ✅ 43→4 |
| `contracts/test_prompt_repair_context.py` | 5 | 3 | 0 | 2 组 2→1 | ✅ 5→3 |
| `contracts/test_schema_grounding_alignment.py` | 16 | 4 | 0 | 12→1 + 2→1 | ✅ 16→4 |
| `contracts/test_mcp_client.py` | 74 | 28 | — | 46 条合并 | ✅ 74→28 |
| `smoke/test_rag_schema.py` | 30 | 12 | — | 18 条合并 | ✅ 30→12 |
| `smoke/test_schema_faq.py` | 20 | 10 | — | 10 条合并 | ✅ 20→10 |
| `contracts/test_state_blocks_contract.py` | 16 | 4 | 1 | 15→4 | ✅ 16→4（并补 `pytestmark`） |
| `contracts/test_tool_contract_14_fields.py` | 6 | 2 | 1 | 4→1 | ✅ 6→2 |
| `test_tool_descriptions.py` | 6 | 1 | 5 | — | ✅ 6→1 |

### 三个「看着有覆盖、实际没覆盖」的坏用例（删的同时要修）

| 文件 | 用例 | 问题 |
|---|---|---|
| `smoke/test_rag_schema.py` | `test_search_tables_from_rag_unavailable_returns_empty` | 名不副实：名字说返回空，body 断言 fallback 成功 `len(rows)==1` |
| `contracts/test_mcp_client.py` | `test_timeout_cancels_inflight_...` | 尾部死断言：L414–422 建了 `new_session` 只 `sleep(0.05)`，零断言 |
| `contracts/test_mcp_client.py` | `test_empty_result_does_not_retry` | 重言式：分类在 `_call_with_retry` 之外，且 monkeypatch 掉了唯一可能回归的 `_do_call` |

### 分类结论要点

- **equivalence 有一条危险**：钉住的 `DuckDB` / `EXTRACT` 在 prompt 里是**否定句**（"不用 DuckDB 专属语法"），测试只钉词不钉极性——prompt 改成肯定句照样绿。合并后的用例要断语义而不是子串。
- **layering 要新增 1 条**：`KIND_MARKERS`（`app/llm/mock.py` 用 6 条固定首句给 Playwright Contract E2E 分类）与真实 prompt 之间**没有任何测试链**——`test_mock_llm_adapter.py` 只是复制了一份字面量。改 prompt 首句 → mock 静默错位、PR 门禁全绿、只有 nightly E2E 炸。实测 6 条当前全部匹配，属缺 guard 而非现存缺陷。
- **registry 抄了三份**：layering / policies / equivalence 各自维护 7 个 prompt 常量，合并后抽 `tests/contracts/_prompt_registry.py`，否则加第 8 个 prompt 会漏测一处。

## Reused existing utilities

- `docs/testing-boundaries.md` 的 12 条边界与判据——本次只补数据，不改模型
- 既有 marker 体系（`contracts` / `smoke` / `graphs` / `persistence` / `api`）——按 marker 分批删、分批验

## Verification

```bash
cd backend
python -m pytest -q                     # 删前基线：1223 passed / 1 skipped
# 每删一个文件后
python -m pytest -m <该文件的 marker> -q
# 全部删完后
python -m pytest -q                     # 期望：N passed / 1 skipped / 0 failed
```

同时更新 `docs/testing-boundaries.md` §六 的现状数据，并把 §三 改成「已完成项 + 剩余项」。

## Explicitly NOT doing

- 不动前端 317 个用例
- 不动 `persistence/` 真 PG 用例（时间大头但都是真承诺）
- 不为减数量删掉「看起来啰嗦但其实在钉失败分类」的用例
- 不新建测试框架/夹具体系

## 落地记录（2026-11-02，第 1 轮：只删测试）

**结果：11 个文件 246 → 72（净减 174），全套 1223 → 1049（`1048 passed / 1 skipped`，0 failed）。**
用户拍板本轮只做删除，守卫与文档留到下一轮。

| 文件 | 现 | 新 |
|---|---|---|
| `contracts/test_prompt_equivalence.py` | 8 | 1 |
| `contracts/test_prompt_layering.py` | 22 | 3（+ `KIND_MARKERS` guard） |
| `contracts/test_prompt_policies.py` | 43 | 4 |
| `contracts/test_prompt_repair_context.py` | 5 | 3 |
| `contracts/test_schema_grounding_alignment.py` | 16 | 4 |
| `contracts/test_mcp_client.py` | 74 | 28 |
| `smoke/test_rag_schema.py` | 30 | 12 |
| `smoke/test_schema_faq.py` | 20 | 10 |
| `contracts/test_state_blocks_contract.py` | 16 | 4 |
| `contracts/test_tool_contract_14_fields.py` | 6 | 2 |
| `test_tool_descriptions.py` | 6 | 1 |

### 三个「假覆盖」用例的处理

- `test_search_tables_from_rag_unavailable_returns_empty` → 合并并**按真实行为改名** `test_mcp_unavailable_falls_back_to_http`（原名说返回空，body 断言的却是 fallback 成功）。
- `test_timeout_cancels_inflight_...` → 合并进 `test_timeout_cancels_and_drains_before_retry`，删掉尾部零断言的 `sleep` 与 `elapsed>=0.8*...` 时间阈值（flake 源）。
- `test_empty_result_does_not_retry` → **删除**（重言式：分类发生在 `_call_with_retry` 之外，且 monkeypatch 掉了唯一能回归的 `_do_call`）。

### 顺带修掉的真缺陷

- `test_state_blocks_contract.py` 缺 `pytestmark` → 此前 `-m contracts` 把它 16 条全部 deselect，按 marker 分层跑时等于不存在。已补，实测 `-m contracts` 能收集到。

### 本轮删掉后「无人守」的断言（下一轮补）

1. `registry.validate()` 集成自校验零覆盖（本就无生产 caller）。
2. `_format_tools_for_prompt(whitelist=...)` 分支零覆盖（无生产 caller）。
3. 逐个工具的「输入/输出/不要用来」per-tool 边界只剩全局四问。
4. `mcp_client` 不得反向 import `mcp_faq_client` 的 AST 断言（`ModuleBoundary` 2 条）删除后无替代；实际反向 import 会循环 import 崩，风险低。
5. `original_query` immutable 仍无任何测试（`state-contract.md` §二 明写）。

### 已知待同步的文档（下一轮）

- `docs/testing-boundaries.md` §三 的数字口径（函数个数 vs 用例数）、§六 现状数据全部失效；§3.2「shadowed 死用例」前提不成立（AST 实测零重名）。
- `tests/contracts/test_mcp_contract_schema.py` docstring 写「14 例」，实际 6 例。
- `AGENTS.md` / `CLAUDE.md` 仍写 `backend/pytest.ini`，实际配置在仓库根 `pytest.ini`。

## 落地记录（2026-11-02，第 2 轮：守卫 + 文档）

### 守卫：把「只在测试里成立」的承诺挪到运行时

| 项 | 改前 | 改后 |
|---|---|---|
| `registry.validate()` | 无任何生产 caller，删测试后即零守卫 | `register_all_tools()` 末尾校验，失败抛 `ToolContractError`（启动/构图即失败）。实测当前 12 个工具 0 错误 |
| `_format_tools_for_prompt(whitelist=…)` | 死参数（两处 caller 都不传），只为缓存多开一个 key | 删掉参数，缓存简化为单个字符串；对应测试已在上轮删除 |
| `original_query` immutable | 文档写「一经写入不得修改」，但**现役 v2 主链根本不携带该字段**（只有 legacy 图有） | 不写假测试；在 `state-contract.md` §二 标注为 legacy-only，并说明要补就补字段、别补「永远绿」的测试 |

新增测试 1 条：`test_register_all_tools_fails_fast_on_incomplete_metadata`（monkeypatch `validate` 返回错误 → 断言抛 `ToolContractError`），钉住「注册即校验」这条接线本身。

### 文档：4 处会误导人的失真

| 文件 | 失真 | 修法 |
|---|---|---|
| `docs/testing-boundaries.md` | §三 用函数个数当用例数、§3.2 的 shadowed 死用例结论不成立、§六 现状数据全部过期 | §三 改成实测表（246→72）+ 作废声明；§四 标掉已补项、补上 B5 那条；§六 换成 1050 用例的实测分布 |
| `CLAUDE.md` | 写配置在 `backend/pytest.ini`，实际在仓库根 | 改成仓库根 `pytest.ini`，并说明为什么不能挪（挪了 `asyncio_mode=auto` 失效，async 测试全部不被识别） |
| `docs/hand-off.md` | 停在 2026-07-24：「31/31 通过」、分支与工作树状态、待办全过期 | 重写为索引式 hand-off（30 秒状态 + 怎么跑 + 已知缺口 + 文档地图），数字只指向命令 |
| `tests/contracts/test_mcp_contract_schema.py` | docstring 写 `TestValidateMatchesContract —— 14 例` | 改为 6 例并注明收敛日期 |

### 第 2 轮回归

- backend **1050 收集 / 1049 passed / 1 skipped / 0 failed**
- evaluation `80 passed / 25 skipped`（未改动）
- 前端未改动

## 最终状态

两轮合计：**246 → 72 个用例（−174）**，全套 **1223 → 1050**；新增 3 个真守卫（`KIND_MARKERS` 对齐、注册即校验、prompt 输出键≠文案子串），修掉 3 个假覆盖用例，4 份文档改到与现实一致。
