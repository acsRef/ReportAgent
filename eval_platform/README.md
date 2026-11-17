# AI 评测平台（ReportAgent × ragent-py）

一个**只读**消费两个项目评测资产的独立小服务：实时跑用例、看判定过程、看历史证据。

> 为什么单独一个服务：ReportAgent 是 Agent Runtime、ragent-py 是 RAG Runtime，各有自己的 venv 与依赖；
> 平台只经 HTTP 调它们的公开接口，不 import、不改代码。

## 起

```bash
# 依赖：两个被评测服务按需启动（不起也能看资产/历史/文档）
# ReportAgent:  backend/ 下 uvicorn app.main:app --port 8100
# ragent-py:    ragent-py/ 下 python -m app.main（默认 :8000）

cd D:/PyProject/ReportAgent
$env:PYTHONPATH='D:\PyProject\ReportAgent'
D:\miniConda\envs\agent\python.exe -m uvicorn eval_platform.app:app --host 127.0.0.1 --port 8200
# 打开 http://127.0.0.1:8200/
```

自测（不依赖上面两个服务，跑得快）：

```bash
python -m pytest eval_platform/tests -q      # 16 passed
```

## 页面 ↔ JD 条款

| 页面 | 内容 | 对应 JD |
|---|---|---|
| 总览 | 被测对象 / 能力版本（repo commit）/ 覆盖 / RAG 历史快照（标注"仅参考"） | 界定被测对象与风险约束 |
| 实时测试 | 选用例 → 跑 → 逐条 check（layer + 期望/实测）、延迟、覆盖/未覆盖 | 目标·指标·用例·判定标准；可追溯 |
| 资产 | 来源 / 覆盖 / 版本 / 复用方式 / 证据档位 | 测试资产沉淀 |
| 风险库 | 误答 / 幻觉 / 越权 / 错误执行 / 不可追溯 | 边界案例库 |
| 历史 | 每条运行的环境指纹 + 判定层 + 档位 | 证据状态与分量 |
| 方法 | 方法论 / 资产登记 / 风险库 / 证据分量（`docs/evaluation/*.md` 原样渲染） | 评测方案与方法论 |

## 关键设计（也是它唯一值得信的地方）

1. **判定分三层**：`deterministic` / `gold` 进 pass 率；`deferred` **不算通过**，页面显式标「未覆盖」。
2. **金标不写死数字**：ragent 用 `gold_documents`（doc_id）+ `gold_evidence.keyword`；
   ReportAgent 的执行层用金标 SQL 现跑（在它自己的 runner 里）。
3. **指标等值而非子串**：`销售额` 与 `净销售额` 是两个口径，子串匹配会把「口径被换」判成 pass。
4. **服务不可用 ≠ 失败**：返回 `unavailable` 并在页面提示"服务未启动"，绝不显示 0 分。
5. **每条记录带环境指纹**：`base_url` + 两项目 commit + 资产清单，回答"这条结论在什么环境成立"。

## 加一个新用例

| 项目 | 怎么做 |
|---|---|
| ragent | 往 `rag_testset.json` 加一题，**尽量补 `gold_documents`**；核验过就把 `gold_source` 写成 `human_verified_<日期>` 并补 `gold_evidence.keyword` |
| ReportAgent | 往 `evaluation/baseline_cases.json` 加一例（schema 冻结，只加 JSON） |

平台按文件内容实时加载，不用改代码。

## 目录

```
eval_platform/
├── app.py            # FastAPI：/api/* + 静态页 + 文档
├── registry.py       # 资产登记与用例归一（含证据档位）
├── runners.py        # 调度 + 三层判定
├── adapters.py       # 两个项目 HTTP 适配（优雅降级）
├── store.py          # SQLite 运行记录
├── static/           # 单页 UI（无构建、无依赖）
└── tests/            # 16 例自测
```

设计文档：`docs/plans/2026-11-17-eval-platform.md`；
方法论/资产/风险/证据：`docs/evaluation/{methodology,asset-registry,risk-case-library,evidence-weights}.md`。
