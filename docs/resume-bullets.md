# Resume Bullets（简历草稿 → 用户改写）

> 使用方式：先读 [interview-talk-track.md](interview-talk-track.md) 五大叙事，再回来看本稿——每条 bullet 都能展开成 2-3 分钟叙事，且全部有证据锚点。
> 数字口径：backend 1182 passed / 1 skipped、frontend 301 passed、evaluation 78 passed（2026-09-07 基线）。

---

## 版本 A（项目经历，5 条，每条 2-3 行；适合「项目经验」段）

**ReportAgent｜Stateful Agentic Data Analysis Workbench**（个人项目；主导架构与全栈实现；Python/FastAPI/LangGraph/React/PostgreSQL）

1. **Agent 与 Workflow 边界**：Graph 编排「需求理解→澄清确认→SQL 生成/校验/修复→报告」全链（需求/执行/报告三 Agent 职责互斥）；把 Agentic 不确定性决策（是否澄清/调哪个工具/修还是停）与确定性骨架（状态机/预算/校验/报告契约）分开——执行失败走 Evaluate→Diagnose→Route 决策闭环，按错误 8 类分类 + 固定预算（SQL 2/MCP 2/LLM 2）修复，不盲重试、不无限循环。

2. **Memory 做成可评测的系统，不是功能**：四类记忆（会话/对话/语义/查询）分层；Selective Recall 四触发 + Write After Reliable Event（显式偏好→active、LLM 推断→candidate 不召回、SQL 校验+执行成功才进查询记忆）+ Agent 档位分流（SQL 上下文无图表偏好；Report 无查询记忆）+ Agent 层隔离（candidate/expired/跨用户不进入任何 Agent 上下文）。过程中发现并修复 2 个潜伏语义 bug（显式偏好写入口无生产调用者——UI 偏好从不生效；重申偏好后 active 丢失）。

3. **五层 Memory 评测 + 一个真实回归案例**：persistence→policy→retrieval→真指标→Agent 行为。自建 Gold Set（25 记忆 + 30 金标 Query，离线 30/30 确定性通过）；真 embedding 指标 Recall@1 0.84 / Recall@3 0.96 / MRR 0.90 / Clean 1.0；真 LLM 行为门（env-gated）抓到 Memory→SQL 真实回归：Query Memory 参考帧的**年份 anchor 诱导时间语义偏移**（「去年」被算成 2023，ON/OFF 归因实验证实因果），在上下文边界加「时间以本 prompt 声明的今天为准」声明修复——偏好→报告走确定性纯函数（词表交集 + dims 归一），不塞 prompt 让 LLM 猜。

4. **可靠性工程**：SQL 错误 8 类分类（对象不存在/语法/超时/权限…）+ 可恢复性双表决策（Agent 修复 vs 用户可重试）；MCP 失败显式 unavailable（不默默返回空数组）；执行三态（SUCCESS/EMPTY/FAILED）全部落库，报告永不伪造成功——数据真实性由三层 Validator 钉住（结构/KPI 聚合重算/行存在性）；Trace 双 sink（PG + Langfuse）全链路可定位失败层。

5. **工程闭环**：1180+ 后端 / 301 前端 / 78 eval 测试全绿；Playwright Contract E2E（mock LLM + 真 PG）入 CI per-PR；15 个 Phase 全部 plan 驱动（文档+Golden Set Before/After 防回归）。

---

## 版本 B（精简 3 条，适合空间紧张）

1. 全链 Agent 工作台（NL→确认→SQL→报告，LangGraph+FastAPI+React SSE v2）：Agentic 决策与确定性骨架分离；SQL 修复环 Evaluate→Diagnose→Route + 8 类错误分类 + 固定预算，不盲重试。
2. Memory 四类分层 + 选择性召回 + Agent 层隔离；五层评测（含自建 Gold Set 30/30 + 真 embedding Recall@1 0.84/Recall@3 0.96/MRR 0.90）；真 LLM 门抓到参考帧年份 anchor 导致的时间语义回归（ON/OFF 归因 + 边界声明修复）——偏好走纯函数，不塞 prompt。
3. 可靠性：SQL 三态全落库 + 报告三层 Validator（数据永不伪造）+ Trace 双 sink（PG+Langfuse）；1180+ 后端/301 前端/78 eval 全绿，Contract E2E 入 CI。

---

## 关键词标签（技能区/项目标签用）

`LangGraph` `FastAPI` `PostgreSQL+pgvector` `React 19 + Vite` `SSE` `Playwright` `RAG/MCP` `Memory 架构` `LLM Evaluation` `Reliability Engineering` `TypeScript`
