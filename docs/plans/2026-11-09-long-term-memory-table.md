> 状态: 已完成

# 长期记忆单独一张表（`memory.long_term_memory`），按意图槽位唯一

## Context

用户：「长期记忆还是弄个表存着吧」。现状是**所有语义记忆混在一张 `memory.semantic_entry`** 里，
靠 `status / scope / expires_at` 三个字段表达生命周期：

| 现状问题 | 具体表现 |
|---|---|
| 候选、会话级临时、长期三类混存 | `semantic_entry` 里既有 `status='candidate'` 的待固化队列，也有 `scope='session'` 24h 到期的临时口径，也有 `scope='user'` 的长期偏好 |
| 冲突靠代码兜 | 同维度冲突在写侧 `supersedes` + 批处理再扫一遍；两处逻辑都要维护，还漏掉「措辞不同」的情况 |
| 去重靠先查后写 | `(user_id, content)` 没有唯一约束（并发可能重复行），`platform` 也没有“槽位”概念 |
| 证据/热度混在一起 | `evidence_count`（写侧证据）与 `access_count`（读侧热度）同表，语义容易混 |

## Design

### D1 · 新表 `memory.long_term_memory`，`UNIQUE (user_id, memory_key)`

```text
id / user_id / memory_key / content / memory_type / importance_score / intent_embedding
source / confidence / evidence_count / revision / previous_content / origin_id
access_count / last_access_time / created_at / updated_at
UNIQUE (user_id, memory_key)
```

- **`memory_key` 是意图槽位**：偏好按维度（`pref:chart` / `pref:granularity`），
  事实按内容指纹（`fact:<sha1(归一化文本)>`）。同一槽位只允许一行 →
  **「同一维度的两条矛盾偏好同时 active」从“需要代码收敛”变成“结构上不可能”**。
- **冲突写法是 UPSERT，不是 supersede**：`ON CONFLICT (user_id, memory_key) DO UPDATE`
  覆盖 `content`、`revision+1`、把旧内容塞进 `previous_content`（保留一步回溯，不建历史表）。
- 短期记忆（会话级 24h 临时口径）与候选队列**继续留在 `semantic_entry`**：
  它们是“工作区”，不该和长期库同表。语义上分成「长期库」与「工作区」两层。

### D2 · 召回同时读两张表（兼容 + 不中断）

`UserMemory.search` 变成两段查询后合并排序（都是 `user_id` 索引等值查，代价可忽略）：

1. `long_term_memory`（全部 = 长期有效）；
2. `semantic_entry`（`status='active'` 且未过期——覆盖会话级临时口径，以及历史/测试直接写入的 active 行）。

合并仍按上一轮定稿的 `rank_key`（语义带 + 带内热度）。`RankedMemory` 增加 `store` 字段，
`record_access` 按 `store` 命中对应表（否则长期记忆的热度永远不涨）。

为什么不做成一条 `UNION ALL` SQL：两张表列不完全对齐、embedding 参数要传两次，
可读性与可测性都更差；两次索引等值查 + 内存合并在 p99 上不是一个可感知的成本。

### D3 · 写入分流（热路径）

| 准入结论 | 落哪 |
|---|---|
| 长期标记（以后/默认/每次） | `long_term_memory`（UPSERT by memory_key） |
| 会话标记（这次/先/暂时） | `semantic_entry`（active + session + 24h，不变） |
| 无标记 | `semantic_entry`（candidate，不变） |

### D4 · 固化：晋级 = 搬进长期库

批处理把够格的 candidate `UPSERT` 进 `long_term_memory`（`origin_id` 记来源），
然后**删除 candidate 行**——候选池是工作队列，不是历史；证据计数随行搬过去。
同维度冲突不再需要“扫描 + supersede”：唯一键已经保证同槽位只有一行。

### D5 · 不做数据迁移

旧的 `semantic_entry(scope='user', status='active')` 行继续可召回（D2 的第二段查询），
不做搬运脚本：这类行会随使用逐渐被新的长期库取代；一次性迁移属于运维动作，另开。

## Files to change

| 文件 | 改动 |
|---|---|
| `backend/scripts/init_pg.sql` | 建 `memory.long_term_memory` + 唯一键 + user 索引 |
| `backend/app/memory/preferences.py` | `memory_key(content, memory_type)`（维度槽 / 事实指纹） |
| `backend/app/infra/memory/long_term.py` | **新增** `LongTermMemory`：`upsert` / `search` / `record_access` / `list` |
| `backend/app/infra/memory/user_memory.py` | `search` 合并两库；`record_access(store=...)`；`RankedMemory.store` |
| `backend/app/infra/memory/memory_manager.py` | 网关：`remember_long_term` / `list_long_term` |
| `backend/app/memory/manager.py` | 长期标记 → 走 `remember_long_term`（不再写 semantic_entry） |
| `backend/app/memory/consolidation.py` | 晋级 = upsert 长期库 + 删候选；去掉同维度扫描（唯一键取代） |
| `backend/tests/contracts/test_memory_long_term_key.py` | **新增**：槽位归类（图表/粒度/事实指纹/跨维度不同槽） |
| `backend/tests/persistence/test_long_term_memory.py` | **新增**：upsert 覆盖与 revision、唯一键冲突、召回合并、record_access 分表 |
| 既有 4 个测试 | 断言从 `semantic_entry` 改到 `long_term_memory`（行为变了就改测试） |

## Reused existing utilities

- `app/memory/admission.classify_scope`：写侧分流判据不变，只换落点。
- `app/memory/preferences.preference_dimension`：槽位归类的词表真源。
- `app/infra/memory/scoring.py::rank_key`：两库合并后仍用同一排序键。
- `semantic_entry` 的 candidate 队列语义、`promoted_at`、`evidence_count`：固化逻辑复用。

## Verification

```bash
python -m pytest backend/tests/contracts/test_memory_long_term_key.py -q
python -m pytest backend/tests/persistence/test_long_term_memory.py -q
python -m pytest backend/tests/persistence/test_memory_system_conformance.py backend/tests/persistence/test_memory_consolidation.py -q
python -m pytest backend/tests -k "memory or preference or chart" -q
```

验收：
1. 「以后都用柱状图」→ `long_term_memory` 一行（`memory_key='pref:chart'`），`semantic_entry` 无新行；
2. 再说「以后都用饼图」→ **同一行被覆盖**（`revision=2`、`previous_content` 是柱状图那句），不是两行；
3. 「这次先按周看」→ 仍在 `semantic_entry`（session + 24h），不进长期库；
4. 召回两库合并：长期库的偏好与会话级临时口径都能召回；`record_access` 分别涨各自的 `access_count`；
5. 固化：candidate 晋级后进长期库、候选行被删、`origin_id` 指回原 id；
6. 全部既有记忆测试不回归。

## Explicitly NOT doing

- **不做旧数据迁移**（D5）：旧 active 行仍可召回，逐步被新库取代。
- **不建长期记忆历史表**：只留 `previous_content` + `revision`（一步回溯）；要完整审计另开。
- **不改召回排序**：`rank_key` 语义带 + 带内热度不变。
- **不改准入判据**：admission 三档不变，只改“写到哪”。
- **不做跨用户/多租户**：唯一键含 `user_id`。
- **不改 Query Memory**：`query_template` / `query_failure` 不动（它们本来就是独立的表）。

## 落地记录（2026-11-09）

### 现在长什么样

```
memory.long_term_memory          ← 长期库（唯一键 user_id + memory_key）
  pref:chart        「以后都用饼图显示」  revision=2  previous_content=「以后都用柱状图显示」
  pref:granularity  「以后按月的粒度来显示」
  fact:<指纹>        「净销售额是扣退款后的口径」

memory.semantic_entry            ← 工作区（候选队列 + 会话级临时口径）
  candidate / scope=session + expires_at
```

真机探针（临时用户，跑完即删）：

```
长期库: [('pref:chart', '以后都用饼图显示', revision=2, previous='以后都用柱状图显示')]
召回(sess-A): [('这次先按周看','semantic','session'), ('以后都用饼图显示','long_term','user')]
召回(sess-B): [('以后都用饼图显示','long_term','user')]     ← 会话级不泄漏
```

### 三处与计划的不同（都写清理由）

1. **召回是“两段查询 + 内存合并”，不是一条 UNION**：两表列不完全对齐、embedding 参数要传两次，
   可读性与可测性都更差；两次 `user_id` 索引等值查 + 一次排序，代价远小于一次 embedding 调用。
2. **证据升级的候选搬完即删**（计划写的是“搬进长期库 + 删候选”，一致）；但**会话标记的候选**
   仍然只是在工作区里 `active + TTL`，不搬库——它本来就该过期，搬进长期库反而要额外清理。
3. **顺手修了一个真 bug**：`remember_explicit_preference` 之前**不接收 `session_id`**，
   于是会话级偏好写进去时 `session_id=NULL`，而召回过滤器要求 session 匹配 → **存了但永远召不回**。
   现在签名补了 `session_id`（`main.py` 短路路径透传）；没给 session_id 时会话级降级成 candidate，
   不假装“已记住”。

### 测试

新增：
- `backend/tests/contracts/test_memory_long_term_key.py`（8 例：槽位归类、跨维度不撞、指纹归一、稳定性）
- `backend/tests/persistence/test_long_term_memory.py`（7 例：覆盖 + revision + previous_content、
  同内容不涨 revision、不同槽共存、两库合并召回、会话级不跨会话、`record_access` 分表、防腐清理）

改动（行为变了就改测试）：`test_memory_system_conformance`（C3/C4/C4-跨维度）、
`test_memory_consolidation`（晋级语义）、`test_memory_write_pipeline_contract`（新增
`remember_long_term` spy）、`test_preference_explicit_wiring`（断言搬到长期库）、
`test_chat_requirement_background`（fake 签名补 `session_id`）。

全量：**1709 passed / 18 skipped**（约 2 分 23 秒）。

### 仍未做

- **旧数据迁移**：`semantic_entry` 里历史的 `scope='user'` active 行仍可召回（第二段查询），
  会随使用逐步被新库取代；一次性搬运脚本属于运维动作，未做。
- **长期记忆管理界面**：`MemoryManager.list_long_term()` 已经可用（槽位/正文/revision/previous），
  但没有 UI/API 端点；要做“用户能看、能删自己的记忆”需要单独一轮。
- **语义归并**：槽位归类仍是词表（图表/粒度）+ 内容指纹；换个说法说同一件事仍然不合并。