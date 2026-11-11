# 运行节点图（交互版）

一份把**当前代码里真实的 LangGraph 拓扑**画出来的可交互页面：5 张图 / 41 个节点 / 46 条边，
每个节点可点开看职责、输入输出、失败分支、代码行号与**实测耗时**，并能按真实 trace 顺序回放。

|  | 位置 |
|---|---|
| 页面（唯一真源） | 博客仓库 `source/graph/index.html` |
| 线上 | https://acsref.github.io/graph/ |
| 配套文章 | https://acsref.github.io/posts/runtime-node-map/ |

页面不在本仓库里，是有意的：它是**博客产物**（自包含单文件，无构建、无外链依赖），
放两份必然漂移。本文件只记录「它是怎么来的、怎么更新」。

## 数据从哪来（不是手绘）

**拓扑**：逐个文件抄 `add_node` / `add_edge` / `add_conditional_edges` 的目标节点名。

| 图 | 文件 | 节点 | 边 |
|---|---|---|---|
| ① 需求理解图 | `requirement_analysis_graph.py:842` | 14 | 17 |
| ② 确认执行图 | `confirmed_execution_graph.py:1390` | 11 | 11 |
| ③ SQL 子图 | `sql_graph.py:1649` | 8 | 11 |
| ④ 报告子图 | `report_graph.py:554` | 4 | 4 |
| ⑤ 数据准备子图 | `data_graph.py:86` | 4 | 3 |

**耗时**：跑一次真实会话，然后查 trace 表（不要用日志估算）：

```sql
select json_agg(json_build_object(
         'name', span_name, 'type', span_type, 'ms', duration_ms,
         'start', to_char(start_time, 'HH24:MI:SS.MS'), 'status', status)
       order by start_time)
from observability.agent_trace_span
where trace_id in (
  select trace_id from observability.agent_trace
  where session_id = '<你的 session_id>'
);
```

一次两阶段请求约 67 个 span，其中 `span_type='NODE'` 的 33 个就是回放序列
（`DATA` 是节点输出快照，`TOOL` 是工具调用，`embedding` 也走 TOOL）。

## 怎么更新

1. 结构变了（加/删节点边）：改页面里 `GRAPHS` 数组，节点用
   `N(id, label, x, y, kind, {...})`，边用 `E(from, to, {label, d, dash, loop, at})`。
   `d` 是手写 SVG 路径，只给「直连会穿过别的节点」的边用。
2. 耗时变了：改 `TRACE` 数组（`[图 key, 节点 id, 毫秒]`）和各节点的 `ms` 字段。
3. 改完必须跑一遍结构化自检（悬空边 / 节点矩形重叠 / 越界 / trace 引用了不存在的节点）——
   页面里没有编译期检查，这些错只会表现为「线画到空白处」。
4. 博客侧：`skip_render: ['graph/**']` 保证 Hexo 原样拷贝；`npx hexo clean && npx hexo generate`。

## 已知的表达边界（页面上也写了）

- **子图不是 LangGraph 的 subgraph**：`sql_agent` / `report_agent` 是父图节点，内部 `.ainvoke()`
  调另一张编译好的图，所以 checkpoint 只存父 State。
- **0ms 节点是真的 0ms**：`evaluate` / `diagnose` / `build_output` 是纯函数，不碰网络不碰库。
- 同一个节点名可能出现在两张图里（`data_detect_intent` 在需求图与确认图的 `data_agent` 里各跑一次），
  页面用 `[图 key, 节点 id]` 二元组消歧。
