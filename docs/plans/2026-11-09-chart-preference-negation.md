> 状态: 已完成

# 记忆偏好的否定语义（「以后不要用柱状图」不能被读成「要柱状图」）

## Context

用户问「长期记忆冲突了怎么办」。核对时发现一个**会反转用户意图**的真缺陷（真机探针实测）：

```
chart_advisor 给 pie，记忆里是「以后不要用柱状图」 → bar     ← 意图被反转
对照：记忆里是「以后都用柱状图」                  → bar
```

链路两端不对等：
- **当轮原话通道**（`app/report/preference.py::explicit_chart_type`）有否定词防护：词前 3 字出现
  `不/别/无需/不是/非` 就跳过 —— 所以「不要柱状图」在当时不会生效；
- **记忆偏好通道**（`_preferred_chart_type`）是裸 substring 匹配，没有否定判定。

而「以后不要用柱状图」确实会进长期记忆：`MemoryPolicy.extract_preference` 能抽（命中「以后…用…」），
准入判成 `long-term-marker → active/user`，于是它作为**偏好**被召回并强制执行——用户明确说不要的
东西，被系统当成要的东西。这不是理论风险，是已复现的行为。

## Design

1. 把否定判定抽成公用函数 `_is_negated(text, index)`（词前 3 字窗口 + 同一套否定词表），
   两个通道共用同一真源，避免再出现「A 通道有防护、B 通道漏了」。
2. 记忆通道从「返回单个类型」升级为 **（正向命中, 排除集合）**：
   `_parse_chart_preference(text) -> (type|None, set[type])`。
3. 应用规则（`apply_chart_preference`）：
   - 先扫全部偏好，累积 `excluded`（否定是**约束**，与排序无关，取并集）；
   - 再按召回分顺序取第一条**正向命中且不在 excluded** 的类型作为目标；
   - 若没有任何正向命中、但当前 chart 类型被排除 → 退到「未被排除的那个可视化类型」；
     都不可用则保持原样（数据硬约束仍然优先，§七）。
4. 语义定调：**否定句存成“排除项”，不存成对立的偏好**。用户说「不要柱状图」不等于
   「要饼图」——选什么是 chart_advisor 的事，记忆只负责排除。

## Files to change

| 文件 | 改动 |
|---|---|
| `backend/app/report/preference.py` | 抽 `_is_negated`；`_preferred_chart_type` → `_parse_chart_preference`（正向 + 排除）；`apply_chart_preference` 用排除集合落地 |
| `backend/tests/contracts/test_chart_preference.py` | 补否定矩阵：纯否定 / 否定+肯定并存 / 否定命中当前类型时降级到另一可视化类型 / 数据为 table 时保持让位 |

## Reused existing utilities

- `app/report/preference.py::_EXPLICIT_CHART_WORDS` / `_NEGATIONS` / `_apply_type`：词表与落地逻辑原样复用，只补否定聚合。
- `_TYPE_TO_WORD`：排除项 → 人话，便于日志。

## Verification

```bash
python -m pytest backend/tests/contracts/test_chart_preference.py -q
python -m pytest backend/tests/persistence/test_report_chart_preference_behavior.py -q   # 真 PG 行为不回归
```

验收：
1. `apply_chart_preference(pie, ["以后不要用柱状图"])` → **pie 不变**（此前会变 bar）；
2. `apply_chart_preference(bar, ["以后不要用柱状图"])` → 降级成 pie（排除当前类型时换另一可视化）；
3. `apply_chart_preference(pie, ["以后不要用柱状图", "以后都用饼图"])` → pie（正向命中且未被排除）；
4. `apply_chart_preference(table, ["以后不要用柱状图"])` → table（数据不支持可视化，偏好让位，§七）。

## Explicitly NOT doing

- **不做全局否定词表**（不只图表）：粒度/口径的否定语义需要 `memory_key` 槽位体系，另开。
- **不改写入侧**：否定偏好仍然要存（它是用户的明确长期意图），只是解释方式从“要”改成“排除”。
- **不动 `explicit_chart_type` 的既有行为**：只把它的否定判定抽出来复用，行为不变。
## 落地记录（2026-11-09）

修完并验证（`app/report/preference.py`）：

| 场景 | 修前 | 修后 |
|---|---|---|
| pie + 「以后不要用柱状图」 | bar（意图反转） | **pie**（不动） |
| bar + 「以后不要用柱状图」 | bar | **pie**（排除当前类型 → 换另一个可视化） |
| pie + [「不要柱状图」, 「都用饼图」] | bar | **pie**（正向命中且未被排除） |
| table + 「不要柱状图」 | table | **table**（数据硬约束让位，§七 不变） |
| pie + 「都用柱状图」 | bar | bar（回归不破） |
| pie + 「别用饼图」 | pie | **bar**（对称性） |

实现要点：
- `_is_negated(text, index, window=3)` 成为**唯一**否定判据，`explicit_chart_type` 与
  `_parse_chart_preference` 共用——这正是缺陷成因（两条通道各写一半）；
- `_parse_chart_preference` 返回 `(正向命中, 排除集合)`：同一条文本里可以同时有肯定与否定；
- `_preferred_chart_type` 把排除集合取**并集**（约束与顺序无关），正向命中按召回分取第一条；
- 兜底 `_first_not_negated`：当前类型被排除时才换另一个可视化类型，两个都被排除就保持原样（不猜）。

测试：`backend/tests/contracts/test_chart_preference.py` 新增 6 条否定矩阵（19 passed 总计）；
回归 `pytest backend/tests/contracts` + chart 行为 + explicit preference API → **912 passed**。

### 仍未做（与本 plan 的 NOT doing 一致）

- 粒度/口径的否定语义（「以后别按月」）不在本次：需要 `memory_key` 槽位体系，另开 plan。
- 写入侧不变：否定偏好仍然照常存（它是明确长期意图），只是解释方式从“要”改为“排除”。