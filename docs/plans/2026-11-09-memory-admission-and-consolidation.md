> 状态: 已完成

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

## 落地记录（2026-11-09）

按 D1–D5 实施完毕，真机 demo（临时用户，跑完即删）验证了整条链：

```
热路径写完：
  以后都用柱状图显示   stable_preference  active  user     无期限
  这次先按周看        temporary_preference active  session  24h ← 此前永不产生
  以后都用饼图显示     insight           candidate user     （推断类）
  柱状图挺好的         insight           candidate user     （无作用域标记）
dry-run：considered=2 promoted=1 skipped=1（只算不写）
真跑：  promoted=1 superseded=1 skipped=1
  柱状图  → superseded（被同维度新偏好取代）
  饼图    → stable_preference active user（晋级，promoted_at 落章）
  挺好的  → 仍是 candidate（§五：推断类不晋级）
```

### 与 plan 的三处偏差（都写清理由）

1. **会话标记当轮 active，不等批处理**（plan 原文写「先 candidate 再由批处理晋级」）。
   改了：用户说「这次先按周看」就是要**现在**生效，等到明早等于没说。
   判据依然是确定性的（会话标记词），所以「能一句话拍死的留在热路径」这条原则没破；
   真正需要证据的（无标记/推断）才进候选队列。
2. **候选用「队列」而不是「时间窗口」选活**（`status='candidate' AND promoted_at IS NULL`）。
   时间窗口在断点续跑时容易漏边界行；队列天然可重跑。`consolidation_cursor` 保留，
   但只用来记录「上次成功跑到何时」，供运维看批处理落后多久。
3. **闸 1 只做最小版**（空 / 纯寒暄 / 过短）。完整的「一次性任务参数不记」拒绝词表有误杀风险
   （会把该记的口径丢掉），仍等用户过词表；现在结构位已经在 `is_admissible()` 里。

### 实施中抓到的真缺陷（比计划更靠前的一环）

第一版跑完 demo 发现「这次先按周看」**根本没落库**——不是准入闸没写好，是
`MemoryPolicy.extract_preference` 的正则只认「以后/默认/每次」和「我喜欢…图表/格式」，
会话级说法在**抽取阶段**就返回 None，准入闸根本没机会执行。
补了第三条正则（`这次/本次/暂时/临时/先 + 显示|用|按|以`），并在
`tests/contracts/test_memory_admission.py` 加了回归钉：
**抽取与准入必须同时命中**，只测其中一半会漏掉这类「上游先死」的问题。

### 落地文件

| 文件 | 内容 |
|---|---|
| `backend/app/memory/admission.py` | `classify_scope`（三档）+ `is_admissible`（最小闸 1）+ `DEFAULT_SESSION_TTL_HOURS=24` |
| `backend/app/memory/manager.py` | `remember_explicit_preference_plan`（纯函数，可单测）+ 接线（`expires_at`/`scope`/`status` 由准入结论决定）；只有 user 级才做 supersede（session 级不该顶掉长期偏好） |
| `backend/app/memory/consolidation.py` | `consolidate(user_id, dry_run, max_items)`：到期淘汰 → 候选晋级 → 同维度收敛 → 记账 |
| `backend/app/infra/memory/user_memory.py` | `list_candidates` / `promote_candidate` / `expire_due` / `list_active_preference_rows` / `supersede_ids` / 游标 / run 记账（全部窄接口） |
| `backend/app/infra/memory/memory_manager.py` | 上述能力的公共网关（domain 不碰 UserMemory 私有成员） |
| `backend/app/jobs/memory_consolidation.py` | CLI：`--user-id/--all-users/--dry-run/--max-items`；真实库上 `--all-users --dry-run` 可跑 |
| `backend/scripts/init_pg.sql` | `semantic_entry.promoted_at`（软迁移）+ `consolidation_cursor` + `consolidation_run` |
| `backend/tests/contracts/test_memory_admission.py` | 25 例：三档判据矩阵 + 抽取/准入双命中 + 闸 1 拒绝 |
| `backend/tests/persistence/test_memory_consolidation.py` | 8 例：晋级（user/session）/ 无标记留队 / 过期退役 / 幂等重跑 / dry-run 不落库 / 同维度收敛 / `max_items` 分两轮续跑 |

回归：`pytest backend/tests/contracts` **889 passed**；`pytest backend/tests/persistence -k "memory or consolidation"`
**64 passed**；`-k "memory or recall or context or preference or scoring or consolidation or admission"` **262 passed**。

### 仍未做（与本 plan 的 Explicitly NOT doing 一致）

- 推断升级（跨会话证据 → active）：要改 §五 冻结条款，等你批准。
- 闸 1 完整拒绝词表：等你过一遍（尤其“一次性任务参数”这一条）。
- 语义近似 supersede（措辞不同但同义）：要 embedding 阈值与误杀评估。
- 进程内每日定时器：CLI 已验证可用，进程内循环留到你确认部署形态后再开（默认关）。
## 追加（2026-11-09 晚）：D6 证据升级 + 闸 1 完整表 + 手动验证脚本

用户拍板：定时器先不做；**把另外两件（推断升级、闸 1 完整词表）做掉，并专门写个测试用的脚本**。

### ① D6 证据升级（修订 §五，已写进 CLAUDE.md §6 与 memory-architecture.md §五）

- 新增列 `semantic_entry.evidence_count`（默认 1）：写侧「同内容更新」时 +1。
  它与 `access_count` 分工不同——后者是**召回热度**（读侧），前者是**写入侧证据**（同一说法被说了几次）。
  不加这一列就永远数不出「这条被说过 3 次」：写侧去重是同内容 UPDATE，不新增行。
- 批处理规则：无作用域标记的 candidate 若 `evidence_count >= min_evidence`（默认 3）→
  晋级 `active` / `user` / `stable_preference`，confidence = **medium**（新增 `CONFIDENCE_EVIDENCE`），
  `high` 永远只留给用户明确说的那句。证据不足 → 继续留队。
- 为什么证据够了才升级：一次 LLM 抽取的可靠性远低于用户明说；重复出现是**唯一**能低成本拿到的
  跨会话佐证。这条把 §五 从「inferred 永不 active」放宽成「有证据可以，但置信度封顶 medium」。

### ② 闸 1 完整拒绝表（`app/memory/admission.py`，已加 11 条样本测试）

| reason | 命中示例 | 为什么 |
|---|---|---|
| `task-parameter` | 「看下 2024 年的销售额」「查一下 3 月的明细」 | 一次性任务参数属于 Session State，不是记忆 |
| `deictic-reference` | 「这个数字」「刚才那个结果」 | 单条数据引用，没有跨会话价值 |
| `question` | 「为什么跌了」「能不能按周看」 | 提问不是事实 |
| `greeting` / `too-short` / `empty` | 「你好」「看图」 | 无信息量 |

反例测试同时钉住**不能误杀**的四句（「以后都用柱状图显示」「这次先按周看」「净销售额是扣退款后的口径」「默认按月的粒度来」）——
误杀的代价是「该记的口径永远丢了」，比多记一条贵得多。

### ③ 专门的手动验证脚本（用户要的“测试用”）

`evaluation/memory_consolidation_scenario.py`：把一段**三天三会话**的对话序列灌进临时用户，
打印「原话 → 准入判定 → 落库形态」，再跑 dry-run 与真跑，最后打印固化前后对照。默认跑完即删，
`--keep` 保留、`--plan-only` 只演不写、`--min-evidence` 调阈值。

真机输出（节选）：

```
原话                  入口      准入  理由             落库形态
以后都用柱状图显示       explicit  记    ok              active/user
看下 2024 年的销售额    explicit  不记   task-parameter  —
这次先按周看            explicit  记    ok              active/session/24h
净销售额是扣退款后的口径  inferred  记    ok              candidate/user（×3 → 证据=3）
柱状图挺好的            inferred  记    ok              candidate/user（证据=1）
为什么华东掉得这么快      explicit  不记   question        —

dry-run：considered=2 promoted=1 skipped=1
真跑：  promoted=1 → 净销售额… 晋级 active/user，confidence=medium（证据 3）
        柱状图挺好的 仍是 candidate/low
```

### 回归

`pytest backend/tests/contracts backend/tests/persistence -k "memory or admission or consolidation or recall or scoring or preference"`
→ **192 passed**；单独两个新文件：准入矩阵 **36 passed**、固化行为 **10 passed**。

### 更新后的「仍未做」

- 语义近似 supersede（措辞不同但同义）：要 embedding 阈值与误杀评估。
- 进程内定时器：按用户要求暂不做，部署侧用 cron 调 CLI。
- 证据口径目前只按 `content` 精确聚合：换个说法说三遍不会累加（这正是语义 supersede 的同一块地基）。