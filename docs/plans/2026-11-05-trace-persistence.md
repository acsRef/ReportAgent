> 状态: 已完成

# 思考链路落库：刷新之后历史轮次仍然读得到「当时怎么想的」

## Context（背景）

2026-11-05 用户看到思考链路的效果后追问：「落库啊，应该是要存在数据库里的啊」。
现状确实不落库：trace 帧只经 SSE 发给当前页面（`ProgressTraceHandler → registry.publish`），
进程里不留、库里不存——**F5 之后历史轮次只剩一行「已思考（用时 N 秒）」，点开是空的**。
同一轮里 `assistant_text`（助手那句话）是落库的，于是出现了「说了什么留下来了、怎么想的没留下」的割裂。

## Design（设计）

### D1 存储形态：`app.conversations` 多一种行，不建新表

```sql
INSERT INTO app.conversations (session_id, user_id, role, content, message_type, metadata)
VALUES ($1, $2, 'assistant', NULL, 'turn_trace',
        '{"entries":[{"nodeName":"安全检查","status":"success","detail":"通过（LOW，风险分 0）","kind":"agent","ts":1762345678901}],
          "duration_ms": 12400,
          "status": "done"}'::jsonb);
```

理由：
- `app.conversations` 已经是会话账本（带 `id` 顺序、`created_at`、`user_id` 隔离、快照一次读出），
  「助手这一轮做了哪几步」本来就是对话的一部分；
- 不引入新表 = 不需要迁移脚本、不需要新的 repository/快照 join；
- 代价只有一个：统计口径要把它排除（`list_sessions` 的 `msg_count` / 首末句，D2）。

字段约定：
- `role='assistant'`、`content=NULL`（不是消息，前端不渲染成气泡）；
- `metadata.entries[]` 用**前端已有的字段名**（`nodeName/status/detail/kind/reasoning/ts`），
  这样 `TimelineEntry` 一一对应，前端不需要第二套映射；
- `metadata.duration_ms` 由**帧到达时间**算（runner 侧），前端不再自己推算；
- `metadata.status ∈ done|error|stopped`。

### D2 采集与写入：runner 顺手收，best-effort 落

两个后台 runner（需求轮 `_run_requirement_graph`、确认/调整轮 `_run_confirm_graph`）在
`on_frame` 里**同时做两件事**：照旧 `registry.publish`（在线用户实时看到），并把
`frame["data"]` 反序列化后带 `ts=time.time()*1000` 塞进 `collected`。
需求轮那条直连出来的「理解意图 · 思考」帧也进 `collected`。

轮次结束（`finally`，成功/失败/超时都覆盖）→ `save_turn_trace(...)`：
- 失败只 `logger.warning`，**绝不影响**主链（trace 是观测数据，不是业务结果）；
- 同一轮最多两行（需求阶段一行、执行阶段一行），前端按时间合并（D3）。

### D3 读取：前端把 trace 行挂到该轮助手消息上

`get_messages` 已经返回 `metadata`（**注意：asyncpg 无 jsonb codec，线上是 JSON 字符串**，
前端必须 `typeof === 'string' ? JSON.parse : ...` 兼容）。

`chatTranscript.mergeServerHistory`：
1. `message_type ∈ {report_version, turn_trace}` 的行**不产生消息**；
2. `turn_trace` 行按 `created_at` 找到「它之前最近的一条 assistant 消息」，
   把 `entries` 合并进该消息的 `trace`（按 `nodeName` 去重、后到覆盖）；
3. 本地已有 trace（当轮实时）优先于落库 trace：落库行只是刷新后的来源；
4. 重复合并幂等（同一行反复出现不会让步骤翻倍）。

### D4 顺带补上闲聊落库

闲聊轮的助手回复此前不落库（`main.py` 只发 `report` 事件），所以刷新后那一轮
「有思考、没回答」。既然这一轮已经要写 trace 行，就把 `casual_reply` 也写一行
`assistant_text`——同一处改动、不留半截。

### D5 统计口径

`list_sessions` 的 `msg_count` / `first_message` / `last_message` 排除
`message_type='turn_trace'`：否则左侧 rail 的条数会被 trace 行灌水、末句预览会变成 NULL。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/infra/execution/trace_store.py`（新） | `build_trace_metadata(frames) -> dict`（纯函数：SSE payload → `entries` + `duration_ms`）+ `save_turn_trace(...)`（best-effort 落一行） |
| `backend/app/main.py` | 两个 runner 的 `on_frame` 收集帧；`finally` 里落库；闲聊补写 `assistant_text` |
| `backend/app/infra/conversation/repository.py` | `save_message` 的 `content` 允许 `None`；`list_sessions` 排除 `turn_trace` 行 |
| `frontend/src/types/analysis.ts` | `ConversationMessage` 补 `metadata` |
| `frontend/src/stores/chatTranscript.ts` | `turn_trace` 行解析 + 挂到该轮助手消息；过滤出消息流 |
| `frontend/src/stores/__tests__/chatTranscript.test.ts` | trace 行挂载 / 不产生消息 / 幂等 / 本地优先 |
| `backend/tests/smoke/test_trace_store.py`（新） | payload 构造（字段名、时间戳、去重、空输入） |

## Reused existing utilities（复用工具）

- `app/infra/conversation/repository.save_message`（同一张表、同一套 owner/时区口径）；
- `ProgressTraceHandler.on_frame` 现有出口（只在外面包一层收集，不改帧格式）；
- `get_messages` / `snapshot_service`（metadata 已经透传，无需新端点）；
- 前端 `ChatMessage.trace` 与 `TimelineEntry`（上一轮 plan 已把过程挂在消息上）；
- `utc_iso` / `timedelta` 口径（时间字段继续走同一函数）。

## Verification（验证）

1. 后端离线：`pytest backend/tests/smoke/test_trace_store.py -q`；
2. 前端：`npx vitest --run src/stores/__tests__/chatTranscript.test.ts`；
3. 真机：两轮问答 → **F5** → 点开第一轮历史思考块：
   头部「已思考（用时 N 秒）」、展开有思考链路原文 + 执行步骤（不是空的）；
4. DB 对照：`select message_type, left(metadata::text, 80), created_at from app.conversations where session_id=$1 order by id`
   应看到每轮一行 `turn_trace`；
5. `.e2elogs/probe_p0_final.mjs` 与 `probe_reasoning_ui.mjs` 不回归。

## Explicitly NOT doing（不做事项）

- 不建新表、不写迁移脚本（D1 用现有会话表）；
- 不做 trace 压缩/裁剪/分页（一行一轮，量级与消息同阶；超过再说）；
- 不把 CoT 送进任何 prompt / Memory（它只是观测数据，不参与决策）；
- 不改 SSE 事件面与 trace 帧格式。

---

## 落地记录（2026-11-05 21:35）

### 实际改动

| 文件 | 改动 |
|---|---|
| `backend/app/infra/execution/trace_store.py`（新） | `collect_frame`（SSE 帧 → 带 ts 的帧 dict）+ `build_trace_metadata`（按 nodeName 去重、耗时按首帧算）+ `save_turn_trace`（best-effort 落一行） |
| `backend/app/main.py` | 两个 runner 的 `on_frame` 改 `_publish_and_collect`（实时 publish + 收集）；`finally` 里 `save_turn_trace`（成功/失败/超时都覆盖）；需求轮那条直连「理解意图 · 思考」帧也进收集；闲聊回复补写 `assistant_text` |
| `backend/app/infra/conversation/repository.py` | `save_message` 的 `content` 允许 `None`；`list_sessions` 统计排除 `turn_trace` |
| `frontend/src/types/analysis.ts` | `ConversationMessage` 补 `metadata`（注明线上是 JSON 字符串） |
| `frontend/src/stores/chatTranscript.ts` | `turn_trace` 行不进消息流；`traceFromServerRow` 解析 `metadata.entries`；`attachPersistedTraces` 按**行序**挂到该轮助手消息；`mergeTraceEntries` 保序追加、本地优先、幂等 |
| `backend/tests/smoke/test_trace_store.py`（新） | 6 例：只收 trace 帧 / 字段映射 / 去重与首帧时间 / 空输入 / 原文截断 / 空原文丢弃 |
| `frontend/src/stores/__tests__/chatTranscript.test.ts` | +6 例：trace 行挂载 / JSON 字符串 metadata / 坏 metadata / 两行合并 / 本地优先 + 幂等 / 不串轮 |

### 验证（真机）

`node .e2elogs/probe_trace_persistence.mjs` **PASS 12/12**：

```
turn_trace rows: [{"entries":10,"duration_ms":12480,"status":"done"},
                  {"entries":13,"duration_ms":21246,"status":"done"}]   ← 需求阶段 + 执行阶段各一行
after reload:  头部「▾ 已思考（用时 33.9 秒）」+ 链路原文首句「我们只需要输出JSON…」
               步骤 21 条（需求 10 + 执行 11，含 生成 SQL / 执行查询 / 撰写报告内容）
第二轮开始：   第一轮 ▸ 收起（历史），点开仍是同一条链路 20 条步骤
气泡/统计：    1 问 1 答不变（trace 行不成消息）、rail msg_count=4 未被灌水
```

其他门禁：后端 `smoke + contracts` **664 passed**（唯一红 `test_requirement_card_mirror`
是 2026-11-04 加 `top_period` 时漏同步的既有问题，单独提交修掉）；前端 `tsc -b` 干净、
`lint` 0 error、受影响单测 **284 passed**。

截图：`.e2elogs/trace_persisted_reload.png`（刷新后 21 步）、`trace_persisted_history.png`。

### 落地偏差 3 项

1. **trace 行归属改按行序而不是时间**：同一事务里多行 `created_at` 相同，
   按时间挑「最后一条助手消息」会把 trace 挂到下一轮（单测当场抓到）。
   现在沿服务端行序往前找最近一条已进入结果集的 assistant 行，用它 `serverId` 定位。
2. **`metadata` 线上是 JSON 字符串**：asyncpg 没注册 jsonb codec，API 原样透传字符串。
   前端 `rowMetadata` 两种形态都吃（字符串 parse / 对象直读），坏 JSON 返回 null。
3. **闲聊回复顺带落库**（原计划 D4）：不落的话刷新后那一轮「有思考、没回答」，很割裂。
