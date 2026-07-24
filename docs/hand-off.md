# File Hand-off and Resume Guide

> 本文档帮助另一台电脑上的开发人员快速理解本次改动并继续工作。

## 1. 分支

- `feat/conversational-workbench` 是本会话所有有价值的提交分支。
- 切到该分支即可看到所有新增的契约、前端类型/reducer/SSE 解析器、文档、UI 风格指南和复制进来的完整 plan。

## 2. 提交顺序

按时间顺序：

1. **chore(docs): 同步 master 已有的 AGENTS.md / CLAUDE.md / docs/prototype.html 改动**
   - 包含你已在 master 修改但未推送的三个文件。
2. **docs: 添加设计、原型与 HTML plan**
   - `docs/intelligent-analysis-workbench.html`（可点击交互原型）
   - `docs/plans/2026-07-24-intelligent-analysis-workbench-design.md`
   - `docs/plans/2026-07-24-intelligent-analysis-workbench-html.md`
3. **docs: 添加完整实施 plan**
   - `docs/plans/2026-07-24-conversational-workbench.md`（plan 副本）
   - `docs/ui-style-guide.md`
   - `docs/code-style-conventions.md`
   - `docs/contracts/requirement-card.md`
   - `docs/api-reference.md`
   - `docs/sse-v2.md`
   - `docs/state-machine.md`
   - `docs/persistence.md`
4. **feat(backend): RequirementCard Pydantic 契约**
   - `backend/app/models/requirement.py`
5. **feat(frontend): AnalysisPhase、报告版本、纯 reducer、SSE 解析器**
   - `frontend/src/types/requirement.ts`
   - `frontend/src/types/analysis.ts`
   - `frontend/src/stores/analysisReducer.ts`
   - `frontend/src/api/analysisEvents.ts`

## 3. 立即可继续的代码

无需 TSD 解密就能继续：

- 所有新增文档（plan、契约、API、SSE、状态机、DDL、UI 风格、代码规范）。
- 前端 prototype `intelligent-analysis-workbench.html`。
- 后端 `backend/app/models/requirement.py`：Pydantic 契约，可独立 import。
- 前端 `types/requirement.ts`、`types/analysis.ts`、`stores/analysisReducer.ts`、`api/analysisEvents.ts`：纯 TS，可独立编译。

## 4. 必须 TSD 解密后才能做

仓库关键 Python 源是 TSD 加密 blob，加密文件包括：

- `backend/app/main.py`
- `backend/app/agent/parent_graph.py`
- `backend/app/agent/sql_graph.py`
- `backend/app/agent/data_graph.py`
- `backend/app/agent/security_guard.py`
- `backend/app/agent/report_graph.py`
- `backend/app/db.py`
- `backend/app/llm.py`
- `backend/app/tools/registry.py`
- `backend/app/infra/checkpoint/session.py`
- `backend/app/infra/conversation/repository.py`
- `backend/app/infra/db/postgres.py`
- `backend/app/infra/memory/*`
- `backend/app/infra/trace/*`
- `backend/app/embedding/service.py`
- `backend/scripts/init_pg.sql`（必须合并本文件 DDL）

解密后，按 `docs/persistence.md` + `docs/api-reference.md` + `docs/state-machine.md` 顺序接入 LangGraph 节点和 FastAPI 路由。

## 5. 在另一台电脑上的第一步

```bash
git fetch origin
git checkout feat/conversational-workbench
git pull
npm --prefix frontend install  # 如果有环境
python -m pip install -r backend/requirements.txt
```

阅读顺序：

1. `docs/intelligent-analysis-workbench.html`（在浏览器中交互看设计）
2. `docs/plans/2026-07-24-conversational-workbench.md`（完整 plan）
3. `docs/ui-style-guide.md`（视觉规范）
4. `docs/code-style-conventions.md`（代码规范）
5. `docs/contracts/requirement-card.md`、`docs/api-reference.md`、`docs/sse-v2.md`、`docs/state-machine.md`、`docs/persistence.md`（具体契约）

## 6. 风险与已知问题

- 当前会话无法安装/运行 npm/pytest/python 依赖，所有验证命令在具备环境的机器上才可执行。
- 仓库加密源文件以 TSD 加密形式存在于 working tree；任何修改必须先解密。
- 旧的 LangGraph 路径（`chosen_tool` / `clarify interrupt` / `intent_card`）需要保留 1 epoch 兼容旧前端，再删除。

## 7. 不属于本分支的内容（已排除）

- `docs/intelligent-analysis-workbench - 副本.html`：副本已删除。
- `prototype.html`：保留，不提交（不在本任务范围）。
- TSD 加密源的任何变更：留待 TSD 解密后。
- 测试基础设施：留待环境就绪后补做。
