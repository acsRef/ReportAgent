# Memory Architecture（记忆与上下文架构）

> 状态: 冻结（P1，2026-08-26）— 目标契约。当前实现差距见「现状映射」节与各 Phase plan。
> 上游: [2026-08-25-refactor-master-freeze.md](../plans/2026-08-25-refactor-master-freeze.md) §六。

## 一、四类记忆，职责分离

> **存储注记（2026-11-09，plan `long-term-memory-table`）**：长期记忆有**独立表**
> `memory.long_term_memory`，唯一键 `(user_id, memory_key)`；`memory_key` 是意图槽位
> （`pref:chart` / `pref:granularity` / `fact:<内容指纹>`）。同槽位只可能一行 →
> 「同一维度两条矛盾偏好同时 active」由**结构**排除，不靠 supersede 扫描。
> `memory.semantic_entry` 退化为**工作区**：候选队列（待固化）+ 会话级临时口径（`scope=session` + TTL）。
> 召回同时读两库并按语义带排序合并。
>
> **归并策略（2026-11-10，plan `semantic-memory-merge`）**：**不按 embedding 相似度自动合并**——
> 实测「同义 0.695~0.933 / 不同的事实 0.642~0.950 / 相反说法 0.906~0.962」三类重叠。
> 改为结构优先判定（极性 → 数字 → 原子值冲突 → 原子值一致 → 灰区 review → distinct）：
> 结构一致才累加证据；结构无信号但余弦 ≥0.90 只登记 `memory.merge_candidate` 待复审；
> 同一指标出现多条口径时**显式提示模型存在分歧**，不静默覆盖。同义词的真源是
> `metrics.yaml` 的 `synonyms`（业务词表），不是阈值。

| 类型 | 职责 | 关键规则 |
|---|---|---|
| **Session State** | 当前分析正在发生什么（year=2024、region=华东…） | 当前 session 有效、用户修改立即覆盖、不属于长期记忆 |
| **Conversation Memory** | 多轮连续性 | Recent Messages（最近 8~10 条）+ Summary（超过窗口后 Old Summary + New Batch → New Summary，**覆盖重写**）。现 `context.py` 的 L1/L2/L2.5 设计保留 |
| **Semantic Memory** | 跨 Session 长期语义 | `stable_preference` / `semantic_fact` / `temporary_preference` |
| **Query Memory** | 历史成功查询经验 | **Experience 不是 Truth**：始终 `Current Schema > Historical SQL` |

## 二、读取时机：Recall Before Agent

```text
User Request → Security → Session State → Context Runtime → Memory Decision
→ Selective Recall → Context Assembly → Agent
```

**Selective Recall 触发条件**（默认不自动召回全部长期记忆）：
1. 历史引用（"继续/刚才那个/再按产品细分"）；
2. 长期偏好影响当前任务（报告生成时召回图表偏好）；
3. 业务定义影响理解（GMV 定义）；
4. Query Experience 与当前查询高相似。

不召回：query 已完整、纯闲聊、与历史无关、用户明确覆盖过去状态。

## 三、Agent-specific Policy

| Agent | Conversation | Semantic | Query |
|---|---|---|---|
| Requirement | ✅ | ✅ | 少量 |
| Execution | 少量 | ✅ 业务事实 | ✅ |
| Report | ✅ 少量 | ✅ Preference | ❌ |

## 四、写入时机：Write After Reliable Event

```text
Task Outcome → Memory Write Decision → Insert / Update / Discard
```

**Query Memory 写入门槛不变**：严格 `SQL Validation SUCCESS AND Execution SUCCESS` 之后；失败 SQL 只记录 `failure_category / failed_sql / retry_count / error`（沿用 `QueryMemory.record_failure()`），不进入成功 Query Memory。

## 五、V1 简化（冻结）

> **修订（2026-11-09，用户批准，plan `2026-11-09-memory-admission-and-consolidation`）**：
> 本节三条不再全部成立，改写如下（历史原文保留在下方，作追溯）：
>
> | 原文 | 现状 |
> |---|---|
> | 无 promotion pipeline | **有，但只在离线批处理里**：`app/memory/consolidation.py` 每日跑一次。热路径仍然不做 promotion |
> | confidence 规则固定（inferred 一律 low、不进 active） | `explicit → high` 不变；**新增证据型**：同一说法被观察到 ≥ N 次（`semantic_entry.evidence_count`）→ 批处理升级为 active，confidence = **medium**（never high） |
> | temporary_preference 绑 session、任务结束可过期 | **写入侧开始真的产生它**（`app/memory/admission.py` 的会话标记：这次/先/暂时/本次 → `active` + `scope=session` + 24h TTL）；此前生产恒为空 |
>
> 另外新增**写入侧准入**这一层（原文没有）：记不记（闸 1 拒绝一次性任务参数/单条数据引用/提问/寒暄）、
> 记给谁记多久（闸 2 长期 vs 会话 vs 无标记 → candidate）。判据全部确定性，模型仍只能“提候选”。

- **行为偏好自动 promotion 降级不做**。第一版只有 `Explicit user statement → stable_preference`（"以后都用柱状图"直接存）；行为证据**不自动**变成长期记忆。candidate/evidence_count/promotion 机制留到有时间再做。
- **Confidence 不让 LLM 自己拍**。规则固定：`explicit_user_statement → confidence = high`；`explicit_business_definition → confidence = high`；`LLM inferred preference → 不进入 active long-term memory`。先把 Memory 做成「可靠记忆」而不是「猜测记忆」，以此控制 memory pollution。
- **temporary_preference 绑定 session_id，任务结束可过期**；Session State 与 TemporaryPreference 逻辑上分开、存储上第一版统一放 `agent.session`（字段区分），等 Evaluation 证明需要独立生命周期再拆表。

## 六、Lifecycle

支持 INSERT / UPDATE / SUPERSEDE / EXPIRE / DELETE；状态机：

```text
candidate → active → superseded
                  ↘ expired
```

新旧偏好冲突时旧 → superseded，避免多个 active 互相矛盾。
`semantic_entry` 补字段：`scope(user|session) / confidence / status / source / session_id / expires_at / created_at / updated_at / last_accessed_at`。

## 七、Conflict Priority（固定）

```text
Current User Requirement > Current DB Schema > Business Definition
> Stable Preference > Query Experience > Conversation Summary
```

**Schema 永远不能被 Memory 覆盖。**

## 八、Context Runtime 统一入口

```text
backend/app/context/runtime.py    # context_runtime.build(session_id, user_id, query, agent)
backend/app/context/policy.py     # agent-specific 召回策略
backend/app/context/decision.py   # 是否召回、召回什么
backend/app/context/assembler.py  # Filter → Conflict Resolution → Token Budget → Assembly
```

字段类型不能漂移：永远来自 live schema tools（`get_table_ddl` / `search_tables`），never from memory。

## 九、现状映射（截至 P1）

| 契约要素 | 现状 | 差距归属 Phase |
|---|---|---|
| Conversation Memory 底座 | `backend/app/context.py` L1 raw(10 条) / L2 digest(≤800 覆盖重写) / L2.5 mid_digest(≤400, 每 5 次归档) 已在位 | P4 组织为 `memory/conversation.py` |
| Semantic / Query Memory 底座 | `backend/app/infra/memory/`：policy.py / user_memory.py / query_memory.py（含 `record_failure()`）/ memory_manager.py / mem0_extractor.py | P4 归位 |
| 召回打分与淘汰 | pgvector `0.6×similarity + 0.2×importance + 0.1×LFU + 0.1×LRU` + per-user 容量上限（默认 200）冷淘汰；embedding 失败降级 keyword | 保留不动 |
| Selective Recall 决策 | 未实现——现全量召回注入 | P4（decision.py/policy.py） |
| semantic_entry 补字段 | 表结构未扩展 | P4 |
| Context Runtime 四文件 | 目录不存在（勿假设已建） | P3/P4 |

## 十、修订（2026-11-16）：对话压缩对标 DSH compaction，数据不参与压缩

plan：`docs/plans/2026-11-16-compaction-dsh-alignment.md`。**不改变**四类记忆的职责边界，改的是
Conversation Memory（L1/L2/L2.5）的**压缩策略**与装配层的**预算裁剪方式**：

1. **策略层独立**：`backend/app/memory/compaction.py` 承载纯函数——token 预算
   （`resolve_spec`，阈值 = min(窗口×0.8, 窗口 − 预留输出 − 余量)）、单元切分（一问一答成对）、
   **保尾选范围**（`select_fold_span`）、数据事实抽取（`collect_data_facts`）、确定性折法、
   shrink guard、数值出口闸（`has_numeric_literals`）。
2. **双闸触发**：token 压力闸（DSH 形态）**或** 条数兜底闸（`> RECENT_WINDOW + COMPRESS_BATCH`，
   保留历史行为，因为本项目消息可能极短）。折叠区间恒为**最老的前缀**，最近 `RECENT_WINDOW` 条、
   至少 3 条，且绝不切开一问一答。
3. **数据通道与叙述通道分离**：日期/金额/百分比/编号由确定性正则从被折叠区间**逐字抽取**，
   注入 `<数据事实>` 块，**不经任何模型**；模型写的 checkpoint 只承载叙述（5 段：用户目标 /
   已确认口径 / 用户纠正 / 已否决的写法 / 未完成事项），数字**只许逐字引用**——
   写完与数据事实做一致性对账（`numbers_not_in_facts`），出现事实里没有的数字（自己算/四舍五入/编造）
   即判越界、退回确定性折法。口径的权威来源仍是结构化槽位（`agent.requirement_draft` /
   `agent.report_version`），不是摘要散文。
4. **失败不空 + 净收益守门**：LLM 抛错或摘要没变短 → 退回确定性折法（只留骨架，数据仍只在
   `<数据事实>` 一处）并告警；若渲染结果**不比原文小**（短对话 + 数据块自身成本），本轮直接
   放弃压缩、原样返回——压缩绝不允许把上下文变大。
5. **装配层保尾**：`context/assembler.py` 的预算裁剪从 `text[:cap]`（砍尾=丢最新）改为
   整行粒度保尾（对话丢最老、召回按 §七 优先级从尾部丢），且不切断单个数值。

> 数据事实块**不落库**、每轮从消息确定性重算（`digest` / `digest_msg_count` /
> `digest_version` / `mid_digest` 四列不变，无 schema 迁移）。
>
> 真数据验证：`backend/tests/persistence/test_compaction_real_flow.py`（`RUN_REAL_COMPACTION=1`）
> 用数仓现查的 2024 华东/华南真值跑生产入口 + 真实 LLM：40 轮回话 784 → 291 tokens（降 63%）、
> checkpoint 数字对账 0 越界、折叠区间每个数字逐字保留、压缩与全量原文给出同样的执行计划。
> 这一轮还抓出 3 个合成用例抓不到的算法缺陷（裸月份/裸年份不被保护、确定性折法重复数据导致
> 压缩变大），修法见 plan `2026-11-16-compaction-dsh-alignment` 第二轮。
>
> **token 估算（2026-11-16 实测校准）**：CJK 1.67 字/token、其余 3.0 字/token（旧的一刀切 3.0
> 把中文低估 1.79 倍）。因此 `threshold_tokens = 52 428` 对应 ≈ 8.8 万汉字，
> `retain_tokens = 10 245` ≈ 1.7 万汉字；装配层 4 000 tokens ≈ 6 800 字符。
>
> 未做（与 DSH 的差异，均有理由）：日志式 surface 替换与 `compaction/start|end` 锁
> （消息表 + `report_version` 已是 append-only 事实源）、tool-pair 边界（对话表无 tool 消息）、
> `/compact` 命令（无命令面板）、provider 溢出触发（当前 prompt ≈ 4k tokens ≪ 65k 窗口）。
