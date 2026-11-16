> 状态: 已完成（2026-11-16）

# 执行中上下文溢出恢复（DSH 第三触发）+ 前端手动压缩入口

## Context

`docs/plans/2026-11-16-compaction-dsh-alignment.md` 落地了 DSH 的**两条**触发（token 压力 / 条数兜底）与手动压缩
（`POST /api/v1/sessions/{sid}/compact`），但**第三触发 `context-overflow` 没做**：provider 报「窗口超限」时
`llm_resilience` 把它当普通错误重试 2 次（必然复现，白等），最终整条任务失败。同时手动压缩只有后端端点，
界面上没有入口——用户不点 Postman 就用不到。

用户明确要求两件都补：

> 「要不要我接着补 execution 中溢出恢复（DSH 第三触发）和前端“压缩上下文”按钮？」→ 「可以」

### 现状（读代码确认，不凭记忆）

| 事实 | 位置 |
|---|---|
| `invoke_with_retry` 只对 `RateLimitError / InternalServerError / APIConnectionError / APITimeoutError` 重试，其余**原样抛出** | `backend/app/reliability/retry.py` `_classify_retryable` |
| 溢出错误（`BadRequestError` + `context_length_exceeded`）因此直达节点，`sql_graph.ainvoke` 整体抛出 | `sql_graph._plan` / `_generate_sql` 里的 `call_llm` |
| 执行节点在 `sql_graph.ainvoke` **之前**已经建好上下文（`ContextRuntime.build`） | `confirmed_execution_graph.py:360` |
| 手动压缩端点已存在（404/409/503 三态 + `reason`） | `main.py` `POST /sessions/{id}/compact` |
| 工作台画布头部有 `wb-head-actions`（现有「聚焦内容」按钮），可挂维护动作 | `WorkbenchPage.tsx:819` |

### 为什么值得做，以及边界

- 溢出是**唯一**会让「压缩」救命而不是省钱的场景：一旦 provider 拒绝，除了压缩没有别的自救手段；
- 但不能无限救：DSH 的预算就是 `maxOverflowRetries = 1`（压缩一次 → 重试一次 → 还不行就认）；
- 恢复动作要求「强制全量折叠」（`retain=0`，只留最近一问一答），因为正常阈值压缩可能不足以腾出空间。

## Design

### 一、错误分类（单一来源）

`backend/app/reliability/errors.py`：

```python
CONTEXT_OVERFLOW_KIND = "context_overflow"
_CONTEXT_OVERFLOW_MARKERS = (
    "context_length_exceeded", "maximum context length", "context window",
    "too many tokens", "reduce the length",
)
def is_context_overflow_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _CONTEXT_OVERFLOW_MARKERS)
```

同时把 `context_overflow` 加进 `SQL_ERROR_KINDS`（`normalize_kind` 的域）与
`USER_RECOVERABLE_KINDS`（用户可以「缩小时间范围/新开会话」自救），并在 `_USER_MESSAGES` 给一句
明确文案。**不进** `AGENT_RECOVERABLE_KINDS`：同一条 SQL 重写没有意义，需要的是整轮重来。

### 二、强制折叠（`retain=0`）

`ContextRuntime.build(...)` / `prepare_conversation_context(...)` / `build_context(...)` 增加
`force`（已有）与 `retain_tokens` 覆盖参数；溢出恢复传 `force=True, retain_tokens=0`
→ `select_fold_span` 只保底 `floor_messages=3` 条。

### 三、接线（只在一处，重试一次）

`confirmed_execution_graph` 新增模块级 helper：

```python
async def _invoke_sql_agent_with_overflow_recovery(
    *, session_id, user_id, user_query, state_dict,
    build_state: Callable[[str, str], dict],   # (conversation_context, assembled_context) → sql state
    invoke: Callable[[dict], Awaitable[dict]],
) -> dict:
    try:
        return await invoke(build_state(conversation_context, assembled_context))
    except Exception as exc:
        if not is_context_overflow_error(exc):
            raise
        # 只重试一次（DSH maxOverflowRetries=1）：强制全量折叠 → 重建上下文 → 再跑
        conversation_context, assembled_context = await _forced_compaction_context(...)
        return await invoke(build_state(conversation_context, assembled_context))
```

节点里改成构造 `build_state` lambda + 调用 helper。**不做**的：不改 `llm_resilience` 的通用重试语义
（它按「瞬时故障」分类是对的），也不给 requirement / report 节点接线（它们的 prompt 规模远小于窗口，
没有证据表明会撞上——不写无证据的机制）。

### 四、前端手动压缩入口

- `src/api/sessionsClient.ts`：`compactSession(sessionId)` → `POST /sessions/{id}/compact`，
  返回 `{compacted, reason, digest_msg_count, digest_version, context_chars, raw_chars}`；
- `WorkbenchPage`：`wb-head-actions` 增加「整理上下文」按钮（`wb-quiet-btn`，无 activeSession 或 busy
  时 disabled），点击 → 调 API → toast：成功报「已压缩 4808 → 1500 字」，未压缩按 `reason` 说清楚
  （`ALREADY_COMPACTED` / `NOTHING_TO_COMPACT` / `NOT_WORTH_IT` / `EMPTY_SESSION`），失败报错误；
- 不做界面上的进度条/轮询：压缩是 ~秒级的同步端点，按钮 loading 即可。

## Files to change

| 文件 | 变更 |
|---|---|
| `backend/app/reliability/errors.py` | `CONTEXT_OVERFLOW_KIND` / `is_context_overflow_error` + 两张表与文案 |
| `backend/app/memory/conversation.py` | `build_context` / `prepare_conversation_context` 增 `retain_tokens` 覆盖 |
| `backend/app/context/runtime.py` | `build(...)` 增 `force_compaction` / `retain_tokens` 透传 |
| `backend/app/agent/confirmed_execution_graph.py` | 溢出恢复 helper + 节点接线（重试一次） |
| `backend/tests/contracts/test_context_overflow_recovery.py` | **新增**：分类用例 + helper 接线（真溢出→压一次并重试；非溢出→直接抛；第二次还炸→抛） |
| `frontend/src/api/sessionsClient.ts` | `compactSession` |
| `frontend/src/pages/WorkbenchPage.tsx` | 「整理上下文」按钮 + toast 文案 |
| `frontend/src/api/__tests__/sessionsClient.test.ts`（若存在）/ `WorkbenchPage` 相关测试 | 端点与文案断言 |
| `docs/plans/README.md` | 索引登记 |

## Reused existing utilities

- `is_context_overflow_error` 与 `normalize_kind` / `user_recoverable` 放在同一个 `reliability/errors.py`；
- 强制折叠复用 `compact_session_now` 的同一套守卫（数据事实、数值一致性、shrink、净收益）；
- 前端复用 `sessionsClient` 的 `request` 封装、`useToast()`、`wb-quiet-btn` 样式；
- 后端契约测试沿用 `tests/contracts/test_session_delete_gate.py` 的「monkeypatch 模块属性 + asyncio.run」写法。

## Verification

1. `cd backend && pytest tests/contracts/test_context_overflow_recovery.py -q`
2. 受影响面：`pytest tests/contracts tests/graphs -q`；收尾跑一次全量
3. 真机溢出：把 `LLM_CONTEXT_WINDOW` 临时调到极小（如 2000）跑一次 confirm → 观察日志
   「context overflow → forced compaction → retry」并确认任务最终完成或按预期失败；
4. 前端：`npx vitest --run src/pages/__tests__/WorkbenchPage.test.tsx` + `npx tsc -b`；
5. 浏览器点按钮：44 条消息的临时会话 → toast 显示压缩前后字数，`agent.session.digest` 落库。

## Explicitly NOT doing

- 不给 requirement / report 节点接线（无证据 + prompt 规模小）；
- 不做「压缩失败自动降级为截断」（截断会破坏数据事实块的完整性）；
- 不做前端进度条/后台轮询（同步端点，秒级）；
- 不改 `llm_resilience` 的重试分类语义。


## 验证结果（2026-11-16）

**真实 provider 溢出实测**（这是本轮最重要的一条：合成用例猜的词表一个都没命中）：

```
真实请求（36 万字符 prompt）
→ BadRequestError: Error code: 400 - {'code': 20015, 'message':
   'number of input tokens (210003) has exceeded max_seq_len (163840) limit.'}
→ 第一版标记（context_length_exceeded / maximum context length / …）全部不匹配 → is_context_overflow_error = False
→ 补上 max_seq_len / exceeded max_seq_len 后 = True
→ 坏 key（AuthenticationError 401）= False（没误判）
```

**端到端接线实测**（真 provider 错误 + 真数仓 + 真 ContextRuntime）：

```
context overflow in sql agent (session=4cadf946…): Error code: 400 - {'code': 20015, …};
forcing full compaction, retry once
→ 最终抛出 BadRequestError，总共调了 2 次（第一次溢出 → 强制压缩 → 重试一次仍溢出 → 抛）
   ↑ 只重试一次、不压缩第二次（maxOverflowRetries=1）
```

**前端真机点击**（Playwright，1440×900，后端 8100 + 前端 3000）：

```
button disabled? false
toast = 已整理上下文：4808 → 1451 字
console errors = 0
```

截图：`frontend/.e2elogs/compact-ui.png`（按钮在画布头部「聚焦内容」左侧，toast 在顶部）；
点完后 `agent.session.digest` = `digest_msg_count=33 / digest_version=1 / 195 字`（5 段式 checkpoint，数值与数据事实一致）。

**回归**：

| 层 | 结果 |
|---|---|
| 后端 | 新增 `test_context_overflow_recovery.py` **13 passed**（分类 9 例 + 接线 4 例）；`tests/contracts tests/graphs` 1445 passed；全量见文末 |
| 契约同步 | `test_reliability_errors.py` / `test_diagnose_policy_sources.py` 的两张 kind 表断言随 `context_overflow` 更新（差集是**故意**钉住的设计判据） |
| 前端 | `npx tsc -b` 0 错误；`npx vitest --run` **61 文件 / 507 passed**（+7：`compactSession` 客户端 1、`compactResultToast` 6） |
| 受影响 fake | 3 个测试里的 `fake_prepare*` 加 `**_kwargs`（`runtime.build` 现在会透传 `force=/retain_tokens=`） |
