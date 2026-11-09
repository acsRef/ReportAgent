> 状态: 已完成

# 记忆系统一致性测试（契约 §一–§七 逐条落地）+ 第一处调整

## Context

上一轮（`2026-11-09-memory-recall-scoreboard`）只回答了「**召回准不准、稳不稳**」。用户随即指出：
「我不是设计了一个记忆系统吗，那个记忆系统也是要着重参与测试的，不行的话还要调整的」。

也就是说要审的不是检索指标，而是**记忆系统本身的设计是否被实现兑现**：
`docs/architecture/memory-architecture.md` §一–§七 写的是契约（四类记忆 / 召回触发 / 档位 /
写入时机 / V1 简化 / Lifecycle / Conflict Priority），现在要把**每一条变成可执行的断言**，
并且当场把「契约写了、代码没做」的地方抓出来。

先做静态排查（读代码定位 caller），已确认三处「契约与实现不一致」，其中一处是**整条记忆类型在生产里是死的**：

| 编号 | 契约 | 实现现状 | 证据 |
|---|---|---|---|
| **D1** | §一「Query Memory = 历史成功查询经验」+ §四「SQL Validation SUCCESS AND Execution SUCCESS 之后写入」 | **v2 主链完全不写 Query Memory**——`remember_query` 唯一 caller 在 `backend/app/legacy/agents/parent_graph.py`（已归置的旧链路），`confirmed_execution_graph` 里零写入。生产环境里 `memory.query_template` 只有「模板中心」和测试种子会产生行 | `grep -rn "remember_query" backend/app` 只有 legacy 一处 |
| **D2** | §四「失败 SQL 只记录 `failure_category / failed_sql / retry_count / error`（沿用 `QueryMemory.record_failure()`）」 | `memory.query_template` **没有这些列**（DDL 只有 success/failure_count），`record_failure(entry_id)` 只对**已存在的成功行** +1 | `backend/scripts/init_pg.sql` DDL；`query_memory.py:127` |
| **D3** | §六「新旧偏好冲突时旧 → superseded，避免多个 active 互相矛盾」 | 只做**同内容** supersede：`supersede_stable_preferences(user_id, content)` 的 WHERE 是 `content=$2`。用户先说「以后都用柱状图」再说「以后都用饼图」→ **两条 active 并存**，召回时给出互相矛盾的偏好 | `user_memory.py:239-256` |
| **D4** | §五「temporary_preference 绑定 session_id」 | 召回过滤只有 `status='active' AND (expires_at IS NULL OR expires_at > NOW())`，**不过滤 session** → A 会话的临时偏好在 B 会话照样召回 | `user_memory.py:19` `_ACTIVE_RECALL_FILTER` |

## Design

### D5 · 一个文件、一张矩阵，按契约条款编号

新增 `backend/tests/persistence/test_memory_system_conformance.py`，用例名带条款号
（`test_c1_...`），每条只断言**契约原文**，不夹带实现细节。已有覆盖不重复造，矩阵里标「沿用」。

| ID | 条款 | 断言 | 本轮 |
|---|---|---|---|
| C1 | §四 | SUCCESS（非 adjust）→ `memory.query_template` 有行；EMPTY / FAILED / adjust → 无行 | **新增 + 修 D1** |
| C2 | §四 | 失败轮不把 SQL 写进成功记忆 | 新增（现在因为 D1 压根不写，顺便钉住） |
| C3 | §六 | 同内容重申 → 仍只有 1 条 active（不产生双 active） | 新增 |
| C4 | §六 | 同一维度不同取值（柱状图 → 饼图）→ 旧 active 变 superseded | **`xfail(strict)` 记录 D3** |
| C5 | §五 | `scope=session` 的条目只在原会话召回 | **`xfail(strict)` 记录 D4** |
| C6 | §五 | `expires_at` 过期不召回 | 沿用（guardrails ⑥） |
| C7 | §五 | LLM inferred → candidate，不召回 | 沿用（write pipeline 契约） |
| C8 | §六 | candidate / superseded / expired 均不可召回 | 沿用（lifecycle 契约 + guardrails） |
| C9 | §一 | 同一 (sql,user) 二次成功 → `success_count` 累加、不新增行 | 新增 |
| C10 | §二 | 纯闲聊 / 完整 query 不触发长期召回 | 沿用（selective recall 契约） |
| C11 | §三 | Execution 档 drop preference、Report 档不吃 query | 沿用（guardrails / runtime 契约） |
| C12 | §七 | Schema > Memory：记忆不得改写 schema 字段 | 沿用（query guidance「参考帧 only」） |

### D6 · D1 的修法：在唯一知道 verdict 的地方写，且只认 SUCCESS

`_persist_report` 是 v2 主链唯一同时掌握 `execution_status` + `sql` + `user_query` 的节点，
写入就挂在这里。两个决定：

1. **只认 SUCCESS**：EMPTY 虽然「执行成功」但零行，把它当经验召回会持续推荐无数据的口径；
   FAILED 的 SQL 是陷阱不是经验。语义上比 §四 的 "SUCCESS AND SUCCESS" 更严格一点，写进注释。
2. **adjust 轮不写**：adjust 的 `user_query` 是「按产品维度再看」这类补充语，单独存成一条
   question 只会污染 Query Memory（下次用「按产品维度再看」检索）。
3. **best-effort**：写记忆抛错只 warn，绝不影响已经落库的报告（与 `remember_explicit_preference`
   的 best-effort 一致）。

为了让「要不要写」这个**决策**可测（而不是只能靠通读 `_persist_report`），抽成纯函数
`_should_writeback_query_memory(verdict, is_adjust)`，测试直接打它；真正的写入函数
`_remember_successful_query(state)` 用真 PG 测。

### D7 · D3 / D4 第一轮只取证，第二轮（用户拍板「三个一起做」）已实施

两处都需要**设计决策**，第一轮不该由我替你拍，所以先钉成 `xfail(strict=True)`——
跑测试就能看到「这里契约没兑现」，修好会变 XPASS 并**因为 strict 而失败**，强制回来改矩阵，
比写进文档可靠。第二轮决定并实施：

- **D3**：新增 `app/memory/preferences.py`，用确定性词表给出维度（`chart` 呈现形态 /
  `granularity` 时间粒度），只 supersede **同维度且不同正文**的旧条目；跨维度不互相顶掉。
  维度判定留在 domain（`app/memory/`），infra 只提供「按正文清单置 superseded」的执行方法，
  不反向依赖 domain。
- **D4**：召回签名加 `session_id`（`UserMemory.search` / `get_user_preferences` →
  `MemoryManager.recall_structured` → `app/memory/semantic` → `ContextRuntime.build`）；
  **没有 session 上下文时 session 级一律不召回**（离线评测 / 模板中心的安全默认）。

## Files to change

| 文件 | 改动 |
|---|---|
| `backend/app/agent/confirmed_execution_graph.py` | `_should_writeback_query_memory` / `_remember_successful_query`（D1，SUCCESS 分支）+ `_remember_failed_query`（D2，FAILED 分支） |
| `backend/app/infra/memory/user_memory.py` | `_scope_filter` + `search`/`get_user_preferences` 的 `session_id`（D4）；`list_active_preference_contents` / `supersede_active_by_contents`（D3 执行侧） |
| `backend/app/memory/preferences.py` | **新增**：确定性偏好维度词表 + `conflicting_contents`（D3 判定侧，domain） |
| `backend/app/memory/manager.py` | `remember_explicit_preference` 里接同维度 supersede（D3） |
| `backend/app/memory/semantic.py` / `backend/app/context/runtime.py` | `session_id` 透传（D4） |
| `backend/app/infra/memory/query_memory.py` / `memory_manager.py` | `record_failure_detail` / `remember_query_failure`（D2） |
| `backend/scripts/init_pg.sql` | 新表 `memory.query_failure` + 索引（D2） |
| `backend/tests/persistence/test_memory_system_conformance.py` | C1–C6、C9 可执行断言（真 PG + fake embedder） |
| `backend/tests/contracts/test_structured_recall_contract.py` 等 3 个契约测试 | fake 签名补 `session_id=None`（接口变了就改测试） |
| `docs/plans/README.md` | 登记本 plan |

## Reused existing utilities

- `MemoryManager.remember_query` / `recall_structured`（`backend/app/infra/memory/memory_manager.py`）：写入/召回既有网关，不新开 DB 访问路径（宪法 §6）。
- `MemoryPolicy.extract_preference` + `remember_explicit_preference`（`backend/app/memory/manager.py`）：explicit 写入口，C3 直接打它，测的就是生产同一函数。
- `_ACTIVE_RECALL_FILTER`（`user_memory.py`）：召回过滤唯一真源，C5/C6 的断言观察点。
- 既有 persistence 测试的夹具范式（`_make_user` / `_cleanup` / `_FakeEmbedder` / `_run`）：照抄，不自创。
- `user_memory.supersede_stable_preferences`：C3/C4 观察它的效果，不改它。

## Verification

```bash
D:/miniConda/envs/agent/python.exe -m pytest backend/tests/persistence/test_memory_system_conformance.py -q
# 既有记忆套件不回归：
D:/miniConda/envs/agent/python.exe -m pytest backend/tests/persistence -k memory -q
D:/miniConda/envs/agent/python.exe -m pytest backend/tests/contracts -k memory -q
```

验收：
1. C1/C2/C3/C9 绿（D1 已修）；C4/C5 显示 `xfail`（已知缺口被钉住）。
2. 真链路：报告生成成功后 `memory.query_template` 多一行（同一 question 重跑则 `success_count` 累加）。
3. 既有记忆测试零回归。

### 第二轮（同日）：D2 / D3 / D4 一起落地，矩阵 10/10 转绿

用户拍板「三个一起做」。三处都改了，且都留了可执行断言：

| 缺口 | 改法 | 证据 |
|---|---|---|
| **D4** session 级记忆跨会话泄漏 | `UserMemory.search` / `get_user_preferences` 加 `session_id` 参数 + `_scope_filter()`：`(scope <> 'session' OR ($n::text IS NOT NULL AND session_id = $n::text))`；`MemoryManager.recall_structured` → `app/memory/semantic.recall_structured` → `ContextRuntime.build` 一路透传。**没有 session 上下文时 session 级一律不召回**（离线评测/模板中心的安全默认） | C5 三个断言：原会话召回 ✅ / 别的会话不召回 ✅ / 无 session 不召回 ✅ |
| **D3** 同维度偏好不 supersede | 新增 `app/memory/preferences.py`：确定性词表给出「维度」（`chart` 呈现形态 / `granularity` 时间粒度），`conflicting_contents()` 只淘汰**同维度且不同正文**的旧条目；infra 侧只加两个执行方法（`list_active_preference_contents` / `supersede_active_by_contents`），**维度判定留在 domain**，infra 不反向依赖 | C4 两条：柱状图→饼图只留 1 条 active ✅ / 粒度 vs 呈现跨维度都保留 ✅ |
| **D2** 失败明细无处可落 | 新表 `memory.query_failure`（`failure_category / failed_sql / error_message / retry_count / target_metric / session_id / trace_id`），`MemoryManager.remember_query_failure` 网关 + `_remember_failed_query(state)` 在 FAILED 分支调用 | C6：真 PG 行断言 + 「失败不写进 success 表」；**真链路** e2e 05 跑完落 1 行（`failure_category='syntax'`，trace_id 与 trace 对得上） |

真链路观察（诚实记账）：那条 FAILED 用例的 payload 是 `{"kind": "syntax", "message": "无SQL语句"}`，
所以 `failed_sql` 为 NULL 是**如实**（真的没有 SQL 可说），不是没接线。
另外 `retry_count` 现在取 `state["sql_repair_count"]`——该键还没在 `ConfirmedExecutionState` 声明
（子图 `retry_counters` 没透传），生产里多为 0；等状态契约补字段后自然生效，已在测试注释里写明。

执行结果：`test_memory_system_conformance.py` **10 passed**（原来 6 passed + 2 xfailed，两个 xfail 转 PASS 并
新增了跨维度反例、confirm 流程、失败明细三条）。
回归：`pytest backend/tests/persistence` **91 passed**；
`pytest backend/tests -k "memory or recall or context or preference"` **229 passed**
（顺带把三个契约测试里的 fake `search` / `recall_structured` 签名补上 `session_id=None`——
接口变了就该改测试，不是给新参数开洞）。

### 一处顺带修正的认知

用户提到「记忆机制参考了 LRU / LFU」——确认属实，代码里的位置与权重是：

| 场景 | 公式 | 位置 |
|---|---|---|
| 召回打分 | `0.6×语义相似度 + 0.2×importance + 0.1×LFU + 0.1×LRU` | `user_memory._compute_score` |
| 淘汰打分 | `LFU×0.4 + LRU×0.4 + importance×0.2`（不含语义，淘汰时无查询） | `user_memory.evict_over_capacity` |
| Query Memory 召回 | `0.5×语义 + 0.3×成功率 + 0.1×LFU + 0.1×LRU` | `query_memory.search_similar` |

两点必须说清（都会影响怎么读评测数字）：
1. **LFU/LRU 是「行级」计数**：`access_count` / `last_access_time` 每次召回（`record_access`）就改写，
   所以它们是**读放大**信号，不是「用户喜欢什么」的信号——同一条记忆被系统自己召回一次就变「热」。
2. **召回打分因此不是静态的**：上一轮 gold scoreboard 里 `_reset_access` 每轮清零，
   所以公布的 `Recall@1 0.84 / @3 0.96` 是「**LRU/LFU 未热**」读数（纯语义+重要性）；
   真实用户连续提问时，第 3 位之后会被历史访问改写——这也是我上一轮看到「尾部集合不可复现」的机制来源之一。

## 落地记录（2026-11-09）

### 铁证：`memory.query_template` 在这台开发库里一直是 0 行

改之前直接查库：`SELECT count(*) FROM memory.query_template` → **0**。
这台库被真机用了几个月、跑过无数次报告，说明「历史查询经验」这类记忆**从来没有被生产过**
——不是「召回不好」，是**根本没有东西可召回**。D1 由此从「读代码推断」升级为「实测确认」。

### D1 已修，并且真链路复验通过

修法：`_persist_report` 里按 verdict 决定是否写（纯函数 `_should_writeback_query_memory` 可测），
best-effort、报告先落库再写记忆。真链路复验（e2e `01-happy-path`，mock LLM + 真 PG）：

```
before: SELECT count(*) FROM memory.query_template → 0
run:    npx playwright test specs/01-happy-path.spec.ts → 1 passed
after:  1 行  (user 1, question='2024年各区域销售额排名',
               target_metric='销售额', sql='SELECT s.region AS 区域, SUM(o.order_amount)…')
```

（验证完把这条 mock 造出来的行删掉了，不拿 fixture 脏用户的记忆库。）

**过程中真链路又抓到一个 bug（只有跑真链路才会发现）**：`/confirm` 流的 `state["user_query"]`
是**空串**（问题在需求卡里），写回逻辑只读它就一条都写不进去——第一次修完 e2e 跑完仍是 0 行。
现在退回需求卡摘要作为「问题原文」，并补了 `test_c2_confirm_flow_uses_card_summary_when_user_query_empty`
钉住。这条正好说明：**单元测试绿 ≠ 链路通**。

### 矩阵执行结果

任务单文件 `backend/tests/persistence/test_memory_system_conformance.py`：**6 passed / 2 xfailed**
（真 PG + fake embedder，9.7s）：

| ID | 结果 |
|---|---|
| C1 写入门槛（只剩 SUCCESS + 非 adjust） | PASS（predicate 4 组取值） |
| C1' 成功一轮 → 真写一行 | PASS |
| C2 空 SQL / 空问题 → 不写 | PASS |
| C2' confirm 流用卡摘要补问题原文 | PASS（新发现的回归钉） |
| C9 同 SQL 二次 → success_count 累加、不新增行 | PASS |
| C3 同内容重申 → 只有 1 条 active | PASS |
| C4 同维度不同取值 → 旧 superseded | **XFAIL（D3 缺口确认）** |
| C5 session 级记忆不跨会话 | **XFAIL（D4 缺口确认）** |

`xfail(strict=True)` 的意思是：**修好那天这两条会变 XPASS 并让测试失败**，逼着回来把矩阵改成 PASS，
而不是在文档里慢慢腐烂。

回归：`pytest backend/tests -k "confirmed_execution or persist_report or writeback or memory"`
→ **101 passed / 2 xfailed**（我改的 `_persist_report` 是主链节点，必须跑这一圈）。

### 剩下的三处缺口（都是设计决策，等你拍）

| 缺口 | 需要你决定的点 |
|---|---|
| **D2** 失败明细无列可落（`failure_category / failed_sql / retry_count / error`） | 要不要动 `memory.query_template` 的 DDL（加列 + 软迁移）？还是另开一张 `memory.query_failure` 表？前者省事但表越来越宽，后者干净但要新写一套仓储 + 模板中心也要能看 |
| **D3** 同维度偏好不 supersede（柱状图 → 饼图两条 active） | 「偏好维度」怎么定义：词表（图表类型 / 粒度 / 语气…）还是让模型抽取时带 `dimension` 字段？前者现在就能做但脆，后者要改写入契约 |
| **D4** session 级记忆跨会话泄漏 | 没有 session 上下文的场景（离线评测、模板中心）里，session 级记忆是否**一律不召回**？定了之后我改 `search`/`recall_structured`/`ContextRuntime` 一路透传即可（5 个文件） |

### 建议顺序

D4 → D3 → D2：D4 影响正确性（错误信息进别的会话），D3 影响一致性（矛盾偏好同时生效），
D2 影响可运维性（查不到「为什么那次 SQL 失败了」的明细），且 D2 要动表。

## Explicitly NOT doing

- **不动 `memory.query_template` 加 `failure_*` 列**：改为新表 `memory.query_failure`（已做）；
  模板中心不展示失败明细（要的话是前端需求，不是记忆契约）。
- **不做语义级 supersede**（D3 只到词表维度）：两句换着说法的偏好（「要柱子的那种图」）仍可能并用；
  语义 supersede 要 embedding 判定 + 阈值，属于二期。
- **不透传子图 `retry_counters`**：`retry_count` 现取 `state["sql_repair_count"]`，该键未在
  `ConfirmedExecutionState` 声明，生产多为 0；补状态字段是独立小改（会动 state-contract.md）。
- **不碰 Turn Understanding / 上下文 compact**：与本契约无关。
