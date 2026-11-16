> 状态: 已完成（2026-11-16）

# 对话压缩对标 DSH compaction（数据不参与压缩）

## Context

用户要求：**完全按 DSH（DeepSeek Harness）的 compact 压缩算法对标重做 ReportAgent 的对话压缩**，但要改一处——
**「我这里重视数据本身，这些数据是不需要 compact 的」**。

先查清现状（不是凭记忆）：

| 现有层 | 位置 | 触发 | 行为 |
|---|---|---|---|
| ① 需求侧确定性压缩 | `backend/app/memory/conversation.py` `prepare_user_utterances_context` | 每轮需求解析 | 最近 4 条原样；更早的截骨架但**日期/金额/百分比/编号逐字保留**；仍超 1800 字符就压成一行纯数据 |
| ② 执行/报告侧散文摘要 | 同文件 `build_context` + `compression_and_extract`（`app/memory/prompts/conversation_prompts.py`） | 消息数 > 20 | 最近 10 条原样；中间批次一次 LLM 调用**覆盖重写**成 ≤800 字叙事摘要（prompt 明令「不含具体字段名和数值」）；每 5 次重写归档 ≤400 字 L2.5 |
| ③ 装配层预算 | `backend/app/context/assembler.py` `_apply_token_budget` | 拼接超 `P4C_ASSEMBLER_TOKEN_BUDGET`（默认 4000 tokens≈12000 chars） | **`text[:char_cap]` 直接截字符串** |

线上实测（真库，2026-11-16）：65 个会话 / 415 条消息；`agent.session.digest` 非空 **0 行**、`mid_digest` 非空 **0 行**；单会话最多 8 条真实消息、最大 143 字符；消息正文均值 `text` 13 字 / `assistant_text` 28 字。结论：**② 从未在生产里跑过**（阈值 20 条），① 的 1800 字符和 ③ 的 12000 字符也从未接近。

由此暴露三个**真问题**（与 DSH 无关，是自身缺陷）：

1. **③ 砍尾 = 丢最新**。`assembled = recall_block + conversation_context`，对话按时间正序在尾部，`text[:cap]` 一超预算就“保留最老、丢掉最近”，与 DSH「保尾」相反；
2. **② 一失败整块上下文变空**。两个调用点（`requirement_analysis_graph.py` / `confirmed_execution_graph.py`）用 `try/except` 兜 `ContextRuntime.build()`，摘要 LLM 一抛，`conversation_context` **连同记忆召回一起变成空串**，且 digest 不推进 → 下一轮再炸一次；
3. **两套标准互相矛盾**。① 规定「宁可超预算也不丢数字」，② 的 prompt 却明令「不含数值」——执行/报告侧的历史恰好是最需要数字的那一侧。

### DSH 的算法（作为对标基准，读的是安装版源码）

`@deepseek-ai/dsh-compaction`（抽象接缝）+ `compaction-basic`（策略与 LLM 摘要）+ `compaction-tool-result-pruner`（无模型剪裁）+ `command-compact`（`/compact`）：

1. **触发按 token 压力**：`thresholdTokens = floor(min(contextWindow×0.8, window − 预留输出 − headroom(65536)))`；
2. **先无模型剪裁**（>8192 字符的工具结果留头 4096/尾 1024），剪完重测仍超才调 LLM；
3. **保尾选范围**：从尾部按 token 累加攒够 `retainTokens`（默认 `messageBudget×0.16`）就停，压缩它之前的一切；system 首节点永不入范围；**绝不切开 tool-call/result 配对**；
4. **摘要用固定 8 段模板**（Primary Request / Key Technical Concepts / Files and Code / Errors and Fixes / Pending Jobs / Current Work / Next Step / Critical Context），要求逐字保留路径·数值·标识符；
5. **落库守卫**：摘要必须比被替换内容更小才提交；异步摘要期间 surface 变了就 abort；`compaction/start|end` 标记即锁，崩溃留孤立 start；
6. **溢出恢复**：provider 报 `CONTEXT_WINDOW_EXCEEDED` → 压缩后重试一次。

### 要改的那一处（用户诉求）

DSH 靠 prompt 要求模型“把数字抄对”；ReportAgent 的数据是**业务事实**（数值、口径、时间范围、结果行），抄错等于篡改。
所以本方案把数据通道从压缩路径里**整条摘出去**：

- **数据事实（facts）**：由确定性正则从被折叠区间抽取（复用 ① 的 `_PROTECTED_PATTERNS`），**逐字原样**写进 `<数据事实>` 块，**不经过任何模型**；
- **模型只写叙述**：checkpoint prompt 硬规则「禁止写任何数字/金额/百分比/日期」，模型连数字都看不到 → 没有篡改的机会；
- **口径的权威来源仍是结构化槽位**（`agent.requirement_draft` / `agent.report_version`），不是摘要散文——这也把 2026-11-04「散文固化错口径」那类缺陷从压缩路径上根除。

## Design

### 一、新增 `backend/app/memory/compaction.py`（纯函数，无 I/O）

| 符号 | 职责 |
|---|---|
| `estimate_tokens(text)` | `ceil(len/3)`，与 `context/assembler.py` 同口径（1 token≈3 chars） |
| `CompactionSpec` / `resolve_spec(...)` | DSH `resolveCompactSpec` 的移植：`threshold = min(window×threshold_ratio, window − reserved_completion − headroom)`；`retain_tokens = messageBudget × retain_ratio`；`retain_tokens >= threshold` 报 `CompactionConfigError` |
| `ConversationUnit` / `classify_units(messages)` | 把消息按**一问一答成对**切成不可再分单元（DSH 的 tool-pair 边界在 ReportAgent 的对应物就是 turn 边界）；跳过 `turn_trace` / `requirement_patch`（只有 metadata 的过程行与指针行，微内容零价值） |
| `select_fold_span(units, spec)` | 保尾：从最新往回累加，`tokens` 到 `retain_tokens` 停，**上限 `RECENT_WINDOW` 条消息**、下限 3 条；不切单元；返回 `(fold_units, keep_units)` |
| `collect_data_facts(units)` | 复用 `_PROTECTED_PATTERNS` 抽取日期/金额/百分比/编号，去重保序，**不截断单个事实**（上限按条数而非字符） |
| `fold_deterministic(units)` | ① 的确定性压缩下放到通用路径：骨架 + 数据原样（无 LLM 时的兜底） |
| `ensure_shrunk(candidate, shadowed_tokens)` | shrink guard：候选不比被折叠内容短就退回确定性折法，仍不短则本轮不压缩 |

### 二、新增结构化 checkpoint prompt（`CONVERSATION_CHECKPOINT_V2`）

段位面向数据分析改写（不是照抄 DSH 的编码 agent 段位），并保留既有 JSON 契约（`summary` / `extracted_schemas` / `extracted_preferences`，V1 的字段与 `compress_and_extract` 签名都不动）：

```text
## 用户目标
## 已确认口径（只写指标名/维度名，不写数值）
## 用户纠正（照抄客户的否定与改口）
## 已否决的写法
## 未完成事项
```

硬规则：**禁止出现任何数字、金额、百分比、日期**（数值由系统另行原样注入）；精确的字段名/列名/sql 片段允许保留；已有 checkpoint 就合并不要照抄。

### 三、`build_context` 重写为 DSH 五步

```text
measure → trigger → select(保尾) → summarize(checkpoint + 数据事实块) → shrink guard
```

- **双闸触发**（保留历史行为，防小消息灌水）：`tokens >= spec.threshold_tokens` **或** 消息数 > `RECENT_WINDOW + COMPRESS_BATCH`；
- **失败降级**：LLM 抛错 → 用 `fold_deterministic` 作为 digest 落库（不再让上下文变空），`logger.warning`；
- **输出**：`<长期脉络>`（L2.5 不变）+ `<对话摘要>`（checkpoint）+ `<数据事实>…</数据事实>`（仅当有事实）+ `<最新对话>`（原样尾部）；
- `digest_msg_count` 语义不变（已折叠到的消息数），**不新增数据库列**（事实块每轮从消息确定性重算）。

### 四、`assembler._apply_token_budget` 改为保尾

超出预算时**按行从头部丢**（丢最老的整条消息），绝不 `text[:cap]` 切断最新内容、也不把一个数字切成两半；单行自身就超预算时才退化为保留该行尾部并记 warning。

### 五、明确不做（本轮）

- 日志式 surface 替换 / shadowed seqs / `compaction/start|end` 锁：消息表 + `report_version`（append-only）已是事实源，LangGraph checkpoint 存的是图状态不是对话；
- tool-pair 边界：对话表只有 `text` / `assistant_text`，没有 tool 消息；
- `/compact` 命令：无命令面板；
- `ctx.tokenMeter` 服务化：`LLMConfig.context_window` 已知，纯函数足够；
- **溢出恢复**（DSH 第三触发）：当前最大 prompt ≈ 12k chars ≈ 4k tokens，距 65536 窗口很远，无 provider 溢出记录；留作后续，先不做无证据的机制。

## Files to change

| 文件 | 变更 |
|---|---|
| `backend/app/memory/compaction.py` | **新增**：spec / 单元切分 / 保尾选范围 / 事实抽取 / 确定性折法 / shrink guard（纯函数） |
| `backend/app/memory/prompts/conversation_prompts.py` | 新增 `CONVERSATION_CHECKPOINT_V2` + `build_conversation_checkpoint_prompt`（V1 保留） |
| `backend/app/memory/conversation.py` | `build_context` 改走五步；`compress_and_extract` 换 V2 prompt（签名不动） |
| `backend/app/context/assembler.py` | `_apply_token_budget` 改保尾按行折叠；复用 `estimate_tokens` |
| `backend/tests/contracts/test_compaction_engine.py` | **新增**：spec 数学 / 保尾 / 不切单元 / 事实逐字 / shrink guard / 降级 |
| `backend/tests/test_build_session_context.py` | 补「LLM 失败 → 降级不空」与「数据事实块」断言 |
| `backend/tests/contracts/test_dialogue_compact.py` | 补「装配层保尾不丢最新」用例 |

## Reused existing utilities

- `_PROTECTED_PATTERNS` / `_protected_fragments` / `_compact_utterance`（`app/memory/conversation.py`）——数字保护的真源，不重写；
- `app/memory/prompts/conversation_prompts.py` 的 6 段式 prompt 组装风格与 `CONVERSATION_SUMMARIZE_META`；
- `app.llm.budgets.max_tokens("memory_summary")` 预算表；
- `session_manager.get_context_state/save_context_state` 的 `_CONTEXT_FIELDS`（digest / digest_msg_count / digest_version / mid_digest，不扩列）；
- `app/infra/conversation/repository.get_messages`（唯一消息读取入口）；
- `context/assembler.py` 的 1 token≈3 chars 口径；
- `app/utils/text.safe_json_parse`（摘要 JSON 解析）。

## Verification

1. 单测：`cd backend && python -m pytest tests/contracts/test_compaction_engine.py tests/contracts/test_dialogue_compact.py tests/test_build_session_context.py -q`
2. 受影响面：`python -m pytest tests/contracts tests/graphs/test_context_runtime_main_chain.py tests/test_context.py -q`
3. 真库核对：造一条 25 条消息 + 含数字的会话，跑 `prepare_conversation_context`，断言 `<数据事实>` 里每个数字逐字出现、`<最新对话>` 是最新若干轮、`agent.session.digest` 落库、`digest_msg_count` 单调推进；
4. 失败降级：monkeypatch LLM 抛错 → 上下文非空且 digest 为确定性折法；
5. 回归：必要时跑 `python -m pytest tests -q -x`（全量 1800+ 例，仅在收尾时跑一次）。

## 已知取舍

- 数据事实块不落库、每轮重算：省一列 schema，代价是每轮多一次正则扫描（消息量 <100，可忽略）。
- checkpoint 里没有数字，模型无法在叙述里引用“那个数”；但事实块就在同一 prompt 里，且结构化槽位是权威来源。
- 本轮不做溢出恢复，若将来接入更大 prompt（如需把 `report_version` 的表格注入上下文）必须补这条。

## 验证结果（2026-11-16）

**全量回归**：`cd backend && pytest -q` → **1880 passed / 18 skipped**（基线 1844，新增 36 例），189s。

**真库 + 真 LLM 探针**（`.e2elogs/probe_compaction.py`，临时会话，跑完自动清理）：

| 项 | 结果 |
|---|---|
| 预算（真实 `LLMConfig`） | `context_window=65536` / `reserved=1500` / `headroom=8192` → `threshold_tokens=52428`、`retain_tokens=10245`，`retain < threshold` |
| 触发 | 36 条消息（含 12 条 `turn_trace` 过程行）→ 折叠 **21** 条、保留 10 条，`digest_version=1` |
| LLM checkpoint | **212 字、5 段齐、零数字**（模型遵守出口闸，未触发降级） |
| `<数据事实>` | `2024年1月、1200 万元、2024年2月、12.5%、39454、2024年3月、2024年4月` 逐字保留 |
| 最新一轮 | `2024年7月` 那轮原样留在 `<最新对话>` 尾部 |
| 落库回读 | `digest` 可读回、`digest_msg_count=21`、`digest_version=1` |
| 第二轮（无新批次） | `updates={}`、`batch=[]`（不再调 LLM），`<数据事实>` 仍在 |
| 上下文规模 | 523 chars ≈ 175 tokens（折叠前 ~1.1k chars） |

**降级路径实测**：第一次探针在未加载 `.env` 时运行，LLM 报 `Missing credentials` →
自动退回确定性折法（`digest` 435 字、`…（数据：…）` 逐字保留数字）→ 证明「失败不空」这一条在生产路径上真的生效。

**未触发确认**：现有 65 个真实会话最多 8 条真实消息（≈ 170 tokens），远低于 52428 的阈值，
所以本次改动对现网行为**无可见变化**——L2 仍然是「长会话才启用」，只是现在它有正确的策略与守卫。

## 第二轮：真数据 · 真流程验证（2026-11-16，用户要求「拿真实数据和真实流程证明算法」）

新增 `backend/tests/persistence/test_compaction_real_flow.py`（`RUN_REAL_COMPACTION=1` 才跑，不进 CI 门禁）：
数字**现查数仓**（`fact_orders` + `dim_date` + `dim_store`：2024 华东 9631.81 万元 / 华南 5206.83 万元 /
月度 Top2 / 真实订单 80889 / 真实同比 20.0%），链路走**生产入口**
`ContextRuntime().build(agent="confirmed_execution_sql_agent")`，下游差分用**真实** `sql_graph._plan`，
失败路径用**真实** `LLMAdapter`（坏 base_url），全程无 mock。

**这一轮直接抓出 3 个算法真缺陷**（合成用例抓不到，只有真数据 + 真模型才暴露）：

| # | 缺陷 | 现象 | 修法 |
|---|---|---|---|
| 1 | 裸月份不被保护 | 对话里「最高是 **11 月**」折叠后彻底消失（checkpoint 又不让编数字）→ 用户问「最高那个月」时上下文里没有月份 | `_PROTECTED_PATTERNS` 增加 `\d{1,2}[月日号]`，并加 `_drop_subsumed` 去重（`2024年11月` 不再重复记 `11月`） |
| 2 | 裸年份不被保护 | 「先看 **2024 年**华东」的年份在折叠后消失（旧规则认为年份由结构化 `time_range` 承载——那是需求侧的前提，执行侧摘要里没有这张卡） | 增加 `\d{4}[-/年]`；`test_dialogue_compact` 里「只有年份不算完整日期」随之修订 |
| 3 | 折叠反而**变大** | 确定性折法给每行追加「（数据：…）」与 `<数据事实>` 重复 → 实测压缩后 **258 tokens > 原文 204**，压缩成膨胀 | 数据通道只留一处（`<数据事实>`），骨架只截头部；另加 `_safe_truncate`（不切出 `2024年1` 这种半截日期），并在 `build_context` 末尾加**净收益守门**：渲染结果不比原文小就本轮放弃压缩 |

同时把「数值出口闸」从**一刀切禁数字**改成**一致性校验**（`numbers_not_in_facts`）：
模型引用 `11 月`、`9631.81 万元` 合法，只有数字 token 在数据事实里找不到（自己算/四舍五入/编造，如 `9631 万元`）
才判越界退回折法；`20` 与 `20.0` 视为同一个数（`_same_number`）。一刀切版实测会因模型正常引用月份而误判降级。

**验证结果（12 passed / 63s）**：

```
[checkpoint] LLM 结构化（5 段齐）
[数字对账] 越界数字 = []
[体积] 原文 2352 chars ≈ 784 tokens → 压缩后 871 chars ≈ 291 tokens   ← 降 63%
[滚动] digest_msg_count 105 → 111
[plan/压缩] time_range='2024'   metric='净销售额' dims=['月份','区域']
[plan/原文] time_range='2024年' metric='净销售额' dims=['月份','区域']  ← 语义等价
[短会话] 4 轮/8 条：digest=无 chars=250                                  ← 双闸不触发，原样
[降级] chars=1989 digest_len=1558 模式=骨架折法                          ← 真实 adapter 打不通，不空
```

覆盖点：触发与保尾（最新 5 轮逐字）/ 数据零丢失（折叠区间**每个** `protected_fragments` 都在事实块里）/
真实业务数值逐字可核对 / checkpoint 数字对账 / 压缩确实变小 / 无新消息不重复调 LLM /
滚动压缩前进且老数字不丢 / 压缩与原文给出同样执行计划 / 短会话不压缩 / 真实失败降级不空。

## 第三轮：手动压缩 + 空转闸（2026-11-16，用户追问「有没有手动 compact」）

**手动入口**（原先不存在，DSH 有 `/compact`）：

```
POST /api/v1/sessions/{session_id}/compact
→ 200 {compacted, reason, digest_msg_count, digest_version, context_chars, raw_chars}
→ 404 SESSION_NOT_FOUND（不存在/他人，不区分）
→ 409 SESSION_BUSY（任务在跑：压缩会改 agent.session.digest，执行中的任务正在读它）
→ 503 COMPACTION_FAILED（维护动作失败不该 500 打挂界面）
```

`reason` 老实说明为什么没压：`EMPTY_SESSION` / `NOTHING_TO_COMPACT`（连折叠区间都没有）/
`ALREADY_COMPACTED`（同一 fold_end 已压过）/ `NOT_WORTH_IT`（压了不会更小）。

**空转闸**（真流程暴露的副作用，本轮一并修）：只加「净收益守门」会让短消息会话每轮都白调一次
LLM 然后放弃（digest 不推进 → 下一轮再来）。补两道：

1. **收益上界闸**：`上限 = estimate_tokens(原文) − estimate_tokens(floor)`，floor = 尾部 + 数据块 + 标签
   （不含摘要）。上限 < `COMPACTION_MIN_GAIN_TOKENS`（默认 40）就直接跳过，**不调 LLM**；
2. **拒绝记忆**：同一 `(session_id, fold_end)` 判过「不值得」后 `COMPACTION_REFUSAL_TTL_S`（默认 600s）
   内不再试；手动压缩 / 删会话会 `clear_compaction_refusals()`。

真机验证（真后端 + 真会话，44 条消息）：

```
第一次点   → {"compacted":true,"digest_msg_count":33,"digest_version":1,
               "context_chars":1500,"raw_chars":4808}      15.5s   ← 降 69%
同边界再点 → {"compacted":false,"reason":"ALREADY_COMPACTED"} 0.02s   ← 不烧 LLM
追加 2 条  → {"compacted":true,"digest_msg_count":35,"digest_version":2,
               "context_chars":1333}                        6.8s
8 条会话   → {"compacted":false,"reason":"NOTHING_TO_COMPACT"}         ← 连折叠区间都没有
```

新增契约测试 `tests/contracts/test_session_compact_gate.py`（7 例：404/409/503/reason 透传/JWT 取
user_id）+ `TestNoWastedCalls`（3 例：收益闸不调 LLM / 拒绝记忆只试一次 / force 跳过两道闸）。

## 仍然没做：执行中上下文溢出（诚实清单）

DSH 的第三触发 `context-overflow`（provider 报窗口超限 → 强制压缩 → 重试一次）**没有实现**：

- 现状：`llm_resilience` 把这类错误当普通错误重试 2 次（无用），最终任务失败 → 落
  `report_version` FAILED（`kind=other`）+ SSE error，不会永远停在 `generating`；但**没有“压缩后重试”**；
- 风险实际上很小：单轮执行期间对话不增长（消息在轮次边界写），prompt 实测 ~4k tokens vs 65k 窗口，
  且 `MAX_TASK_DURATION=600` 兜住最坏耗时；
- 若要补：在 LLM adapter 里识别 `context_length_exceeded` / `maximum context length` → 归类
  `context_overflow` → 触发一次 `force=True, retain=0` 的全量折叠 → 重试一次（`maxOverflowRetries=1`）。
  这是明确的下一步，本轮没做是因为没有证据表明会撞上（无一条真实溢出记录）。



