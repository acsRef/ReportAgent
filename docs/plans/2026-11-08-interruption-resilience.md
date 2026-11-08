> 状态: 已完成

# 输出过程中的中断：断流 / 退出 / 刷新 —— 缓冲有界、状态如实、每类一个独立用例

## Context（背景）

用户 2026-11-08 提出：思考链路现在是**流式**的（`trace` 帧 status=running 持续推送），
那么「推流过程中断掉」必须被认真对待：

1. **网络/连接中断**（SSE 断流）：后台任务还在跑，帧还在往里塞——**没有订阅者时缓冲会长到哪去？**
2. **用户中途退出**（点停止 / 切会话 / 新建分析 / 登出 / 关页面）：前端不再收帧，
   但那一轮的后台任务要继续跑完并落库；重进会话要能看到真实结果。
3. **刷新（F5）**：连接被浏览器掐断，页面重载后 phase/消息从哪里恢复？过程正文还在不在？

同时明确要求：**这些情况要单独写测试用例，不要每次跑整套**。

现状（已具备的底座，本次不重写，只补断言）：
- 需求轮 / 确认轮都在 `ExecutionRegistry` 后台任务里跑（2026-08-12 / 2026-11-03 两次收口），
  SSE 断连只取消订阅者，不取消图；
- 刷新后 `loadSessionSnapshot` 恢复 phase + 消息 + 版本，`useExecutionPoll` 每 5s 轮询到稳定态；
- 轮次结束会落 `turn_trace`（2026-11-05），所以**轮结束后**刷新能读回思考链路。

## Design（设计）

### D1 缓冲有界：`live` 去重 + 流式帧上限（后端）

`registry.publish` 目前把每一帧都 append 进 `entry.live`（迟到订阅者重放源）和 `events` 队列。
流式帧每 350ms 一帧、帧内还带最多 4000 字原文 → 一轮长任务能堆出几十 MB 的 `live`。收紧：

1. **`live` 只保留「同一 step 的最后一个 running 帧」**，终态帧全留——迟到订阅者仍能拿到
   完整终态序列，但重放不会再吐出上千个中间帧；
2. **流式帧硬上限** `MAX_STREAM_FRAMES = 600`（≈ 3.5 分钟持续流式，远高于实测 8–25s）：
   超过后新帧不再 publish（图继续跑、终态照发）——直接避免「无人订阅时队列无限增长」；
3. `publish` 在 `finished` 后仍是 no-op（已有语义，补测试钉住）。

### D2 中断时消息状态如实（前端）

`closeTrace` 现在只改步骤状态，**不动消息自身的 status** → 断流后那条助手消息永远停在
`streaming`（虽然界面上不明显，但 `ChatTurn.status` 会一直是「进行中」）。改为按原因落状态：

| 收尾原因 | 步骤状态 | 消息 status |
|---|---|---|
| `done` | success | `done` |
| `error` | error | `error` |
| `interrupted` | error | `error` |
| `stopped` | pending | 保持 `streaming`（后台还在跑，轮询会补终态） |

### D3 三类中断各自的恢复路径（只补测试，不改行为）

| 场景 | 期望行为 |
|---|---|
| SSE 断流（网络） | 前端 `onClose({sawTerminal:false})` → `STREAM_INTERRUPTED` + 气泡标错误 + 起轮询；后台跑完后快照回灌（卡片/报告/思考链路都在） |
| 用户停止 / 切会话 / 新建分析 / 登出 | 前端只断渲染；后台跑完落库；重进会话看到真实终态 |
| F5 刷新 | 快照恢复 phase；`parsing`/`generating` 期间显示「解析中/生成中」+ 轮询；**轮次结束前**过程正文没有（内存里），结束后随 `turn_trace` 回灌 |

### D4 独立用例（只跑这几个文件，不跑整套）

| 文件 | 覆盖 |
|---|---|
| `backend/tests/contracts/test_interruption_resilience.py`（新） | live 去重 / 流式帧上限 / finished 后 publish 无效 / 迟到订阅者重放顺序 / runner 异常与取消都唤醒订阅者 |
| `frontend/src/stores/__tests__/interruption.test.ts`（新） | 断流收尾（interrupted→error）/ 用户停止（stopped→pending 且仍 streaming）/ 错误收尾 / F5 中途快照（只有 user 行时当轮仍成立）/ 重放合并幂等 |
| `.e2elogs/probe_interrupt_midstream.mjs`（新） | 真机：流式途中 abort 连接 → 后台仍跑完 → 快照有 assistant_text + turn_trace |
| `.e2elogs/probe_refresh_midturn.mjs`（新） | 真机：parsing 中 F5 → 轮询恢复出确认条/卡片，无 console error |
| `.e2elogs/probe_close_page_midturn.mjs`（新） | 真机：关闭页面 → 后台跑完 → 新开页面看到终态 |

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/infra/execution/registry.py` | `publish` 的 `live` 去重（同 step running 只留最后一帧）+ `MAX_STREAM_FRAMES` 上限 |
| `frontend/src/stores/chatTranscript.ts` | `closeTrace` 同步落消息 status（done/error/中断），stopped 保持 streaming |
| 上述三个测试文件 + 三个探针 | 新增 |

## Reused existing utilities（复用工具）

- `ExecutionRegistry`（后台任务 + 迟到订阅者重放）——只收紧缓冲，不改生命周期；
- `_subscribe_events` / `useExecutionPoll` / `loadSessionSnapshot`（刷新恢复链路，已存在）；
- `chatTranscript.closeTrace` / `mergeServerHistory`（状态与历史合并，已存在）；
- 真机探针脚手架（`.e2elogs/probe_*.mjs`）+ 现有 `run_with_timeout`。

## Verification（验证）

只跑下面四条，不跑整套：

```bash
pytest backend/tests/contracts/test_interruption_resilience.py -q
npx vitest --run src/stores/__tests__/interruption.test.ts
node .e2elogs/probe_interrupt_midstream.mjs
node .e2elogs/probe_refresh_midturn.mjs
node .e2elogs/probe_close_page_midturn.mjs
```

## Explicitly NOT doing（不做事项）

- 不做**断点续传**（重连后继续接收这条流的中间帧）：重连走快照 + 轮询，这是既有产品语义；
- 不把「进行中」的 trace 落库（落库仍在轮次结束），因此 F5 后未完成轮次只有「解析中/生成中」；
- 不引入 WebSocket / 消息总线；
- 不改 SSE 事件面与前端事件契约。

---

## 落地记录（2026-11-08 14:00）

### 真机抓到 1 个真 bug（本次最主要的产出）

**解析中按 F5 回来，界面永远停在「正在解析」、发送框一直禁用**（`probe_refresh_midturn` 第一次跑就红）：
库里那一轮 7 秒后就跑完了（`awaiting_confirm`，`assistant_text` + `turn_trace` 都在），
但前端**根本没起轮询**——`loadSessionSnapshot` 的「忙」判定只认 `generating/adjusting`，
`parsing` 被判成「不忙」→ `setExecPolling(false)` → 再也没有人把结果刷回来。

修法：判定统一走 `isBusyPhase`（`parsing/generating/adjusting`，与轮询完成判定 `isSettledPhase`
同一张表），非法 phase 保持「不忙」。补了 2 个单测钉住（`resume parsing → busy=true`）。

### 实际改动

| 文件 | 改动 |
|---|---|
| `backend/app/infra/execution/registry.py` | `live` 里**同一 step 只保留最新一帧**（流式续帧与收尾帧都就地替换）；流式帧总量上限 `MAX_STREAM_FRAMES=600`（超过只停过程帧，终态照发） |
| `frontend/src/stores/sessionEvents.ts` | `loadSessionSnapshot` 的 busy 判定改 `isBusyPhase`（**F5 卡死的正解**） |
| `frontend/src/stores/chatTranscript.ts` | `closeTrace` 按原因同步消息 status：done→done、error/interrupted→error、stopped 保持 streaming |
| `backend/tests/contracts/test_interruption_resilience.py`（新） | 10 例：live 去重 / 终态就地替换 / 帧上限 / finished 后 no-op / 迟到订阅者重放顺序 / runner 异常与取消唤醒 / 同会话重入 Busy |
| `frontend/src/stores/__tests__/interruption.test.ts`（新） | 7 例：断流→error / 停止→pending 且仍 streaming / 停止后 done / F5 只有 user 行时当轮成立 / 轮次结束后快照补回内容 / 重复合并不翻倍 |
| `frontend/src/stores/__tests__/sessionEvents.test.ts` | +2 例（parsing→busy、稳定态→不忙） |
| `.e2elogs/probe_interrupt_midstream.mjs`（新） | 真机：流式途中 abort 连接 |
| `.e2elogs/probe_refresh_midturn.mjs`（新） | 真机：parsing 中 F5 |
| `.e2elogs/probe_close_page_midturn.mjs`（新） | 真机：跑到一半关掉整个浏览器 |

### 验证（只跑这几个文件，没跑整套）

```bash
pytest backend/tests/contracts/test_interruption_resilience.py backend/tests/contracts/test_reasoning_stream.py -q
# 23 passed

npx vitest --run src/stores/__tests__/interruption.test.ts src/stores/__tests__/sessionEvents.test.ts
# 18 passed
```

真机三条全绿：

| 探针 | 关键证据 |
|---|---|
| `probe_interrupt_midstream` **8/8 PASS** | 收到 2 个流式思考帧后 abort → 7s 后 phase 收敛 `awaiting_missing`；库里 `user:text` + `assistant:assistant_text` + `assistant:turn_trace(10 条)`；重进会话出现「已思考（用时 14.8 秒）」+ 确认条，无卡死标记 |
| `probe_refresh_midturn` **7/7 PASS** | 解析中 F5（`busy=true`）→ 重载后先显示解析卡（诚实忙碌态）→ 轮询把确认条刷回来；DB `awaiting_confirm`，内容齐全 |
| `probe_close_page_midturn` **7/7 PASS** | 跑到一半关浏览器 → 无任何前端在场，12s 后后台跑完；重开页面内容完整、无「进行中」残留 |

### 落地偏差 1 项

- `closeTrace(stopped)` **不**改消息 status（保持 `streaming`）：用户只是停止观看、后台还在跑，
  轮询补到终态后再落 `done`；把它标成 error 会让「后台其实成功了」的那一轮看起来失败。
