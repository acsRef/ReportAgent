"""Execution Accuracy：**结果对不对**，而不是「SQL 跑没跑通」。

为什么单独立这一层（2026-11-10，用户拍板）：

`evaluation/runner.py` 原本的头号指标是 `sql_success_rate`，它衡量的是
「SQL 执行成功 + 有行」——这是 **execution success**，不是 **execution accuracy**。
BEAVER（arXiv 2409.02038）的结论正是这条：检索、schema linking、join 键、领域编码
全是**上游手段**，召回得准不代表最终 SQL 对；唯一能闭环的判据是**执行结果与金标一致**。

本模块提供确定性比较：
- 列按**归一化列名**对齐（去空白/全角空格/大小写），与列顺序无关；
- 行按**多重集**比较（顺序无关），数值统一成 Decimal 后按容差比；
- 字符串只去首尾空白，**不做大小写折叠**（编码类字段大小写有意义）。

不做的事（都写进 docstring 而不是偷偷做）：不比较列别名以外的类型、不忽略重复行、
不对 NULL 与空串做等价（它们是不同的结果）。
"""
from __future__ import annotations

import datetime
import decimal
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

DEFAULT_FLOAT_TOL = decimal.Decimal("0.01")
_MAX_DIFF_SAMPLES = 3


@dataclass(frozen=True)
class MatchResult:
    """一次结果比对的结论。`reason` 用于归因，不用于打分。"""

    ok: bool
    reason: str
    gold_rows: int = 0
    pred_rows: int = 0
    missing: list[tuple] = field(default_factory=list)
    extra: list[tuple] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "gold_rows": self.gold_rows,
            "pred_rows": self.pred_rows,
            "missing": [list(r) for r in self.missing],
            "extra": [list(r) for r in self.extra],
        }


def normalize_column(name: str) -> str:
    """列名归一：去空白（含全角）、折大小写。仅用于**对齐列**，不改值。"""
    return "".join(str(name or "").split()).replace("\u3000", "").casefold()


def to_decimal(value: Any) -> decimal.Decimal | None:
    """数值统一成 Decimal；不可转的返回 None（留给字符串比较）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, decimal.Decimal):
        return value
    if isinstance(value, int):
        return decimal.Decimal(value)
    if isinstance(value, float):
        return decimal.Decimal(str(value))
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        try:
            return decimal.Decimal(text)
        except (decimal.InvalidOperation, ValueError):
            return None
    return None


def normalize_value(value: Any) -> Any:
    """单元格归一：数值→Decimal，时间→ISO，其余→去空白字符串（None 保持 None）。"""
    if value is None:
        return None
    number = to_decimal(value)
    if number is not None:
        return number
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    return str(value).strip()


def _values_equal(a: Any, b: Any, tol: decimal.Decimal) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, decimal.Decimal) and isinstance(b, decimal.Decimal):
        return abs(a - b) <= tol
    return a == b


def _row_key(row: Sequence[Any]) -> tuple:
    return tuple(normalize_value(v) for v in row)


def _match_rows(
    gold: Iterable[tuple], pred: Iterable[tuple], tol: decimal.Decimal
) -> tuple[list[tuple], list[tuple]]:
    """多重集匹配：返回 (gold 里没被匹配上的, pred 里多出来的)。

    贪心即可：先按精确相等消耗，再用容差匹配剩余数值行。
    """
    gold_left = list(gold)
    pred_left = list(pred)

    # 第一轮：完全相等
    for row in list(pred_left):
        for i, g in enumerate(gold_left):
            if len(g) == len(row) and all(
                _values_equal(x, y, decimal.Decimal(0)) for x, y in zip(g, row)
            ):
                gold_left.pop(i)
                pred_left.remove(row)
                break

    # 第二轮：数值容差
    for row in list(pred_left):
        for i, g in enumerate(gold_left):
            if len(g) == len(row) and all(
                _values_equal(x, y, tol) for x, y in zip(g, row)
            ):
                gold_left.pop(i)
                pred_left.remove(row)
                break

    return gold_left, pred_left


def compare_result_sets(
    gold_columns: Sequence[str],
    gold_rows: Sequence[Sequence[Any]],
    pred_columns: Sequence[str],
    pred_rows: Sequence[Sequence[Any]],
    *,
    float_tol: decimal.Decimal | float | str = DEFAULT_FLOAT_TOL,
    match_columns: bool = False,
) -> MatchResult:
    """金标结果 vs 被测结果 → `MatchResult`。

    默认按 **BEAVER 口径只看值**：列名（别名）不同不算错，但**列数必须一致**，
    行按多重集比较。为什么默认不看列名：别名是表述习惯（`region AS 区域` vs `AS region`），
    执行结果对不对与它无关；要严格钉列名时显式传 `match_columns=True`。

    判错顺序（reason 直接用于归因）：
    1. `column-count` / `column-mismatch`：列数不同 / （严格模式下）列名集合不同；
    2. `row-count`：行数不同；
    3. `values`：行数相同但存在不一致（附缺失/多出的样例行）。
    """
    tol = (
        float_tol if isinstance(float_tol, decimal.Decimal) else decimal.Decimal(str(float_tol))
    )
    gold_cols = [normalize_column(c) for c in gold_columns]
    pred_cols = [normalize_column(c) for c in pred_columns]
    if len(gold_cols) != len(pred_cols):
        return MatchResult(
            ok=False, reason="column-count",
            gold_rows=len(gold_rows), pred_rows=len(pred_rows),
        )
    if match_columns and set(gold_cols) != set(pred_cols):
        return MatchResult(
            ok=False, reason="column-mismatch",
            gold_rows=len(gold_rows), pred_rows=len(pred_rows),
        )

    order = [pred_cols.index(c) for c in gold_cols] if match_columns else list(range(len(gold_cols)))
    pred_aligned = [[row[i] for i in order] for row in pred_rows]
    gold_keys = [_row_key(r) for r in gold_rows]
    pred_keys = [_row_key(r) for r in pred_aligned]

    missing, extra = _match_rows(gold_keys, pred_keys, tol)
    if not missing and not extra:
        return MatchResult(ok=True, reason="match", gold_rows=len(gold_rows), pred_rows=len(pred_rows))
    reason = "row-count" if len(gold_rows) != len(pred_rows) else "values"
    return MatchResult(
        ok=False, reason=reason,
        gold_rows=len(gold_rows), pred_rows=len(pred_rows),
        missing=missing[:_MAX_DIFF_SAMPLES], extra=extra[:_MAX_DIFF_SAMPLES],
    )


def accuracy_summary(matches: Sequence[MatchResult | None]) -> dict[str, Any]:
    """聚合 Execution Accuracy：只统计**有金标**的 case（None 表示该 case 没标金标）。"""
    scored = [m for m in matches if m is not None]
    if not scored:
        return {"with_golden": 0, "matched": 0, "execution_accuracy": None}
    matched = sum(1 for m in scored if m.ok)
    reasons: dict[str, int] = {}
    for m in scored:
        if not m.ok:
            reasons[m.reason] = reasons.get(m.reason, 0) + 1
    return {
        "with_golden": len(scored),
        "matched": matched,
        "execution_accuracy": round(matched / len(scored), 4),
        "failure_reasons": reasons,
    }
