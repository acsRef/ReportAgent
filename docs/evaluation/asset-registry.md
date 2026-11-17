# 资产登记（Asset Registry）

> 平台「资产」页直接渲染这张表。**登记不手抄**：字段由 `eval_platform/registry.py` 从资产文件本身
> 与仓库 commit 现算，避免"登记表和资产漂移"。

## 登记字段（每份资产必填）

| 字段 | 为什么需要 |
|---|---|
| `id` / `path` | 唯一定位 |
| `cases` | 规模（并与覆盖范围对得上） |
| `coverage` | 测了哪个能力面的哪些维度 |
| `source` | 谁产出的：人工设计 / 真实文档 / 历史快照 |
| `labeling` | 标注方式：手写期望 / 金标 SQL 现跑 / 人工核验子集 / LLM 裁判 |
| `version` | 仓库 commit + 读取日期，结论可绑定版本 |
| `reuse` | 别人怎么复用它（命令或代码路径） |
| `tier` | 证据档位（见 `evidence-weights.md`） |
| `maintain` | 加用例的成本 / 注意事项 |

## 当前资产

| id | 项目 | 用例 | 覆盖 | 标注 | 档位 |
|---|---|---|---|---|---|
| `ra-baseline` | ReportAgent | 21 | 需求/执行/报告/行为 4 段，9 维字段期望 | 手写 expectations + 金标 SQL 现跑 | 仅参考 |
| `ra-memory-gold` | ReportAgent | 25 记忆 / 30 金标 query | 记忆召回：必中 / 禁召 / 保守空召回 | 手写 gold + forbidden | 仅参考 |
| `rag-sany` | ragent-py | 65 | 10 类别（A–J），64 题带 `gold_documents`，15 题人工核验带 `gold_evidence` | `gold_source` 区分 `human_verified_*` / `original_unverified` | 受限可用（人工核验） |
| `rag-eval-snapshot` | ragent-py | 65 | 历史跑分（LLM 裁判 0–3） | LLM 裁判，未逐题复核 | 仅参考 |

## 维护规则

1. **加用例**：改资产 JSON 即可，不动 schema（`evaluation/schema.py` 冻结）；
2. **核验一题**：把 `gold_source` 从 `original_unverified` 改成 `human_verified_<日期>`，
   并补 `gold_evidence.keyword`（平台会用关键词做检索命中判定）；
3. **删用例**：只允许在覆盖率不下降时删；删了要在 commit message 写明原因；
4. **版本绑定**：快照类资产（`eval_results.json`）不可重算，登记时必须标"快照"并注明生成时间；
5. **不要写死数字**：金标 SQL / doc_id 可以写，金额、行数不许写死在断言里——直接跑出来比。

## 缺口（下一轮该补的资产）

| 缺口 | 影响 |
|---|---|
| ReportAgent 执行/报告的平台内用例（现依赖其自身 runner） | 平台只能测到需求层 |
| ragent 生成层的人工核验子集（现在只有 LLM 裁判快照） | 拒答/纠偏类只能 deferred |
| 标注一致性（同一题两人标、算 IAA） | 单人标注，无法证明标注可靠 |
| 长会话 / 并发场景资产 | 多 Agent 并发协作无覆盖 |
