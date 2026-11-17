> 状态: 已完成（2026-11-17）

# 双项目 AI 评测平台（可实时跑、可看效果、可沉淀资产）

## Context

用户诉求（2026-11-17）：

> 「最好是沉淀下来然后弄个地方可以单独展示的，然后我的那个 ragent-py 那个项目是 rag 的，你想一下能不能两个综合一下，
> 写一个小的测试平台（既可以实时测试，也可以看到效果），最好是要全面一些，按照现在的 jd 要求来做。」

JD（AI 评测方向）要求：界定**被测对象/能力版本/场景/风险约束**、评测方案含**目标/指标/用例/覆盖/判定标准**、
AI 辅助但要**人工复核结论**、把一次性测试沉淀成**资产（来源/覆盖/版本/复用说明）**、
**边界案例库**（误答/幻觉/越权/错误执行/不可追溯）、**可追溯记录 + 证据分量**（Demo/PoC/测试环境/受限试行/正式运行）、
治理与发布（不替代门禁）。

现状盘点（已做，不是猜）：

| 项目 | 现有可复用资产 | 缺口 |
|---|---|---|
| ReportAgent | `evaluation/baseline_cases.json`（20 例 / 13 类 / 9 维字段期望）、`memory_gold_cases.json`（25 记忆 30 金标 query）、`runner.py`+`checker.py`+`schema.py`、`execution_accuracy.py`+`task_decomposition.py`、`results/*.json` 快照、`docs/p*-golden-before-after.md` | 无方法论文档、无资产登记、无证据分量表、无风险案例库索引、无统一入口 |
| ragent-py | `eval/sany_annual_reports/rag_testset.json`（65 题 / 10 类别 / **gold_documents + gold_evidence(keyword) + gold_judgment + gold_source=human_verified**）、`eval_results.json`、`eval_report.md`、`eval/README.md` | 同上；另外评测靠 LLM 裁判 0–3 分，成本高、不可离线复算 |

两边恰好互补：**ReportAgent 强在"确定性判定器 + 结构化期望"，ragent 强在"金标证据 + 拒答/纠偏/口径类风险题"**。

## Design

一个独立小服务（FastAPI + 单页前端，端口 **8200**），不改动两个项目本身：

```
eval_platform/
├── app.py                # FastAPI：/api/* + 静态页 + /api/docs/*
├── registry.py           # 资产登记与用例归一：两项目资产 → 统一 Case 视图（含来源/覆盖/版本/证据档位）
├── store.py              # SQLite：runs / run_items（可追溯记录，落盘可复查）
├── adapters/
│   ├── reportagent.py    # /health + 走 /api/v1/chat 取 requirement 事件（到需求卡为止）
│   └── ragent.py         # /health + login + /api/v1/retrieve（检索召回，不生成）
├── runners.py            # 调度 + 判定：把 Case + 观测 → pass/fail/deferred + 指标
├── assets/registry.yaml  # 资产登记（来源/覆盖/版本/复用方式/证据档位）
└── static/{index.html,app.js,style.css}
```

### 一、被判对象与"能判到哪一层"（诚实分级）

| 目标 | 实时动作 | 判定 | 证据档位 |
|---|---|---|---|
| RAG 检索 | `POST /api/v1/retrieve`（pin kb_ids） | **确定性**：`gold_documents` 命中 @k、`gold_evidence.keyword` 是否出现在返回 chunk | 受限试行（需真实 KB 已入库） |
| RAG 回答 | `POST /api/v1/chat/stream` | **启发式**：拒答题是否拒答、答案是否含 gold keyword | 受限试行（启发式，不当结论） |
| ReportAgent 需求理解 | `POST /api/v1/chat` → 取 `requirement` 事件 | **确定性**：与 case 期望字段比对（status/time_range/scope/dimensions 子集） | 测试环境 |
| ReportAgent 执行/报告 | 需 confirm + 真 SQL（慢） | **本轮 deferred**（页面明确显示"未覆盖"，不算 pass） | — |

「未覆盖」必须显式展示——这是 JD 第 5 条"说明证据状态"的落地，也是这个平台唯一值得信的地方。

### 二、判定标准三层（写进 `docs/evaluation/methodology.md`，页面可读）

1. **确定性断言**：结构、字段、集合、数值容差（`NULL≠空串≠0`）、doc_id/keyword 命中；
2. **金标比对**：现跑金标 SQL / 现查金标 doc_id（数据变了结论跟着变，不写死数字）；
3. **启发式/评审**：拒答、纠偏语义——只能标记为 "advisory"，**不进 pass 率**。

### 三、资产登记（JD 第 3 条）

`assets/registry.yaml` 每份资产一行：`id / 项目 / 路径 / 用例数 / 覆盖 / 来源 / 标注方式 / 版本(commit+日期) / 复用方式 / 证据档位 / 维护成本`。
页面「资产」页直接渲染这张表 —— 交接时看这一个文件就够。

### 四、风险案例库（JD 第 4 条）

从两边资产**自动派生**，不手写第二份：
- ragent 的 `常见错误答案` + `考察的RAG易错点` → 误答/幻觉/口径类；
- ragent 的 `gold_judgment=not_answerable` → 幻觉（拒答边界）；
- ReportAgent 的 plan 与测试里五类真实案例（1.84 亿 vs 4784 万、NULL 分组当冠军、越权 404、折叠过大…）→ 静态条目。

### 五、可追溯 + 证据分量（JD 第 5、6 条）

每次运行落 SQLite：`run_id / 起止时间 / 目标 / 环境(base_url+KBs) / 代码版本 / 用例集版本 / 每条结果 / 判定层 / 证据档位`。
页面「运行详情」给出：这条结论**在什么环境、什么版本、覆盖了几层、哪几层没覆盖。**

### 六、AI 辅助 + 人工复核（JD 第 2 条）

平台不调用 LLM 生成结论；只做**确定性判定 + 启发式标记**，并在 UI 上把两者视觉分开（`advisory` 灰标）。
「AI 辅助」体现在资产侧的标注来源字段（`gold_source: human_verified_2026-08-23` / `original_unverified`）——
页面按 `gold_source` 过滤，一眼看出哪些题是人工核过的。

## Files to change

| 文件 | 变更 |
|---|---|
| `eval_platform/app.py` | **新增**：FastAPI（健康/资产/用例/运行/历史/文档） |
| `eval_platform/registry.py` | **新增**：两项目资产加载 + 用例归一 + 风险派生 |
| `eval_platform/store.py` | **新增**：SQLite 运行记录 |
| `eval_platform/adapters/{reportagent,ragent}.py` | **新增**：HTTP 适配（含优雅降级） |
| `eval_platform/runners.py` | **新增**：调度 + 判定 |
| `eval_platform/static/*` | **新增**：单页 UI（总览/实时测试/资产/风险库/历史/方法） |
| `eval_platform/tests/test_platform.py` | **新增**：资产加载、判定、降级 |
| `eval_platform/README.md` | **新增**：怎么起、怎么加用例 |
| `docs/evaluation/*.md` | **新增**：methodology / asset-registry / risk-case-library / evidence-weights（页面渲染） |

## Reused existing utilities

- ReportAgent：`evaluation/baseline_cases.json`、`evaluation/checker.py` 的维度词汇（只读引用，不改其代码）
- ragent-py：`eval/sany_annual_reports/rag_testset.json`、`eval_results.json`
- 既有 HTTP 契约：ReportAgent `POST /api/v1/chat`（SSE）、`GET /health`；ragent `POST /api/v1/auth/login`、`POST /api/v1/retrieve`、`GET /health`

## Verification

1. `pytest eval_platform/tests -q`（不依赖外界服务：适配器用假实现）
2. 起平台：`python -m uvicorn eval_platform.app:app --port 8200` → `GET /api/health` 返回两个被评测服务的可达状态
3. 两个服务都不启动时：页面不报错，显示「服务未启动 / 只能看历史资产」
4. RAG 服务在跑时：跑一道 `gold_documents` 非空的题 → 展示 top-k chunk + `doc_hit@k` + 证据档位
5. ReportAgent 在跑时：跑一条 baseline case → 展示 requirement 卡与字段级 diff
6. Playwright 截图（页面在本机跑起来的样子）

## Explicitly NOT doing

- 不做 LLM 裁判跑分（成本不可控，且 JD 要"人工复核"）——金标/启发式够用；
- 不改两个项目的评测代码（平台是只读消费者）；
- 不做用户体系/权限（本机展示用，JD 的"越权"是用例维度不是平台功能）；
- 不做自动回归看板（历史快照已能对比，先把手动跑通）。


## 验证结果（2026-11-17）

**自测**：`python -m pytest eval_platform/tests -q` → **16 passed**（资产加载 21/65、证据档位、五类风险、
三层判定、SSE 解析、HTTP 面）。

**真机（ReportAgent 后端在跑、ragent 未启动）**：

```
GET /api/health      → reportagent.up=true, ragent.up=false（页面显示「未启动」，不是 0 分）
GET /api/overview    → RA 21 用例 / RAG 65 用例 / 资产 4 份 / 风险 46 条 / 人工核验 15 题 / 金标文档 64 题
                       RAG 历史快照：65 题均分 2.23/3（归一 74.4%），标注「仅参考」
POST /api/run?case_id=explicit-region-sales-ranking
  → verdict=pass，3 条 deterministic check 全 pass
    （status=complete · target_metrics=[销售额] · time_range=2024年）
    延迟 9.3s / 19.6s（整轮需求理解，真 LLM）
```

**Playwright（1440×980）**：总览 / 实时测试 / 风险库 / 方法 四页截图，**0 console error**；
`.e2elogs/eval-platform-{overview,live,risk,method}.png`。

**过程中修掉的三个真问题**（都是真机才暴露的）：

1. `/api/v1/chat` 要求 JWT → 平台补登录（默认 admin/admin123，env 可覆盖）；
2. SSE 帧用 **CRLF** 分隔 → 不归一就只能解析出最后一个 `done` 事件（需求卡永远为空）；
3. 按行 `break` 会切在 `event: requirement` 与 `data:` 之间 → 改成整条流读完再解析。
4. 指标匹配最初用**子串** → `净销售额` 会被当成 `销售额` 判 pass（正好复现 RA-R1 那个缺陷）→ 改为归一后**等值**比较；
   跨文档召回阈值从 0.5 抬到 0.67（命中一半对跨文档题太弱）。

**未做**：ragent 未启动，检索层 live 未跑（适配器与判定已就绪，启动 ragent 后可直接点）。


## 第二轮：RAG 细节与场景测试（2026-11-17，用户：「rag 系统也要多测试，测测增量更新这些小功能」）

**启动 ragent 后**（`:8000`，与 ReportAgent 共用同一个 PG：`public.documents/chunks/knowledge_bases`）：

| 项 | 结果 |
|---|---|
| 生产库 | 3 个 KB：三一重工年报（3 PDF / 1381 chunks）/ 数据字典 / FAQ |
| RAG 用例 live | Q01 / Q26 / Q56 **全 pass**（doc_hit + evidence_keyword_hit；answer 层 deferred） |
| 场景 1 增量更新 | **pass**（297s）：同一 `document_id` 覆盖 → 文档数仍为 1、新 token 可召回、旧 token 不再召回 |
| 场景 2 删除后无脏召回 | **pass**（153s）：删后列表清空、检索不返回该文档 |
| 场景 3 跨库隔离 | **pass**（146s）：pin 生产库检索不返回临时库文档 |
| 自测 | **20 passed**（新增场景注册表/`_hit`/`_index_problem`/timeout verdict 4 例） |

### 这轮撞出来的四个真问题

1. **资产重挂（最重要）**：`gold_documents` 是 2026-08 首次入库的 document_id，重新入库后变成
   `e15c…/61be…/a005…` → 不重挂就**全量假失败**。落法：`eval_platform/assets/rag_doc_map.json` +
   `registry.map_gold_documents()`，页面注明「已按 rag_doc_map 重挂」；纪律写进 `asset-registry.md`。
2. **ragent 登录限流**：10 次 / 5 分钟 / IP。平台每次调用都登录 → 直接 429。修：进程内 token 缓存（9 min）
   + 支持 `RA_TOKEN` / `RAG_TOKEN` 注入。
3. **入库很慢**：3 行 markdown 从上传到 `indexed` 实测 **141s**（分块 + embedding）→ 场景等待上限 90s 太短，
   第一次三个场景全 fail。修：上限 300s，且把「入库超时」记成 `timeout`、verdict 记 `error`
   （**工具上限 ≠ 产品失败**）。
4. **`_wait_indexed` 的文档列表过滤没问题**（`GET /documents?kb_id=` 实测只返回本库），最初怀疑过滤失效是误判，
   已在文档里写清楚，避免下次再怀疑错地方。

### 对外展示页

- `eval_platform/export_snapshot.py`：从平台 API 导出**自包含静态页**（资产/场景/风险库/最近运行/快照/方法摘要）
  → 博客 `source/eval/index.html`（25.5 KB）+ `eval_platform/assets/snapshot.json`（可入库的快照）；
- 博客：`skip_render: ['graph/**','eval/**']`、about 增「评测平台」小节、新文章 `eval-platform.md`
  （项目深挖的横向篇：讲三层判定 / 资产重挂 / 超时与失败的区分）。


## 事故与恢复（2026-11-17，必须留在档）

**发生了什么**：平台的场景测试清理逻辑（`_drop_kb`）按知识库列文档再逐个删除。但
`GET /api/v1/documents` **不接受 `kb_id` 过滤**（签名只有 `limit/offset`，admin 会拿到全库），
于是"删临时库的文档"变成了**删全库**：14 份文档 / 1391 chunks（三一重工 3 份年报 + 数据字典 11 份 + FAQ）全部被删。

**为什么没马上发现**：第一次跑场景时它把"文档数=16（期望 1）"报成失败，我把它当成"入库慢/未就绪"的噪音，
继续修等待上限——**这条告警其实就是事故本身**。教训：当断言数字和预期差一个数量级时，先查是不是作用域错了。

**影响面**：ragent 的知识库（`knowledge_bases` 3 行）与 ReportAgent 的会话/报告未受影响；
受影响的是"文档 → chunk → embedding"。三一 65 题评测、数据字典检索在恢复前不可用。

**修复**：
1. `adapters.ragent_docs(kb_id)` 改为**客户端过滤**（`kb_id` 不匹配的一律不收），注释写明事故原因；
2. `_drop_kb` 只删"本库的 document_id"；
3. 新增回归测试 `test_ragent_docs_filters_client_side`（构造一条别库文档，断言不会被收进列表）。

**恢复**：用项目自带灌库路径重建，不手改数据库：
- 数据字典：`mcp_server.server.cmd_ingest_table_schemas`（自省 PG → render → upload，幂等）；
- FAQ：`cmd_ingest_faq`（`mcp_server/faq_seed.json` 20 条）；
- 三一年报：`adapters.ragent_upload` 重传 3 份 PDF（`eval/sany_annual_reports/*.pdf`）。

**恢复后必须做的事**：document_id 会变 → 重建 `assets/rag_doc_map.json`（资产重挂），否则 65 题 doc_hit 全假失败。


## 事故恢复的运维发现（2026-11-17 晚，写进文档免得下次再踩）

1. **入库是串行队列，且大文件会饿死后来的小文件**：先用 MCP `ingest_table_schemas` 灌 14+ 份字典文档
   （每份 wait_indexed 串行），结果 3 份年报 PDF 卡在 `processing/indexing` 一个多小时，
   `chunk_count=0`。处置：停掉串行灌库脚本 → 重启进程（BackgroundTasks 随进程丢失）→ 清掉 17 份
   `indexing` 孤儿文档 → 只重传 3 份 PDF。**教训：批量灌库与"立刻要结果"的评测不能并行。**
2. **PDF 入库真正的瓶颈是 OCR**：日志里 `OCR on page.number=80/81` + RapidOCR，
   250 页 × 3 份 + 每批元数据 LLM 调用 → 分钟级到十分级。任何"上传后 N 秒断言可召回"的用例都是错的。
3. **重启会留下 `indexing` 孤儿**：进程内 BackgroundTasks 被杀后文档状态不会回滚，
   `documents.status` 永远是 `indexing`。恢复动作必须包含「清 stuck 文档」，否则队列看起来永远在忙。
4. **ragent 的 `GET /documents` 加了 `kb_id`（上游修复，见 `acsRef/Rag` `3630539`）**：
   平台侧的客户端过滤保留（双保险），但真正的用途是让"按库统计/清理"不再依赖拿全库。


## 第三轮：恢复后全量重跑 65 题（2026-11-17 夜，最终数字）

恢复完成后**重跑全部 65 题**（不是抽查），逐条落 `runs.sqlite`。这一轮同时抓到了两个「平台自己的错」，
所以结论要按修正后的口径读：

| 阶段 | pass | fail | deferred | 说明 |
|---|---|---|---|---|
| 首跑（阈值未修） | 56 | 8 | 1 | 8 条 fail 里 4 条是判定器 bug |
| 修正阈值后重跑非 pass 项 | **60** | **4** | **1** | 60/65 = 92.3%，deferred 不算通过 |

### 四条真 fail 的归因（都是可复现的真问题，不是环境噪音）

| 用例 | 现象 | 根因 | 归属 |
|---|---|---|---|
| Q11 | `doc_hit@8` 3/3 命中，但金标关键词 `25,930` 不在召回文本里 | 该关键词来自**员工情况表**，与「主要会计数据单位」无因果关系 → 金标证据本身不成立 | 资产缺陷（EV-R4） |
| Q25 | 2025 年题，金标文档（2025 年报）在 k=5/8/12 **完全不出现**；top-k 全是 2024/2023 年报 | 元数据 LLM 关闭后 `chunks.year` 全为 NULL，跨年消歧退化；三份年报的同类表格文本高度相似，2024 版本排序压过 2025 | 产品行为回退（EV-R5） |
| Q50 | 金标 2 篇，k=8 只中 1 篇（k=12 才 2/2） | 跨文档召回对 k 敏感；8 条里后补的文档排在 9–12 位 | 检索质量（已知局限） |
| Q62 | 金标 2 篇（2025+2023），k=8/12 只中 2025 一篇 | 同 Q50，且 2023 篇从未进入 top-12 | 检索质量（已知局限） |

> **EV-R3 是最值钱的一条**：8 条 fail 里 4 条是判定器自己算错（阈值 `0.67` 够不到 `2/3`），
> 如果只看 verdict 汇总，会得出"恢复后检索整体变差"的错误结论。**判定器必须先被验证。**

### `chunks.year` 为 NULL 的实测证据

```sql
SELECT d.filename, c.year, count(*) FROM public.chunks c
JOIN public.documents d USING(document_id) GROUP BY 1,2;
-- 三份年报 436/471/474 chunks，year 全为 NULL（字典/FAQ 同样）
```

ragent 里 `year` 是 `conflict_key` 的 period、`evidence.coverage_by_year`、跨年冲突检测的输入；
NULL 的语义是「不可比」，于是这些质量特性静默退化——**没有报错，只是不再生效**。
这也是为什么这一轮的 Q25 值得留档：它是「为了快关掉开关」这件事的**代价凭证**。

### 平台自身的两条修正

1. `runners.py`：跨文档召回阈值写成真分数 `CROSS_DOC_RECALL_MIN = 2/3`（原 `0.67` 使 2/3 永远 fail），
   并加回归测试 `test_recall_two_of_three_passes`；
2. `registry.py`：把评测工具自身事故（EV-R1…EV-R5）纳入平台风险库（`_EVAL_TOOL_RISKS`），
   风险库从 46 → **51** 条——工具自己的坑必须出现在被展示的清单里，而不是只躺在 markdown。

自测：`python -m pytest eval_platform/tests -q` → **22 passed**。
