> 状态: 进行中

# CI 门禁收缩为「后端 pytest」，Contract E2E 移出 PR 面

## Context

每次 push 都会红，报的是：

```
Error: 1 failed
  [chromium] › e2e/specs/06-report-version.spec.ts:14 › 报告 v1 后发送 adjust，自动切到 v2 并渲染新内容
  等待 .wb-report-shell 包含 "商品1" 超时（60s）
```

失败详情里，v2 报告实际渲染的是**真实数据的商品销售额排名**（微软 Surface Pro 9、iPad Air…），而断言要的 `商品1` 是 mock fixture 里的占位内容。

根因不是产品坏，而是 **Contract E2E 的 mock keying = 语义 kind + 调用序**：`backend/app/llm/mock.py` 按「prompt 首句分类 + 第 N 次调用」返回 canned 响应。只要 prompt 文案/调用次数变化（本次改了 requirement prompt 的结构与口径规则），映射就错位，某一步拿到别的 fixture → 断言落空。

也就是说：**这个门禁拦住的不是回归，是"我改了 prompt"**。对一个个人项目，它带来的成本（每次 push 红、2.6 分钟）远大于收益。

用户决策：**CI 只跑后端测试**。

## Design

拆成两个 workflow，职责一刀切：

| workflow | 触发 | 内容 | 是否阻塞提交 |
|---|---|---|---|
| `.github/workflows/ci.yml` | `pull_request` + `push: master` | **仅** `backend-gate`：全量 pytest（PG service 真跑 persistence） | 是（唯一门禁） |
| `.github/workflows/e2e-contract.yml` | `workflow_dispatch` + 每日 cron（18:00 UTC / 02:00 CST） | `frontend-gate`（vitest + build）与 `contract-e2e`（Playwright 10 specs） | **否** |

细节：

- `e2e-contract` 加 `workflow_dispatch.inputs.only`（`all|frontend|contract`），可只跑一半，便于定位。
- 失败时 `upload-artifact` 上传 `frontend/e2e/artifacts`（截图 / trace / error-context），保留 7 天——否则失败了什么也看不到。
- `contract-e2e` 的注释显式写明「mock keying 脆弱，失败需人工看 artifacts」，避免下一个人误判成产品回归。
- 两个 job 都加 `timeout-minutes`，防止 runner 卡死。
- **不删任何代码**：specs / helpers / mock fixture 原样保留，本地 `npm run e2e:contract` 照旧可用。

配套文档同步（避免"文档声称 per-PR"这种失真）：

- `README.md` 测试行、`CLAUDE.md` §9 P12 现状 + §14 Phase 门纪律、`frontend/e2e/README.md`、`docs/resume-bullets.md`。

## Files to change

| 文件 | 变更 |
|---|---|
| `.github/workflows/ci.yml` | 删除 `frontend-gate` / `contract-e2e` 两个 job；保留并加超时 |
| `.github/workflows/e2e-contract.yml`（新） | 手动 + 夜间跑前端与 Playwright，带 artifacts 上传 |
| `README.md` / `CLAUDE.md` / `frontend/e2e/README.md` / `docs/resume-bullets.md` | 把"per-PR"改为"独立 workflow（手动/夜间）" |
| `docs/plans/README.md` | 登记本 plan |

## Reused existing utilities

- 后端 job 原封不动复用（PG service + `init_pg.sql` + `seed_business_p15prelude.sql` + `setup_app_role.sql` + `::error::` 注解风格）。
- Playwright 工程、`npm run e2e:contract` 脚本、mock fixture **零改动**。
- `docs/testing-boundaries.md` 的边界模型不变：B10（前端契约）仍由本地 vitest + 手动 e2e 守，只是不再进 PR 门禁。

## Verification

1. **YAML 语法**：两个 workflow 用 `python -c "import yaml,...; yaml.safe_load(open(...))"` 解析通过。
2. **后端门禁等价性**：本地跑与 CI 相同的命令 `pytest -q` → 1188 passed / 1 skipped（与拆分前一致）。
3. **推送后观察**：push `master` 只应触发 `ci`（backend-gate）；`e2e-contract` 只在手动/定时触发。
4. **不回归**：`frontend/e2e/` 目录与 npm 脚本未被修改，本地 `cd frontend && npm run e2e:contract` 仍可按原样运行。

## Explicitly NOT doing

- 不删除 Playwright 工程、specs、mock fixtures（只是不再进 PR 门禁）。
- 不修 spec 06 的脆弱断言（根因是 mock keying，要修得让 mock 支持"按 prompt 内容哈希匹配"，属独立议题）。
- 不改 `backend/app/llm/mock.py` 的 keying 机制。
- 不引入 `continue-on-error` 假装绿——`e2e-contract` 自己红是该红的，只是不拦提交。
