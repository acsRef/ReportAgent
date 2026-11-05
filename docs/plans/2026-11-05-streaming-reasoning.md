> 状态: 已完成

# 思考链路实时可见：流式 CoT + 预算可调（为什么被截断 / 怎么办 / 怎么看实时）

## Context（背景）

2026-11-05 用户追问三件事：

1. **为什么思考会被截断**——因为我们在直连调用里设了 `thinking_budget=512`（推理模型的思考 token 上限）。
   实测：`reasoning_tokens=508` 顶到预算，模型就把「半句话」的思维链收尾、转去写 JSON（`finish_reason` 仍是 `stop`）。
   而且 `max_tokens=1200` 是**思考 + 回答共享**的总额——只调大预算不调总额，回答会被挤掉。
2. **怎么办**——把预算与总额做成一对可调参数（默认给出安全组合），截断时标注（上一轮已做）。
3. **想实时看输出怎么调**——目前**没有开关**：调用是 `stream: false`，思维链要等节点跑完才一次性进 trace。
   实测 provider 支持流式并逐块返回 `reasoning_content`（第一个 delta 1.06s，1476 字/8.3s），所以要自己接。

## Design（设计）

### D1 预算与总额成对可调（默认组合不自相矛盾）

- `TURN_THINKING_BUDGET`（已有，默认 512）：思考预算；
- `TURN_MAX_TOKENS`（新，默认 `max(1200, budget + 900)`）：总额。**思考与回答共享**，
  只调预算不调总额 → 回答被截断 → JSON 解析失败（会表现为「理解失败」）。
- 二者关系写进 docstring + hand-off：`budget 512 → 总额 1412`，`budget 1024 → 1836`。
- 延迟代价：预算翻倍约等于思考时间翻倍；超时链路已有「不思考快速重试」，不会被它拖死。

### D2 流式思维链（provider → 后端 → SSE → 前端）

```
chat_with_reasoning(on_delta=…)   # stream=True，边收边回调累计文本
  → reasoning_stream.emit(text)   # 节流（默认 350ms）+ 组装 trace 帧
  → registry.publish(task, frame) # 现成 SSE 通道：trace 帧 status=running
  → 前端 withTraceEntry 按 nodeName 就地更新 → 思考块里的链路文字随之增长
```

约束（沿用既有契约）：
- **不新增 SSE 顶层事件类型**（P11 契约）：复用 `trace` 帧，`status=running` 期间带 `reasoning`；
- 帧内文本按 4000 字截断（与落库/展示口径一致），完整文本仍在最终 success 帧与 DB 里；
- 流式失败（网络/协议）→ 退回一次非流式调用；再失败才回落通用解析（现有语义不变）；
- `TURN_REASONING_STREAM=0` 可关掉（比如评测批量跑时省事）。

### D3 前端：链路边跑边长

`TracePanel` 已经把「有 `reasoning` 的条目」渲染成思考链路，流式帧只是让同一节点的
`reasoning` 变大 → React 就地重渲染。补两点：
- 运行中且链路已出现内容时，链路区显示一个「正在输出…」的小尾巴（避免用户以为输出完了）；
- 当轮 `autoOpen`（已有）保证一进来就看得见。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/llm/reasoning_client.py` | `chat_with_reasoning(on_delta=…)`：`stream=True` 解析 SSE；累计 content/reasoning；`TURN_MAX_TOKENS` 默认按预算推算 |
| `backend/app/infra/execution/reasoning_stream.py`（新） | contextvar sink + 节流发布（`TURN_STREAM_INTERVAL_MS`）+ 帧组装；永不抛错 |
| `backend/app/main.py` | 需求轮 runner 安装/复位流式 sink（publish 复用 `_publish_and_collect`） |
| `backend/app/agent/turn_understanding.py` | `understand_turn(on_reasoning=…)` 透传 + 流式失败退回非流式 |
| `backend/app/agent/requirement_analysis_graph.py` | `_turn_understand` 传 on_reasoning → `reasoning_stream.emit` |
| `frontend/src/components/workbench/TracePanel.tsx` | 运行中链路尾部「正在输出…」 |
| `frontend/src/styles/workbench.css` | 该尾巴的样式 |
| 测试 | 后端：流式帧组装/节流/开关关闭；前端：运行中链路渲染 |

## Reused existing utilities（复用工具）

- `ProgressTraceHandler` / `registry.publish`（现成 SSE 通道与帧格式）；
- `_publish_and_collect`（实时 publish + 落库收集，上一轮刚建）；
- 前端 `withTraceEntry`（按 nodeName 就地更新）+ `reasoningBlocks`（链路抽取）；
- `reasoning` 通道（contextvar 模式，本模块照抄同一套 install/reset 写法）。

## Verification（验证）

1. 离线：`pytest backend/tests/smoke backend/tests/contracts -q`；前端受影响单测；
2. 真机 `probe_reasoning_stream.mjs`：发一句 → **在需求轮结束前**轮询 DOM，观察
   ①思考块中「思考链路」文字长度单调增长 ②SSE 侧同一节点出现 ≥3 个 running 帧；
3. 真机（关闭开关）`TURN_REASONING_STREAM=0` 重启 → 只有最终一帧（回归安全）；
4. 预算可调：`TURN_THINKING_BUDGET=1024` + 默认总额 → 回答 JSON 仍完整（不出现「理解失败」）。

## Explicitly NOT doing（不做事项）

- 不把流式做成新的事件类型或新的前端 store 结构（复用 trace 帧 + 既有消息模型）；
- 不做「逐字打字机」动画（渲染的是累计文本，天然连续）；
- 不改 `LLM_TURN_TIMEOUT` 默认值；
- 不把流式用到 SQL/报告节点的 LLM（那是另一条链，先只服务 turn understanding）。

---

## 落地记录（2026-11-05 22:40）

### 实际改动

| 文件 | 改动 |
|---|---|
| `backend/app/llm/reasoning_client.py` | `_prepare()` 统一组装请求；`resolve_thinking_budget` / `resolve_max_tokens`（总额默认 `budget+900`，`TURN_MAX_TOKENS` 可覆盖）；`chat_with_reasoning(on_delta=…)` 走 `stream=True`；新 `chat_with_reasoning_async`（图节点用，不堵事件循环）；流式断流「有内容就尽力返回」 |
| `backend/app/infra/execution/reasoning_stream.py`（新） | contextvar sink + 节流（`TURN_STREAM_INTERVAL_MS` 默认 350ms）+ 组帧（`trace` / `status=running` / `step=理解意图 · 思考`）；`TURN_REASONING_STREAM=0` 可关 |
| `backend/app/agent/turn_understanding.py` | `understand_turn(on_reasoning=…)` + 新 `understand_turn_async`；`_finish_reply` / `_delta_forwarder` 抽公共逻辑（同步/异步行为一致） |
| `backend/app/agent/requirement_analysis_graph.py` | `_turn_understand` 改 `await understand_turn_async(..., on_reasoning=reasoning_stream.emit)` |
| `backend/app/main.py` | 需求轮 runner 安装/复位流式 sink（publish 复用 `_publish_and_collect`） |
| `frontend/src/components/workbench/TracePanel.tsx` | 链路「正在输出…」尾巴 + 流式时自动滚到底 |
| `frontend/src/stores/chatTranscript.ts` | 同节点续帧**保留最早时间戳**（否则流式每 350ms 换戳 → 「已等待 Ns」恒为 0、整轮耗时被压平） |
| 测试 | 新增 `backend/tests/contracts/test_reasoning_stream.py`（12 例：流式解析/断流/节流/开关/帧形态/预算成对）；前端 `TracePanel` +2、`chatTranscript` +1 |

### 验证（真机 + 离线）

`node .e2elogs/probe_reasoning_stream.mjs` **PASS 8/8**（一次真机需求轮）：

```
SSE：同一节点 13 个 running 帧，累计文本 2→995 字单调增长
     首个流式帧 0.7s，requirement 帧 6.1s（≈5.4s 的实时可见窗口）
UI ：链路文字采样 2 → 43 → 97 → 156 字（边跑边长）
     头部「▾ 正在思考（理解意图 · 思考 · 1s）」（等待秒数在流式中正常计数）
     结束时该节点仍是 success 帧、正文完整
```

离线：后端 `smoke + contracts` **694 passed**；前端 `tsc -b` 干净、`lint` 0 error、
受影响单测 **290 passed**。

### 落地偏差 2 项（都是验证时抓出来的）

1. **同步 httpx 堵死事件循环**：第一版在 async 节点里调同步流式客户端，8 个 running 帧
   **全部挤在 requirement 帧前 35ms** 才吐出来（前端看起来仍是「一次蹦出来」）。
   → 新增 `chat_with_reasoning_async`（`httpx.AsyncClient`）+ `understand_turn_async`，
   图节点改走 async 路径；实测首个流式帧提前到 0.7s。
2. **续帧时间戳被刷新**：流式帧每次都是新对象，`withTraceEntry` 直接用新时间戳 →
   「已等待 Ns」永远 0s。改为同节点保留 `min(旧, 新)`，并补了单测。

### 给用户的答案（三问）

1. **为什么截断**：`TURN_THINKING_BUDGET`（默认 512 token）是我们给推理模型设的思考上限；
   顶到上限后模型就把思维链收尾、转去写 JSON（`finish_reason` 仍是 `stop`）。
   注意 `max_tokens` 是**思考 + 回答共享**的总额，所以只调预算会把回答挤掉。
2. **怎么办**：`TURN_THINKING_BUDGET=1024` → 总额自动变 `1924`（或显式 `TURN_MAX_TOKENS`）。
   代价是每轮更慢、更容易撞 `LLM_TURN_TIMEOUT`；撞了会走「不思考快速重试」。
   不想调也行：截断现在会明确标注。
3. **怎么实时看**：**已经是默认行为**（`TURN_REASONING_STREAM` 默认开）——
   思考块里的链路文字边跑边长，带「正在输出…」尾巴和等待秒数；
   `TURN_REASONING_STREAM=0` 可关，`TURN_STREAM_INTERVAL_MS` 调推送频率（默认 350ms）。
