> 状态: 已完成

# 记忆召回质量与稳定性 scoreboard（L1 正确性 + L4 可靠性，纯本地验证）

## Context

用户诉求：「我需要你来验证我这个设计记忆的正确性和可靠性，你觉得应该怎么测试」。
讨论后选定**先做 L1（召回质量）+ L4（稳定性/降级）**，并且**不进 CI 门禁**——只要一份能直接
点开的本地判决书。

现状（先摸过，别重复造）：

| 已有资产 | 覆盖了什么 | 缺口 |
|---|---|---|
| `evaluation/memory_gold_runner.py`（25 记忆 / 30 query，Recall@1/@3/MRR/Clean） | 真实 embedding 下的**位次**指标 | ①只给总分，**不分层**——某类记忆崩了会被平均掉 ②**只跑一次**，没有稳定性口径 ③没有 PASS/FAIL 判定，数字只能靠人看 |
| `backend/tests/persistence/test_memory_gold_recall.py` | 词位化 fake embedding 的链路硬断言（CI 可跑） | 不测真实语义空间 |
| `test_memory_agent_layer_guardrails.py` / `test_user_memory_eviction.py` / `top_k_zero` / `promote` | 隔离、淘汰、容量、候选态 | 属于 L3/L0，不在本次范围 |
| `evaluation/tests/test_memory_llm_sql_behavior.py` | 真 LLM 行为（4 case） | 属于 L2「用没用上」，本次不做 |

## Design

### D1 · 不新建平行 runner，扩展 `gold_memory_runner.py`

一个入口就够（`--repeat/--html/--degrade/--min-*`），避免「两个 runner 算法不一致」的历史坑。
pytest 版（fake embedding）**零改动**，两者分工不变：pytest 管链路正确性，runner 管真实语义空间的
质量与稳定性。

### D2 · 分层：聚合数会藏起崩塌的那一类

同一批 case 按三个维度各算一遍（同一份召回，不同切法，不多花 API 钱）：

| 维度 | 取值 | 为什么值得单独看 |
|---|---|---|
| `agent` | `requirement_analyze` / `confirmed_execution_sql_agent` / `report_plan_analysis` | 三档 policy 不同（Execution 档还 drop preference），总分掩盖档位差异 |
| gold 记忆类型 | `preference` / `fact` / `query` | 「偏好记得住、业务事实记不住」和反过来，是两种完全不同的设计缺陷 |
| `memory_scope` | `semantic` / `query` / `both` | 两个 domain view 是两套召回实现，要能分开问责 |

为此把 per-case 结果从「首个命中的 rank」升级成 `gold_rank_by_key`（每个金标各自的名次），
分层按 **key** 聚合而不是按 case 猜类型（一个 case 可能同时有偏好和事实两种金标）。

### D3 · 稳定性：重复 N 次，且**每轮清空 embedding 缓存**

`--repeat 3`；每轮前 `clear_cache()`。理由：不清缓存的话第二轮起向量直接命中进程内 LRU，
测出来的「稳定」是**缓存稳定**，不是真实世界的稳定。清了才是「同一问题隔几分钟再问一次」
的真实表现（含 embedding API 本身的抖动）。代价是 30 query × 3 轮 ≈ 90 次 embedding 调用，可接受。

指标：

| 指标 | 定义 | 判据 |
|---|---|---|
| `spread_recall1` | 各轮 Recall@1 的极差 | ≤ 0.10（30 例下 1 例翻转 = 0.033） |
| `stable_top1` | top-1 命中与否三轮完全一致的 case 占比 | ≥ 0.90 |
| `mean_jaccard` | 每个 case 三轮召回文本集合的两两 Jaccard 均值 | ≥ 0.90（只看 top-1 会漏「集合本身在漂」） |

### D4 · 降级探针（L4）：把「优雅降级」这句口号量出来

强制 `embedder.embed` 抛异常（`embed_or_none` → None → keyword 分支），再跑一遍全量 case：

- 硬断言：不抛异常、并且**观测到** `app.embedding.service` 的
  `Embedding failed, falling back to keyword search` 警告（可靠性 = 可观测，静默降级不算）。
- 观测项（先测量再定目标，不做门槛）：`degraded_hit_rate`——降级后 gold 还能命中的比例。

**已知风险**（本轮要用数据证实或证伪）：keyword 分支按**空白字符**切词
（`user_memory.search`），中文 query 没有空格 → 整句当一个 token → `content ILIKE '%整句%'`
→ 语义记忆在中文场景下**等于全失效**。英文/带空格 query 才有机会命中。若探针证实，这是
「降级」名不副实的确凿证据，修法（中文分词 / 退化到 recent-k）另开 plan。

### D5 · 判决书：终端 + JSON 快照 + 自包含 HTML

- 终端：`层 × 指标 × 阈值 × 实测 × PASS/FAIL`（gate）/ `观测项`（observation）两段分明。
- JSON：`evaluation/results/memory_gold_<ts>.json`，保留原有字段（向后兼容），新增
  `passes` / `strata` / `stability` / `degradation` / `gates`。
- HTML：`docs/reports/memory-scoreboard.html`，双击可看，零外部依赖（配色沿用
  `docs/reports/scenario-report.html` 的 tokens + 卡片风格）。

退出码：任一 gate 不过 → `2`（本地手动门用，不进 CI，所以不担心抖动卡流水线）。

## Files to change

| 文件 | 改动 |
|---|---|
| `evaluation/gold_memory_runner.py` | 扩展：argparse、分层聚合、N 轮稳定性、降级探针、gate 判定、HTML 输出 |
| `backend/app/embedding/service.py` | 加 `clear_cache()`（一行方法，供评测每轮清缓存；不改任何行为） |
| `docs/reports/memory-scoreboard.html` | 生成的判决书（提交进仓库，可直接点开） |
| `docs/plans/README.md` | 登记本 plan |

## Reused existing utilities

- `evaluation/memory_gold_cases.json`（25 记忆 / 30 query，含 `gold` / `forbidden` / `agent` / `memory_scope`）：**不新增用例集**，先把既有金标吃透。
- `gold_memory_runner.py` 既有的 `_make_user` / `_cleanup` / `_reset_access` / `_seed` / `_recall`：原样复用，只把「跑一轮」抽成函数。
- `app.infra.memory.memory_manager.MemoryManager`（写路径）+ `app.memory.semantic|query`（召回路径，与 `ContextRuntime` Step 4 同层）——测的就是生产同一层，不模拟。
- `app.embedding.service.EmbeddingService.embed_or_none` 的既有降级逻辑与 warning 日志：探针只是触发它，不改它。
- HTML 风格沿用 `docs/reports/scenario-report.html`（同款 palette/卡片）。

## Verification

```bash
# 全量：3 轮 + 降级探针 + HTML
D:/miniConda/envs/agent/python.exe evaluation/gold_memory_runner.py \
  --repeat 3 --degrade --html docs/reports/memory-scoreboard.html

# 只看召回（快，1 轮）
D:/miniConda/envs/agent/python.exe evaluation/gold_memory_runner.py --repeat 1
```

验收：
1. 终端打印 gate 表，全部 PASS 或明确指出哪一项不过（过程中发现真实缺陷不算失败，算产出）。
2. 三次重复的 Recall@1 极差与 case 级 Jaccard 有数；相同输入下重跑整体结论一致。
3. 降级探针给出 `degraded_hit_rate` 与「是否观测到 warning」两件事。
4. `docs/reports/memory-scoreboard.html` 能在浏览器直接打开，不依赖网络。
5. pytest 版 gold recall（fake embedding）零回归：`pytest backend/tests/persistence/test_memory_gold_recall.py`。

## 落地记录（2026-11-09）

### 判决：设计成立，但「降级」名不副实

两次完整运行（每次 3 轮 + 降级探针），**9 条 gate 全 PASS**：

| 指标 | 实测 | 阈值 |
|---|---|---|
| Recall@1 | 0.840 | ≥ 0.80 |
| Recall@3 | 0.960 | ≥ 0.95 |
| MRR | 0.900 | ≥ 0.85 |
| Clean（forbidden 零命中） | 1.000（0/26） | = 1.00 |
| spread(Recall@1) | 0.000 | ≤ 0.10 |
| stable top-1 | 1.000 | ≥ 0.90 |
| mean Jaccard | 0.942 / 0.973（两次运行） | ≥ 0.90 |
| 降级不抛异常 / 有告警 | 是 / 44 次 | 硬要求 |

结论分三层：

1. **排位可靠**：两次运行、六轮，`Recall@1/@3/MRR/Clean` 完全一致；漏召的 case 也是同一批
   （R@1 漏 `q9/q18/q21/q25`，R@3 只漏 `q18`）。这支持「记忆确实被检索到且排序稳定」。
2. **尾部不可复现**：命中判定稳定，但召回**集合的尾部**在漂——Jaccard 0.94~0.97，
   最低 `q20` 仅 0.56（两轮各 2 条，换了其中 1 条）；`q1/q4/q18` 各 3 条、每轮换 1 条。
   影响：如果下游只塞 top-3 进 prompt，第 3 条**每次可能不同**；agent 若依赖它做决策就是 flaky 源。
3. **降级几乎等于失效**（本次最有价值的发现）：强制 embedding 失败后，`any_items_rate = 0.28`、
   `Recall@3 = 28%`（从 96% 掉到 28%）。原因在 `user_memory.search` 的 keyword 分支：
   `query.lower().replace(",", " ").split()` 按**空白**切词，中文 query 没有空格 → 整句当一个
   token → `content ILIKE '%整句%'` → 必然 0 行。**「Embedding 挂了就降级到关键词」这句话在中文
   场景下是不成立的**，只剩 28% 的 case 还召回到东西（带空格 query / query memory 路径）。

### 分层暴露的问题（总分掩盖了的）

| 维度 | 分桶 | 命中率 | MRR | 读法 |
|---|---|---|---|---|
| agent | `requirement_analyze` | **83.3%** | 0.833 | 唯一低于 100% 的档；需求层取的是「口径类事实」，口语文案与记忆文案差距最大 |
| agent | `report_plan_analysis` | 100% | 0.944 | 偏好类好召回 |
| agent | `confirmed_execution_sql_agent` | 100% | 0.900 | query memory 命中率高 |
| gold_type | `fact` | 90.9% | 0.712 | 事实类 MRR 最低——**排在前面的是对的，但常常不是第一条** |
| gold_type | `preference` | 92.3% | 0.744 | 同上 |
| gold_type | `query` | 100% | 0.795 | 历史 SQL 召回最稳 |

单点案例：
- `q18`「促销表现报告按季度看」→ `fact_promo_q3` **完全没进 top-3**（唯一 R@3 漏召）。
  同义/概念距离不足，importance 权重（0.2）顶不上语义（0.6）的缺口。
- `q9`「报告要柱状图和表格结合」→ 表格 rank 2、柱状图 rank 3：**多偏好合并诉求**没有「组合」概念，
  两条同题材偏好互相挤位。
- `q25`「统计各区域 GMV 销售额」→ `fact_gmv_net` rank 2、`qry_region_sales` rank 5。

### 对先前判断的更正

我在讨论里说「embedding 降级是静默的」——**不准确**：`embed_or_none` 有
`Embedding failed, falling back to keyword search` 警告，44 次探针里每次都能观测到。
真正黑盒的是 **`backend/app/context/*` 的召回决策**（零 logger、零 span）：能查到「降级了」，
查不到「这次为什么没召回那条偏好、分数多少、被哪条挡了」。这一点仍然成立，留给 L2 一起做。

### 实施中修掉的自身缺陷（都记在这，避免下次重犯）

1. `_reset_access` 写的是 `UPDATE query_template`（漏 schema）→ 直接报 `UndefinedTable`。
2. 名次用 `enumerate(texts)` 从 **0** 起算 → `Recall@1` 全部错位（第一次跑出 0.120 的假指标）。
3. 种子校验缺失：写入路径是 `embed_or_none`，一旦 embedding 超时/限流就会写进「无向量行」，
   那种行永不命中，指标会把**环境问题**报成**设计问题**。现在 `_verify_seed` 硬校验
   （19 条带向量 + 6 条 query），不满足直接失败。
4. 分层最初只对 case 猜类型 → 一个 case 同时含偏好与事实时必错；改成按**金标 key** 聚合。

### 产出

- `evaluation/gold_memory_runner.py`：`--repeat/--degrade/--html/--from-snapshot/--min-*`（后者可调阈值）。
- `docs/reports/memory-scoreboard.html`：自包含判决书（无网络依赖，双击可看）。
- 快照：`evaluation/results/memory_gold_20261109_185459.json`（Jaccard 0.973）与
  `..._190008.json`（0.942）——**两份都留着**，因为「尾部漂移幅度本身不稳定」就是结论之一。
- 回归：`backend/tests/persistence/test_memory_gold_recall.py` **30 passed**（fake embedding 口径不受影响）。

### 建议的下一步（不在本 plan 内）

1. 修 keyword 降级：中文分词（字符 bigram 最省事）或退化到 recent-k，并在上下文里显式标注「降级召回」。
2. `fact` 类 MRR 0.71 偏低：看是否该给「口径类事实」提高 importance 或在同分时按类型优先。
3. 多偏好合并（`q9`）：preference 召回是否需要「按维度去重后合并」，而不是单条打分挤位。
4. L2 行为层（ON/OFF 配对）：本报告只证明「召回了、稳」，**没证明「改变了答案」**。

## Explicitly NOT doing

- **不做 L2 行为效果（召回有没有真的改变答案）**：那要真 LLM 配对重跑，是下一份 plan 的事；本 plan 的 HTML 里会明确写「召回 ≠ 起效」，不越界声称。
- **不进 CI**：不新建 workflow、不改 `ci.yml`；阈值只用于本地判决。
- **不修降级路径**：探针只测量与取证（中文零命中若要修，另开 plan：分词 or 退 recent-k）。
- **不补 context runtime 的 recall span/日志**（我先前说它「黑盒」是对的，但这属于产品代码改动，等 L2 一起做）。
- **不碰 eviction / top_k=0 / promote / 跨用户隔离**：已有 persistence 测试覆盖（L0/L3）。
