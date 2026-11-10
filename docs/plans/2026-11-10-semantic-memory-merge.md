> 状态: 已完成

# 记忆的语义归并：结构优先，embedding 不裁决

## Context

用户要求考虑「语义归并」——现在槽位是「词表维度 + 内容指纹」：

- 偏好靠词表落固定槽（`pref:chart` / `pref:granularity`），换说法能合并；
- 事实/无维度内容落 `fact:<sha1(归一化正文)>`，于是**换个说法说同一件事就是两个槽**，
  证据也不累加（`evidence_count` 永远停在 1，推断类永远升不了级）。

直觉方案是「用 embedding 相似度合并」。所以在写任何代码之前先量了一遍
（`evaluation/semantic_merge_probe.py`，26 个标注对 / 40 句，真 embedding）：

| 类别 | 相似度区间 | 中位 |
|---|---|---|
| 同义说法（应合并） | 0.695 ~ 0.933 | 0.881 |
| 不同的事（必须分开） | 0.642 ~ **0.950** | 0.809 |
| **相反说法**（字面极像、语义相反） | **0.906 ~ 0.962** | 0.933 |

三条结论，直接决定设计：

1. **类间重叠**：华东 vs 华南 是 **0.950**，比 10/12 条真正的同义说法都高；
2. **相反说法永远高**：`以后都用柱状图` vs `以后不要用柱状图` = 0.942，
   `以后报告都用表格` vs `……别用表格` = **0.962** —— 余弦对否定**完全无感**；
3. **阈值扫描全线告负**：θ=0.90 时 7 个错误合并 / 8 个错误拆分；θ=0.94 仍有 3 个误并。

→ **单靠 embedding 阈值做自动归并，在这套语料上是不可行的**（会静默覆盖用户的记忆）。
但同一批数据也说明：**结构信息足够**——只要抽出「原子值」和「极性」，正确率立刻翻转。

## Design

### D1 · 判定器：结构优先，语义只进灰区

顺序敏感（`evaluation/semantic_merge_guard_eval.py` 是可跑的判定器原型）：

| 顺序 | 判据 | 结论 | 理由 |
|---|---|---|---|
| 1 | **极性不同**（一方含否定词） | `distinct` | 「用柱状图」vs「不要柱状图」字面 0.942，语义相反 |
| 2 | **数字集合不同** | `distinct` | 数值是硬事实（和「数字不许压缩」同一条原则） |
| 3 | 同一 kind 两边都有值且**取值不同** | `distinct` | 华东/华南、按周/按天、柱状图/饼图、净销售额/实收金额 |
| 4 | 两边原子值**完全一致** | `merge` | 柱状图/柱形图/条形图；按月/每个月；华南/福建 ✅ 不依赖余弦 |
| 5 | 无原子值但余弦 ≥ 0.90 | `review` | **只提示，不自动合并**（写 `merge_candidate`，等 glossary 或人工） |
| 6 | 其余 | `distinct` | 宁可多一条，不可错并一条 |

同一批 26 对上的实测：**错误合并 0、召回 0.92（11/12）、精确率 1.00**。
唯一漏并的是 `GMV 按净销售额算` vs `GMV 就是扣掉退款之后的销售额`——后者没用术语，
属于**业务词表缺口**（`metrics.yaml` 的 `synonyms` 里补「扣掉退款」即可），不是判定器的问题。

### D2 · 生产落点：三种情况分别处理

| 情况 | 现在的行为 | 目标 |
|---|---|---|
| 偏好（有维度） | 槽位合并，但**极性与取值都挤在同一个槽** | 槽位键补极性：`pref:chart` / `pref:chart:exclude`；取值覆盖保持（latest wins） |
| 事实（无维度） | 指纹槽，换说法 = 新行 | **不自动合并**；同一指标出现多条口径时，在召回上下文里**显式标注「存在 N 条口径」**，让模型看见分歧，而不是静默覆盖 |
| 推断类证据 | 指纹槽 → 换说法不累加 | 只有判定器说 `merge` 时才跨说法累加 `evidence_count`；`review` 既不累加也不晋级 |

### D3 · 顺手修一个现存缺陷（已复现）

槽位键不含极性导致真实丢数据：

```
用户先说「以后都用饼图」→ pref:chart = 用饼图
再说「以后不要用柱状图」→ pref:chart 被覆盖 = 不要用柱状图（revision=2，previous=用饼图）
```

结果：**正向偏好（饼图）被一条否定句顶掉**，而否定句在应用层只是“排除”，
于是系统最终退回到 chart_advisor 的默认值。加极性到槽位键即可：
两条各占一个槽，应用层先取正向、再用排除集合约束（`apply_chart_preference` 已经是这个语义）。

### D4 · 同义词的真源是业务词表，不是 embedding

原子值抽取的四个 kind 各自有词表，业务指标直接**读 `app/knowledge/metrics.yaml` 的
`synonyms`**（单一真源，已经是卡片解析用的同一份）。工作流因此变成：
「发现漏并 → 往词表加一条说法」，可评审、可回归，而不是「调阈值直到看起来好一点」。

### D5 · 灰区（review）是可观测的，不是垃圾桶

`merge_candidate(user_id, memory_key, a_content, b_content, similarity, reason, created_at)`：
只记录、不生效；固化批处理每轮报「新增 N 条待复审」。它同时也是词表缺口的输入——
攒够 10 条同一 kind 的 review，就该往词表里加同义词了。

## Files to change

| 文件 | 改动 |
|---|---|
| `backend/app/memory/merge.py` | **新增**：`polarity()` / `atoms()` / `decide()`（原型已验，搬进 domain 并接 metrics.yaml） |
| `backend/app/memory/preferences.py` | `memory_key()` 支持极性后缀（`pref:chart:exclude`） |
| `backend/app/memory/consolidation.py` | 证据累加前先 `decide()`；`review` 记 `merge_candidate` 且不晋级 |
| `backend/app/infra/memory/long_term.py` | 事实多条口径的读取（供上下文标注「存在 N 条口径」） |
| `backend/app/infra/memory/memory_manager.py` | `record_merge_candidate` / `list_merge_candidates` 网关 |
| `backend/app/context/assembler.py`（或 recall 后处理） | 同指标多口径提示行 |
| `backend/scripts/init_pg.sql` | `memory.merge_candidate` 建表 |
| `backend/tests/contracts/test_memory_merge_decide.py` | 判定矩阵（复用 `evaluation/semantic_merge_probe.py` 的 26 对标注） |
| `backend/tests/persistence/test_long_term_polarity.py` | 极性槽位：正向偏好不再被否定句顶掉 |

## Reused existing utilities

- `app/knowledge/metrics.yaml` 的 `name` + `synonyms`：业务指标同义词的唯一真源。
- `app/memory/preferences.py::preference_dimension` + `memory_key`：维度槽位。
- `app/memory/admission.py::_NEGATIONS`：否定词表（与图表应用层共用同一份）。
- `semantic_entry.evidence_count` / `promoted_at`：证据与固化状态。
- `evaluation/semantic_merge_probe.py` + `evaluation/semantic_merge_guard_eval.py`：可重跑的测量与判定器评估（改词表后必须重跑）。

## Verification

```bash
# 1) 重新测量（真 embedding，~2 分钟；改词表/阈值后必跑）
python evaluation/semantic_merge_probe.py --json evaluation/results/semantic_merge_probe_latest.json
python evaluation/semantic_merge_guard_eval.py evaluation/results/semantic_merge_probe_latest.json
# 期望：错误合并 = 0，召回 ≥ 0.92

# 2) 判定矩阵单测（离线，纯函数）
python -m pytest backend/tests/contracts/test_memory_merge_decide.py -q

# 3) 极性槽位（真 PG）
python -m pytest backend/tests/persistence/test_long_term_polarity.py -q
```

验收：
1. 「以后都用饼图」+「以后不要用柱状图」→ 长期库**两行**（`pref:chart` / `pref:chart:exclude`），
   应用层仍得出饼图；
2. 同义说法（柱状图/柱形图/条形图）→ 判定器 `merge`，证据累加到同一槽；
3. 相反说法 / 数字不同 / 原子值冲突 → `distinct`，绝不合并；
4. 无原子值且余弦 ≥ 0.9 → 只进 `merge_candidate`，长期库不变；
5. 同一指标两条不同口径 → 召回上下文出现「存在 2 条口径」提示（不覆盖）。

## Explicitly NOT doing

- **不做 embedding 阈值自动合并**：实测类间重叠（华东/华南 0.950 > 多数同义对），
  自动合并会静默覆盖用户记忆。这条是设计结论，不是妥协。
- **不建同义词管理 UI**：词表在 `metrics.yaml`，改它走代码评审；UI 化另开。
- **不做跨用户/全局归并**：槽位仍以 `user_id` 为前缀。
- **不改召回排序**（`rank_key` 语义带不变）与**准入判据**（admission 三档不变）。
- **不自动修复历史**：老的 `fact:<指纹>` 多行不会自动合并；需要时跑一次带 dry-run 的重写脚本。
- **不做 LLM 判同义**：要真 LLM 配对跑 + 成本，且与「确定性优先」的写入侧原则冲突；留给灰区复审。

## 落地记录（2026-11-10）

按 D1–D5 实施完毕。真机探针（临时用户，跑完即删）：

```
极性槽：  pref:chart = 以后都用饼图
         pref:chart:exclude = 以后不要用柱状图      ← 不再互相覆盖（此前饼图被顶掉）

同义归并：长期库 fact:<hash>「净销售额是扣退款后的口径」evidence=1
         候选「净销售额等于销售额减掉退款」→ 判定器 merge → evidence=2，**不新建行**

多口径提示：两条不同口径（不含退款 / 包含退款）同时召回时，多出一条
         [口径提示] 记忆里同一指标存在多条口径，请与用户确认：净销售额：2 条不同口径（…）
```

### 与计划的一处偏差（被契约测试抓住的，值得记）

口径提示最初写在 `infra/memory/memory_manager.py` 里，直接 `from app.memory.merge import atoms`。
全量测试立刻红了：`test_memory_conversation_decouple` 钉着「infra.memory 不得反向 import
domain/context」。这不是测试碍事，而是**分层真的错了**——「同一指标有多条口径」是**语义判断**，
infra 只该懂存取。于是把提示移到 domain 侧的 `app/memory/semantic.py`（semantic view），
infra 回归纯存取。这条正好演示了契约测试的用途。

### 实现要点

| 文件 | 内容 |
|---|---|
| `backend/app/memory/merge.py` | **新增** `polarity / atoms / numbers / decide`；`NEGATION_WORDS` 成为全仓否定词唯一真源（`report/preference.py` 改为引用它） |
| `backend/app/memory/preferences.py` | `memory_key` 支持极性后缀：`pref:chart` / `pref:chart:exclude` |
| `backend/app/memory/consolidation.py` | 无标记候选先 `decide()`：`merge` → 累加既有条目证据并删候选；`review` → 记 `merge_candidate` 并删候选；否则才走证据升级 |
| `backend/app/memory/semantic.py` | domain 侧口径分歧提示（`_multi_definition_hint`） |
| `backend/app/infra/memory/user_memory.py` | `list_candidates_with_match`（一次 LATERAL 查询拿到「候选 + 最近长期条目 + 余弦」）、run 记账加 `merged/reviewed` |
| `backend/app/infra/memory/long_term.py` | `add_evidence` / `record_merge_candidate` / `list_merge_candidates` |
| `backend/app/jobs/memory_consolidation.py` | 输出 `merged` / `reviewed` |
| `backend/scripts/init_pg.sql` | `memory.merge_candidate` 表 + `consolidation_run.merged/reviewed` 列 |

测试：`test_memory_merge_decide.py`（21 例判定矩阵，含极性/数字/跨 kind/灰区）、
`test_memory_merge_polarity.py`（5 例极性槽 + 同义累加 + 灰区复审）。
全量后端 **1735 passed / 18 skipped**。

测量脚本保留并纳入流程：改词表/阈值后重跑
`evaluation/semantic_merge_probe.py` → `evaluation/semantic_merge_guard_eval.py`
（当前基准：错误合并 0 / 精确率 1.00 / 召回 0.92）。

### 仍未做

- **灰区的人工出口**：`merge_candidate` 目前只能查库（`MemoryManager.list_merge_candidates`），
  没有界面；按用户要求「界面不做」，但攒够同类条目时应往 `metrics.yaml` 的 `synonyms` 加说法。
- **历史事实的重跑**：老的 `fact:<指纹>` 多行不会自动合并；需要时写一个带 dry-run 的重写脚本。
- **跨用户归并**：不做（槽位含 `user_id`）。
## 追加（2026-11-10）：CI 红了一次，根因是 init_pg 的顺序

push `b5577b1` 后 `backend-gate` 红、`frontend-gate` 绿。本机全量是绿的——典型「在我机器上没问题」，
原因是：本机 DB 早就存在，`merged/reviewed`、`promoted_at`、`evidence_count` 是我用 `ALTER` 手工补的；
而 CI 是**全新库**，只跑 `backend/scripts/init_pg.sql`。

排查方式（可复用）：用 docker 起一个空库 `ragent_ci_check`，把 `init_pg.sql` 打进去，
再跑那些按 `DATABASE_URL` 取连接串的测试。结果一目了然：

1. `init_pg.sql` 里两条 `ALTER TABLE memory.semantic_entry ADD COLUMN promoted_at / evidence_count`
   写在 `CREATE TABLE memory.semantic_entry` **之前** → 全新库报 `relation does not exist`（psql 继续，
   但列没加上）；
2. `consolidation_run` 的 `CREATE` 里没有 `merged/reviewed`，只有我本机手工加过 →
   `record_consolidation_run` 在 CI 库里失败（`column "merged" ... does not exist`）。

复现与验收（在 `ragent_ci_check` 上）：

```
# 旧 schema（手动 DROP 这两列）
ALTER TABLE memory.consolidation_run DROP COLUMN merged, DROP COLUMN reviewed;
pytest backend/tests/persistence/test_memory_consolidation.py   → 2 failed（run 记录断言）
# 修完 init_pg 重灌
… 31 passed
```

修法（`backend/scripts/init_pg.sql`）：
- `semantic_entry` 与 `consolidation_run` 的新列**写进 CREATE**（全新库靠它）；
- 软迁移 `ALTER ... ADD COLUMN IF NOT EXISTS` 一律**放到对应 CREATE 之后**（老库靠它）。

教训记在这里：**新增列必须同时改 CREATE 和 ALTER，且 ALTER 在 CREATE 之后**；
只在本机 ALTER 过、没写进 init_pg，CI 必红。