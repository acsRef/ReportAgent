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
