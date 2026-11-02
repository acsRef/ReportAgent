# 出报告后的追问必须回到需求层（修「七月份」被答成全年 + 年份漂移）

> 状态: 已完成

## Context

用户在有报告后追问「详细和我讲一下七月份的呢」，得到的是**同一张卡的第二份报告**：
需求卡仍是 `2024年 / 华东 / 时间+区域`，SQL 与 v1 逐字相同。

两层根因：

1. **路由**：`phase=report_ready` 时前端发 `mode=adjust`，后端 `_chat_confirmed_execution` 直接开跑执行图，**不再经过需求解析**。卡片不动，SQL 自然不动。
2. **年份漂移（更危险）**：把追问当 supplement 重新解析后，新卡 `time_range` 变成「七月份」（丢了 2024），SQL 生成器按 today（2026）解析成 `order_date >= '2026-07-01'`——**静默返回另一年的数字**。报表看起来完全正常，这是最难发现的一类错。

## Design

### 1) report_ready 后的追问改走需求层

`chatModeForPhase(report_ready)`：`adjust` → **`supplement`**。

`supplement` 路径已有「继承上一轮卡片 + 重新解析」的能力（P15 T2），于是追问会把新诉求合进卡片：新卡 `summary=详细分析七月份的数据`、`time_range=2024年7月`，其余约束（华东、趋势+异常）继承。

代价：追问后需要再点一次「确认并生成报告」。这是有意的——需求变了就该让用户看一眼新卡。

### 2) 年份继承（确定性）

`requirement_parser._inherit_year()`：新卡只有月份、没有年份，而上一轮卡里有年份 → 合并成「2024年7月」。中文月份（七/十一）转数字，`7月` 保持。

放在需求层而不是 prompt：年份是**已确认的事实**，不该让模型按 today 再猜一次。

## Files to change

| 文件 | 变更 |
|---|---|
| `frontend/src/components/workbench/phaseText.ts` | `report_ready` → `supplement` |
| `frontend/src/components/workbench/__tests__/phaseText.test.ts` | 断言改为 supplement |
| `backend/app/agent/requirement_parser.py` | `_inherit_year()` + 在 `_merge_prior_card` 里调用；补 `import re` |
| `backend/tests/contracts/test_supplement_time_inheritance.py` | 新增 5 例（中文月份 / 已有年份不覆盖 / 无时间继承 / prior 无年份 / 阿拉伯数字） |

## Reused existing utilities

- `requirement_parser._merge_prior_card`（P15 T2 的 supplement 继承）
- `requirement_analysis_graph` 的 prior_card 读取与持久化
- 既有 `phaseText.chatModeForPhase` 与前端 mode 分发

## Verification

真链路（`probe_followup.py`）：

```
首轮卡片: time_range=2024年  scope=[华东]  methods=[trend_analysis, anomaly_detection]
v1: SUCCESS / 12 行
追问「详细和我讲一下七月份的呢」→ 新卡: time_range=2024年7月, status=complete
v2: SUCCESS / 1 行
WHERE: region='华东' AND year=2024 AND month=7 AND order_date >= '2024-07-01' …
```

修复前同一条追问：`order_date >= '2026-07-01'`（按 today 解析）。

## Explicitly NOT doing

- 不改 `adjust` 的落库语义（`persist_adjust_run` / `parent_version`）——本轮走 supplement，版本链由既有 confirm 路径推进
- 不做「追问自动确认」——需求变了必须让用户看到新卡
- 不处理「上个月/去年同期」这类相对时间（本轮只补年份，相对时间另开）

## 落地记录（2026-11-02）

两轮真链路：

1. 只改前端路由（adjust → supplement）后：新卡 `time_range=七月份`，SQL 落到 **2026-07** —— 静默错年。
2. 补 `_inherit_year()` 后：新卡 `2024年7月`，SQL `year=2024 AND month=7`，1 行。

回归：backend **1066 passed / 1 skipped**（+5）；frontend **324 passed**。
