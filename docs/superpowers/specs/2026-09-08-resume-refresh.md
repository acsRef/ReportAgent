# Resume Refresh Design — 中英双份社招版简历

> Date: 2026-09-08
> Author: superpowers brainstorming session
> Status: approved (用户拍板: 中英双份 + 社招 + 数据点 + 验证)

## Context

用户当前是**社招**状态,需要一份新简历。`D:\PyProject\简历模板\` 下三份文件:

| 文件 | 现状 |
|---|---|
| `cv.tex` | LaTeXTemplates 老英文模板(Peking University / 北大教育 / 老实习) |
| `resume_ch.tex` | 已按 ReportAgent+RAG 改写的中文版,但 bullet 偏短 |
| `resume.cls` | 样式文件,不动 |

`D:\PyProject\ReportAgent\docs\resume-bullets.md` 已有版本 A(5 条更长)与版本 B(3 条精简)草稿,本次刷新**采版本 A 的密度**,但用 commit `85e613b` 后的真实数字(backend 1181+ / frontend 302 / evaluation 78)。

## Design

### 交付物

- [cv.tex](D:/PyProject/简历模板/cv.tex): 重写为英文版,社招标准结构
- [resume_ch.tex](D:/PyProject/简历模板/resume_ch.tex): 同步润色升级,结构与英文版对齐
- [resume.cls](D:/PyProject/简历模板/resume.cls): **不动**

### 段位顺序(两份同步)

```
Header (姓名 / 电话 / 邮箱 / GitHub / 期望岗位)
Summary / 个人简介 (英文版 2-3 行;中文版 2-3 行)
Skills / 技能 (密度沿用 resume_ch.tex + 增 Langfuse / Playwright / TypeScript / Memory 架构)
Professional Experience / 工作经验 (占位段,提醒用户填真实公司经历)
Projects / 项目经验 (核心段,详写)
Education / 教育 (下移,简短占位)
Honors & Certifications / 证书 (软件设计师 / CET-4 占位)
```

### ReportAgent 5 条 bullets(版本 A 全文,数据点拉满)

每条 bullet 都有数字 / 因果 / 验证:

1. **Agent–Workflow boundary** — 3-stage agent pipeline,8 类错误分类 + 固定预算(SQL 2 / MCP 2 / LLM 2)
2. **Memory as a measurable system** — 4 类记忆 + Selective Recall + Agent 档位隔离
3. **5-layer evaluation caught real regression** — Gold Set 30/30 + 真 embedding Recall@1 0.84 / Recall@3 0.96 / MRR 0.90;真 LLM 门抓到参考帧年份 anchor 导致「去年=2023」回归,ON/OFF 归因 + 边界声明修复
4. **Reliability engineering** — SQL 3 态落库 + 3-layer Validator(结构 / KPI 聚合重算 / 行存在性)+ 双 sink trace(PG + Langfuse)
5. **Engineering closure** — 1180+ backend / 301 frontend / 78 eval 全绿;Playwright Contract E2E 入 CI;15 phases plan-driven

### RAGent 4 条 bullets(沿用 + 数据点)

1. 多格式文档 + 表格双表示
2. Hybrid Retrieval → RRF → Cross-Encoder Rerank → MMR
3. RAG Evaluation Pipeline(65 题 3 分制 Judge + Ablation)
4. 工程能力(增量索引 / PII / JWT+RBAC / MCP Data Dictionary / 配置开关隔离)

### 关键词标签(技能段用)

`LangGraph` · `FastAPI` · `PostgreSQL+pgvector` · `React 19 + Vite + SSE` · `Playwright` · `Langfuse` · `RAG/MCP` · `Memory Architecture` · `LLM Evaluation` · `Reliability Engineering` · `TypeScript` · `Python`

### 占位符约定

- 姓名 / 电话 / 邮箱: `[Your Name]` / `[+86 xxx-xxxx-xxxx]` / `[you@example.com]`
- 教育: `[University Name · Degree · Year–Year]`
- 工作经验: `[Company · Role · Duration]` + 2-3 个 bullet 占位行
- 用户后续替换,LaTeX 编译期不会报错

## Files to change

- 改写: `D:\PyProject\简历模板\cv.tex`(英文版)
- 改写: `D:\PyProject\简历模板\resume_ch.tex`(中文版)
- 不动: `D:\PyProject\简历模板\resume.cls`
- 不动: `D:\PyProject\ReportAgent\docs\resume-bullets.md`(源事实文档)

## Reused existing utilities

- [docs/resume-bullets.md](D:/PyProject/ReportAgent/docs/resume-bullets.md) 版本 A — bullet 事实源
- [docs/interview-talk-track.md](D:/PyProject/ReportAgent/docs/interview-talk-track.md) — 项目叙事上下文(用不上,但作为证据索引)
- [CLAUDE.md](D:/PyProject/ReportAgent/CLAUDE.md) §6 — Memory 评测数字官方口径

## Verification

- `pdflatex cv.tex` / `pdflatex resume_ch.tex` 编译无报错(如有 LaTeX 发行版)
- 视觉检查: 双列 bullet 对齐 / 项目标题字体加粗 / GitHub 链接 hyperref 正常
- 数字一致性: 1180+ / 301 / 78 / Recall@1 0.84 / Recall@3 0.96 / MRR 0.90 / 30/30 / 65 / 8 类错误 / SQL 2 MCP 2 LLM 2 全部一致
- bullet 与 [resume-bullets.md](D:/PyProject/ReportAgent/docs/resume-bullets.md) 版本 A 语义对齐

## Explicitly NOT doing

- 不动 `resume.cls` 样式
- 不引入新的 LaTeX 包(只沿用原模板的 `geometry` / `color` / `hyperref` / `array` / `ifthen` / `parskip`)
- 不写真实公司工作经历(用户后续填)
- 不写教育背景详情(用户后续填)
- 不写真实姓名电话邮箱(用户后续填)
- 不生成 PDF(只交付 .tex 源文件,用户自行编译)