# Code Style Conventions

延续仓库已有规范，并在本项目中强化的硬约束。任何新代码必须通过本文件第 1–3 节的自动 lint，第 4–7 节通过静态 diff review。

## 1. Python

- `from __future__ import annotations` 在每个模块顶部
- 类型注解必须完整，函数签名不允许缺失
- 联合类型使用 `X | None` 而不是 `Optional[X]`
- Pydantic v2 模型（`BaseModel` + `Field` + `model_validator`）
- 异常类型必须明确，避免 `Exception: ...` 裸用
- 日志使用参数化格式：`logger.info("...", session_id, count)` 而不是 f-string
- 模块级 lazy singleton 使用显式 getter：`get_X() -> X`
- `asyncpg` 查询必须使用参数绑定，禁用字符串拼接
- 事务包裹：requirement/report/message/session 写入必须位于同一 `async with pool.acquire()` 事务
- 写入路径必须校验 `user_id`，从 JWT 上下文获取，不接受请求体传入

## 2. TypeScript

- 严格模式（`strict: true`）必须保持
- 类型优先使用判别联合，避免 `Record<string, unknown>`
- 禁止双重 `as unknown as` 链式断言
- SSE 事件反序列化必须有显式 type guard；`unknown` 不允许直接进 reducer
- 函数式 reducer：纯函数、不可变更新；SSE 网络副作用放在 action/service 层
- 组件 props 局部命名为 `Props`，默认导出函数组件
- CSS Modules 优先，禁止在 JSX 写 `style={{}}` 魔法值
- Ant Design 控件通过 `ConfigProvider` 主题 + `classNames/styles` 控制，禁用 `!important` 与全局脆弱选择器

## 3. Style and Lint

- 提交前 `git diff --check` 必须 0 错误
- 前端 `npm run build` 通过（仅在有环境机器上）
- 前端 `npm run lint` 通过（仅在有环境机器上）
- Python `ruff check` 或仓库等价工具通过（仅在有环境机器上）
- 禁止新增 `*TSC-Header-###%` 加密源文件；如果需修改加密文件，先在解密环境下进行

## 4. State Machine Discipline

- 所有 AnalysisPhase 合法转移集中在后端状态机和前端 reducer 中
- React 组件不得直接修改业务 phase；只调用 dispatch
- 单一事实来源：phase 权威来自后端 SSE，前端 reducer 镜像
- 失败路径不污染报告版本：失败时不能新增 report_version 行
- 业务用户不应看到后端 tool key；analysis_methods 只展示业务名称
- 调整报告（adjust）必须显式携带 `base_report_version`；不允许无基线调整

## 5. SQL Gate

- 确认前严禁执行 `validate_sql` / `execute_sql`
- 确认前严禁进入 Report Agent
- 结构性保证：requirement-analysis graph 只注册 Schema tools（`search_tables` / `get_table_ddl` / `list_tables`）
- 测试用 spy 验证：vague question / clear question 流程中 `validate_sql` / `execute_sql` 调用次数 == 0

## 6. Transaction Boundary

- 创建/更新 requirement draft + 写 conversation pointer 必须在同一事务
- 创建 report_version + 更新 session latest_version + 写 conversation pointer 必须在同一事务
- 跨事务失败必须回滚所有写入，避免孤儿数据
- 版本号使用事务内 `MAX(version) + 1`，配合 unique constraint 防止并发覆盖

## 7. Authorization

- 所有 read/write 必须同时校验 JWT `user_id` 与 `session_id`
- 跨用户访问 session / report / template 一律拒绝（HTTP 403）
- 模板按 `user_id` 隔离；不能列出或访问他人模板
- 安全审核（`SecurityGuard`）必须覆盖 `/chat` 与 `/confirm` 与 PATCH 入口

## 8. Backward Compatibility

- 旧 SSE `card / clarify / token` 事件至少保留 1 个 epoch
- 旧 chat 路由保留 1 个 epoch，但新 API 不再依赖
- 旧 ChatCards 仅作为 legacy read-only renderer
- 旧 frontend 组件用 adapter 过渡，不一次性删除
- 删除前必须确认所有 active 客户端已升级
