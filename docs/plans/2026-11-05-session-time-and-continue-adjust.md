> 状态: 已完成

# 会话时间差 8 小时 + 「继续调整」没反应 + 清空测试会话

## Context（背景）

用户在一次真机试用后提了三件事（2026-11-05）：

1. **「无法继续调整」**——报告出完之后再发一句，界面看起来什么都没发生；
2. **「已经发出去的消息找不到了，用起来很奇怪 别扭」**；
3. **「左侧的时间也是错的，怎么可能就是八小时」** + 「把这些没用的数据都清理掉」。

## 取证

### A. 左侧时间差 8 小时（根因确定）

| 事实 | 证据 |
|---|---|
| PostgreSQL 服务端时区 `Etc/UTC` | `show timezone` |
| `agent.session.updated_at` 由 `NOW()` 写入 → 存的是 **UTC naive** | 21:57 的会话存成 13:57 |
| `SessionManager.create_session` 用 `datetime.now()`（**本地 naive**）写 `created_at` | 同一会话 created_at=21:57 / updated_at=13:57（差 8h） |
| API 直接 `dt.isoformat()` → 无时区字符串 | `2026-11-05T10:02:42.324911` |
| 浏览器 `Date.parse()` 把无时区字符串当**本地时间** | 于是「刚生成」显示成「8 小时前」 |

### B. 「无法继续调整」（真机复现）

Playwright 跑完整两轮（`probe_live_multiturn.mjs`）：报告 v1 出来后再发一句
「再加上华南区域对比」→ 之后 **2 分钟里界面上只有 v1**，只有 confirm bar 悄悄出现
（在长报告下方，用户看不见）。会话在库里停在 `awaiting_confirm`。

即：报告后的追问走 supplement 重新解析 → 停在「等待确认」，**要用户再点一次确认**；
而用户的心智是「我在接着改这份报告」，于是体感为「我发了话，没反应」。

### C. 「消息找不到」

对比库与界面（`probe_history_visibility2.mjs`）：`dbQuestions` 与 `domBubbles` 数量一致
（2 条 → 2 个气泡，含历史轮）。**消息没有丢**——是 B 的副作用：一轮追问之后没有任何
新结果产生，用户找不到「这一轮的结果」，误以为上下文丢了。

### D. 测试数据

`agent.session` 1176 行，绝大多数是 `e2e- / eval- / demo- / realq- / slot- / probe- / smoke- / matrix-…`
的测试与探针会话；`requirement_draft` 2145、`report_version` 864、`app.conversations` 2314。

## Design（设计）

### D1 时间戳统一：写入用 UTC，读出带时区

- `app/utils/timefmt.py::utc_iso()`：naive（库里是 UTC）→ 带 `+00:00` 的 ISO；
- 所有喂前端的序列化点改用它：`conversation/repository`（messages、session `updated_at`、
  版本 `created_at`）、`snapshot_service`（`confirmed_at`/`created_at`）、`main.py`（报告详情）；
- `SessionManager.create_session` 改用数据库 `NOW()`，与 `updated_at` 同一时钟；
- 数据迁移：`agent.session.created_at` 全部是本地 naive（1176 行**全部**满足
  `created_at > updated_at`），统一 `- interval '8 hours'` 回到 UTC。

### D2 报告后的追问：完整卡自动确认

前端记住「这一轮是接着上一份报告改的」（发送时 `phase === 'report_ready'`）：
解析结果若是 `status=complete` 且没有待问槽位 → **自动走既有确认流程**，直接产出新版本；
需要澄清时照旧停下来问（不牺牲理解）。

—— 为什么不在后端自动确认：确认/执行是独立后台任务（registry 的 `confirm` kind），
在 requirement 任务里再起一个会撞 `SESSION_BUSY`；前端复用既有 `handlePatchAndConfirm`
（PATCH → /confirm 流）语义最省、且已有错误处理。

### D3 数据清理

只保留最近更新的 10 个会话，其余连同 `requirement_draft` / `report_version` /
`app.conversations` / `checkpoints` 一并删除（外键顺序：session → version → draft → conversations）。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/utils/timefmt.py` | 新增 `utc_iso()` |
| `backend/app/infra/conversation/repository.py` | 3 处时间序列化 |
| `backend/app/services/snapshot_service.py` | 2 处 |
| `backend/app/main.py` | 报告详情 `created_at` |
| `backend/app/infra/checkpoint/session.py` | `created_at` 改 `NOW()` |
| `frontend/src/pages/WorkbenchPage.tsx` | `autoConfirmRef` + 自动确认 effect |

## Verification（验证）

1. API：`GET /api/v1/sessions` → `updated_at=2026-11-05T10:02:42.324911+00:00`（带时区）。
2. 界面：左侧时间 `刚刚 / 6 分钟前 / 12 分钟前`，昨天的是 `18 小时前`（`rail_time_fixed.png`）。
3. 真机两轮：报告 v1 后发「再加上华南区域对比」→ **无需点确认**，直接出
   「报告 v2 · 分析2024年华东与华南区域的销…」，两条提问气泡都在、v1 仍可查看。
4. 清理后：session 10 / draft 79 / version 26 / conversations 22。

## Explicitly NOT doing（不做事项）

- 不改 ragent-py 的时区/库配置（两项目共用一个库，改服务端时区会波及 RAG 侧）；
- 不在后端 requirement 任务里串联 confirm（会撞 registry 的单会话任务锁）；
- 不实现「自动确认也自动展开新报告」的滚动逻辑（先保证能生成）。

## 追加（同日，用户：「右边那些东西也是点不动的」）

### 全量可点性审计（33 个 button，Playwright 命中测试 + 点击观测）

| 结论 | 数量 | 明细 |
|---|---|---|
| 可点且有反应 | 23 | 导航 / 会话行 / 版本切换 / 收起报告 / 收藏（PATCH 落库）/ 导出 Excel（GET blob）/ 导出 HTML（本地生成下载）/ 继续调整（聚焦输入框）/ 聚焦内容 / 展开更多明细 |
| 视口外（需滚动，非缺陷） | 10 | 报告工具栏在长报告下方，`top≈1665` |
| **被遮挡 → 点不动（真缺陷）** | 1 | 左侧列表最后一行被 `.wb-left-footer` 盖住：`.wb-session-scroll` 少了 `min-height: 0`，flex 子项被内容撑高溢出。已修（`flex: 1 1 0; min-height: 0`），重跑命中测试 = 0 被遮挡 |

### 右栏两处「看起来能点，点了没反应」

1. **推荐继续分析**：原来只把文字塞进底部输入框（无提示、无滚动），视觉上等于没反应。
   改为**直接发出去**（`handleSend`），并 toast「继续分析：<那句话>」；任务进行中点击会提示稍等。
   实测：点击 → `POST /api/v1/chat` + 问题气泡出现。
2. **当前需求范围**标签：只读展示（本就不该点），补了 `title` 说明「改需求直接在下方输入框说」，
   避免它长得像按钮。

## 验证记录

- `probe_hit_test.mjs`：修复前后对比，COVERED 从 1 → 0；
- `probe_suggestion_click.mjs`：点击右栏推荐 → 发出 `POST /chat`，输入框上方出现新问题气泡；
- `probe_all_buttons2.mjs`：报告工具栏五个按钮均产生网络请求或界面变化。
