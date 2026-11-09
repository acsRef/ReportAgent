> 状态: 进行中

# 记忆准入 + 离线固化（长期记忆不在热路径上判定）

## Context

用户提出两件事，本 plan 一起回答：

1. 「什么记忆需要塞进去 / 判断是否长期记忆，应该专门处理」——写入侧缺一道**准入**；
2. 「长期记忆应该不是实时的吧，应该是比如说一天的某一个时间统一做这件事」——判定与升级应该**离线批量**做。

先摆现状（都是刚查过的代码事实，不是推测）：

| 现状 | 证据 |
|---|---|
| 写入是**同步、逐轮**的 | `app/memory/manager.py::remember_explicit_preference` 每轮一次正则 + 一次写库；`_remember_successful_query` 现在也在 `_persist_report` 里同步写 |
| 分类器**已经写了，但接线时丢了** | `policy.extract_preference()` 返回 `MemoryEntry(memory_type=stable/temporary/insight)`；manager 里硬编码 `memory_type="stable_preference"`、`scope=USER`、无 `expires_at` → `entry.memory_type` 被丢弃 |
| `temporary_preference` **生产恒为空** | 全仓没有任何写入方产生它（只有 policy 分类 + 读取白名单提到它）。和 Query Memory 是同一型缺陷：类型存在、契约写了、生产没有 |
| 判据**语义错位** | `_TEMPORAL_KEYWORDS = ["昨天","今天","本月","最近",…]` 是**时间指示词**；判「是否长期」该看**作用域标记**（这次 / 先 / 暂时 / 本次 / 以后 / 默认 / 每次） |
| 没有准入闸 | 正则命中即写 `active`，一句「这次先按周看」与「以后都用柱状图」同权重、同生命周期 |
| 没有调度设施 | 全仓只有 `backend/app/infra/db/postgres.py::_pool_monitor_loop` 这种 asyncio 间隔循环先例，无 APScheduler/Celery |

上一轮刚修的两件事是本 plan 的前提：D4 给了**读取侧的 scope 过滤**（`_scope_filter`），
B 方案给了**热度只做同分裁决**（`scoring.rank_key`）。缺的正是**写入侧**：谁有资格成为长期记忆、
记给谁、记多久、什么时候才算数。

## Design

### D1 · 两条路径：热路径只存“证据”，判定放离线

| | 热路径（同步，每轮，零 LLM） | 固化（离线，每日一次） |
|---|---|---|
| 做什么 | 只做**确定且不能等**的写：显式长期偏好（「以后/默认/每次」）→ 直接 `active`；其余一律写 `candidate`（带原文、session、时间） | 读取窗口内的 candidate → **准入三闸** → promote / supersede / expire |
| 延迟 | 毫秒级（一条 UPDATE） | 分钟级，可跑在凌晨 |
| 为什么 | 用户说「以后都用柱状图」必须**当场**生效，等到明早就是坏体验 | 模糊/推断类需要**跨会话证据**，实时看不到上下文；而且判定要算分、要查重、要处理冲突，不该压在一次请求里 |

判据归属明确：**能一句话拍死的在热路径**（`以后|默认|每次|一直|都` + 偏好结构），**需要证据的进批处理**。

### D2 · 准入三闸（全部确定性，可测）

**闸 1 · 值不值得记（可复用性）**

| 记 | 不记（或只留 candidate） |
|---|---|
| 跨会话仍成立的偏好：图表形态 / 时间粒度 / 口径 / 单位 | 一次性任务参数：「看下 2024」「华东」（那是 Session State，不是记忆） |
| 稳定业务定义：「GMV 按净销售额算」 | 单条数据引用：「这个数字」「涨了多少」 |
| 用户的纠正：「不对，华南包含福建」 | 寒暄 / 感谢 / 情绪 |
| 成功 SQL 经验（D1 已接通） | 模型中推断的一切（§五 冻结：不进 active） |

**闸 2 · 记给谁、记多久**

| 话语标记 | 落库 | 说明 |
|---|---|---|
| 长期：「以后 / 默认 / 每次 / 一直 / 都」 | `scope=user`，`expires_at=NULL` | 热路径直接 active |
| 会话：「这次 / 先 / 暂时 / 本次 / 这一回」 | `scope=session` + `session_id` + `expires_at=now+24h` | **本轮就要补的缺口**：读取侧已按 scope 过滤，写入侧从不产生 |
| 都没有 | `candidate`，不写 active | 保守：不猜（与 §五「LLM inferred 不进 active」同一立场） |

**闸 3 · 与既有记忆什么关系**：同内容 → update（已有）；同维度（`app/memory/preferences.py` 的 chart/granularity）→ supersede（已有）；
语义近似但措辞不同 → **二期**（要 embedding 阈值，见「明确不做」）。

### D3 · 离线固化的幂等 / 预算 / 可观测

- **游标**：`memory.consolidation_cursor(user_id, last_window_end)`；窗口 = `last_window_end → now`，天然断点续跑。
- **幂等**：同一窗口重跑不得二次 promote —— 用「窗口结束时间 > 上次游标」+ 每条 candidate 的 `promoted_at`（或在 cursor 原子推进前先落 run 记录）保证。
- **预算**：单用户每次最多 `MEMORY_CONSOLIDATION_MAX_ITEMS`（默认 200）；整轮总时长 `MEMORY_CONSOLIDATION_BUDGET_S`（默认 120s）；超时保留游标下次续跑。
- **并发**：同用户串行（cursor 行 `SELECT ... FOR UPDATE`）；多进程同时跑由该锁自然互斥。
- **可观测**：`memory.consolidation_run`（started / finished / considered / promoted / superseded / expired / skipped / errors / dry_run）；
  每个 user 一次 trace span，失败只 warn 不中断整轮（与既有 best-effort 记忆写入一致）。
- **dry-run**：`--dry-run` 只算不写，用于上线前对比「会发生什么」。

### D4 · 调度：进程内循环 + CLI 双入口

- 进程内：复用 `_pool_monitor_loop` 的写法，在 lifespan 起一个每日定时器；**默认关**
  （`MEMORY_CONSOLIDATION_ENABLED=0`），开启时 `MEMORY_CONSOLIDATION_AT=03:00`（本地时区）。
  默认关的理由：开发机和 CI 不该悄悄跑批处理改写记忆库。
- 外部：`python -m app.jobs.memory_consolidation --date 2026-11-09 [--dry-run] [--user-id N]`，
  生产用 cron / 定时任务跑，进程重启不影响（游标在库里）。
- 两条入口共用同一个 `consolidate(user_id, window)` 函数，不写两份逻辑。

### D5 · 数据模型（最小改动）

- **复用** `memory.semantic_entry`：`status='candidate'` 就是候选池，`scope/session_id/expires_at` 已有，
  `promoted_at` 需新增一列（软迁移，`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`）。
- **新增两张小表**：`memory.consolidation_cursor`、`memory.consolidation_run`（字段见 D3）。
- 不新建「utterance 原文表」：candidate 行本身就是证据（content + source + session_id + created_at）。

### D6 · 与冻结契约的关系（需你点头）

§五 写明「LLM inferred 不进 active」。**批处理第一版不越这条线**：只做「显式类 candidate 的作用域/TTL 修正」
与「同维度冲突收敛」，不做「跨会话重复 → 推断升级」。
后者是真正的价值点（这才叫“学习”），但它要改写 §五 的冻结条款，必须单独批准——本 plan 只留接口位。

## Files to change

| 文件 | 改动 |
|---|---|
| `backend/app/memory/admission.py` | **新增**：作用域判定（闸 2）+ 可复用性判定（闸 1）+ L1 期限常量（纯函数，无 IO） |
| `backend/app/memory/consolidation.py` | **新增**：`consolidate(user_id, window, dry_run)` —— 读候选 → 三闸 → promote/supersede/expire → 落 run 记录 |
| `backend/app/jobs/memory_consolidation.py` | **新增**：CLI（`--date/--user-id/--dry-run`），生产 cron 入口 |
| `backend/app/memory/manager.py` | 热路径接线：用 `entry.memory_type` + `admission` 决定 `scope/expires_at/memory_type`；模糊类落 candidate |
| `backend/app/infra/memory/user_memory.py` | `promote_candidate()` / `expire_due()` / `list_candidates(since)`（窄接口，不暴露 SQL 给 domain） |
| `backend/app/main.py`（lifespan） | 可选的每日定时器（env 开关，默认关） |
| `backend/scripts/init_pg.sql` | `promoted_at` 软迁移 + `consolidation_cursor` / `consolidation_run` 建表 |
| `backend/tests/contracts/test_memory_admission.py` | 闸 1/2 判定矩阵（≥12 条中文说法） |
| `backend/tests/persistence/test_memory_consolidation.py` | 真 PG + fake embedder：晋级 / 过期 / 幂等重跑 / 断点续跑 / dry-run 不落库 |
| `docs/plans/README.md` | 登记本 plan |

## Reused existing utilities

- `MemoryPolicy.extract_preference` / `_classify`（`backend/app/infra/memory/policy.py`）：闸的输入源，**不重写正则体系**，只把它的输出真正用起来。
- `app/memory/preferences.py::preference_dimension`：闸 3 的「同维度」判定（已落地）。
- `UserMemory` 既有 `save / search / supersede_* / list_active_preference_contents`：写入与查询只走这些，不新开 SQL 路径（宪法 §6 网关）。
- `_pool_monitor_loop`（`backend/app/infra/db/postgres.py`）：进程内定时循环的写法范式。
- `evaluation/gold_memory_runner.py` 的报告范式 + `_verify_seed`：批处理跑完后同样产出可核对的数字，而不是只 print 一行。

## Verification

```bash
# 判定矩阵（纯函数，秒级）
python -m pytest backend/tests/contracts/test_memory_admission.py -q

# 固化行为（真 PG + fake embedder）
python -m pytest backend/tests/persistence/test_memory_consolidation.py -q

# 真机 dry-run（不写库），人工核对「会发生什么」
python -m app.jobs.memory_consolidation --user-id 1 --dry-run

# 真跑（本地先手动，生产交给 cron）
python -m app.jobs.memory_consolidation --user-id 1
```

验收矩阵：
1. 「以后都用柱状图」→ 当轮 active、`scope=user`、无 TTL（热路径，不等批处理）。
2. 「这次先按周看」→ 当轮 `candidate`；批处理跑完 → `active`、`scope=session`、`expires_at≈+24h`；换会话召回不到（D4 的 `_scope_filter` 已能保证）。
3. 「帮我查下 2024 销售额」→ 不产生 candidate（一次性任务参数），也不再污染长期记忆。
4. 同一窗口连跑两次 → 第二次 `considered=0 / promoted=0`，库内行数不变（幂等）。
5. 中断（`--max-items 1` 后再跑）→ 从游标续上，不丢不重。
6. 早于 `expires_at` 的 session 级条目在批处理里被 `expired`（不是条条都留）。

## Explicitly NOT doing

- **不做推断升级**（跨会话重复 → promote active）：要改 §五 冻结条款，单独批准后在 D6 的接口位上加。
- **不做语义近似去重/supersede**：需要 embedding 阈值与误杀评估，属于二期；本轮只做同内容 + 同维度。
- **不建 utterance 原文表**：candidate 行即证据，避免再开一张无限增长的表。
- **不引入调度依赖**（APScheduler / Celery / Redis）：asyncio 循环 + CLI 足够，且可测试。
- **不在热路径加 LLM 调用**：准入判定必须确定性；模型仍只能“提候选”。
- **不改召回侧**：读路径上一轮刚定稿（scope 过滤 + 语义带排序），本 plan 不动它。
- **不做多租户/跨用户批处理**：游标与预算都是 per-user。
