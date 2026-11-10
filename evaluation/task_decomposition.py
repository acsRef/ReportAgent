"""子任务诊断 + 复杂度分档：把「SQL 错了」拆成「哪一步错了」。

为什么需要这一层（2026-11-10，承 `execution_accuracy`）：

Execution Accuracy 只说「结果不一致」，**不说差在哪**。BEAVER（arXiv 2409.02038）把
Text-to-SQL 拆成可分别评分的子任务：表定位 / join 键 / 列映射 / 谓词 / 分解结构。
只有拆开才知道该修哪一层——是 schema 检索没找对表，还是表都对了但 join 键写错。

做法：把**金标 SQL** 与**被测 SQL** 各自解析成结构件（sqlglot），逐项比覆盖率：

| 子任务 | 比什么 | 失败意味着 |
|---|---|---|
| `tables` | 物理表集合（金标 ⊆ 被测） | schema 检索 / 表定位没找对 |
| `join_keys` | 等值 join 键对（`表.列=表.列`，别名已解析） | 表对了但关联条件错（扇出、漏 join 落这里） |
| `columns` | 投影的列引用与聚合表达式 | 口径选错、少一列、多一列 |
| `predicates` | WHERE/HAVING 过滤原子（去表限定） | 时间/区域/状态过滤丢失或写错 |
| `structure` | 有无 CTE、聚合种类集合 | 分解方式不同（该预聚合却在同一层聚合） |

判定与 `compare_result_sets` 同口径：**只看金标有没有被覆盖**（recall）。被测多出来的
表/谓词只记录不判错——多带一个无害的维表不该算失败。

复杂度分档由**金标结构**推导（不人标、可复现）：T1 单表 / T2 星型维表 join /
T3 预聚合或多口径或明细。分档只用于「按难度切片看准确率」，不是给 case 打质量分。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

# 子任务固定顺序：越靠前越「上游」。归因时取**第一个**失败项，因为表都没找对时
# 谓词必然也不同，全记会把每个子任务都算成高频。
SUBTASK_ORDER: tuple[str, ...] = ("tables", "join_keys", "columns", "predicates", "structure")

_AGG_CLASSES: tuple[tuple[type, str], ...] = (
    (exp.Sum, "sum"),
    (exp.Count, "count"),
    (exp.Avg, "avg"),
    (exp.Min, "min"),
    (exp.Max, "max"),
)


def _norm_ident(text: str) -> str:
    return str(text or "").strip().strip('"').casefold()


def _aggregate_kind(agg: exp.Expression) -> str:
    kind = next((label for cls, label in _AGG_CLASSES if isinstance(agg, cls)), "agg")
    if isinstance(agg, exp.Count) and agg.find(exp.Distinct) is not None:
        return "count_distinct"
    if agg.find(exp.Distinct) is not None:
        return f"{kind}_distinct"
    return kind


def _alias_map(root: exp.Expression) -> dict[str, str]:
    """本查询内 alias/表名 → 真实表名（裸表名也映射到自己）。"""
    mapping: dict[str, str] = {}
    for table in root.find_all(exp.Table):
        name = _norm_ident(table.name)
        if not name:
            continue
        mapping[name] = name
        if table.alias:
            mapping[_norm_ident(table.alias)] = name
    return mapping


def _column_ref(column: exp.Column, aliases: dict[str, str]) -> str:
    """列引用归一成 `表.列`（能解析到表就带上表名）。"""
    table = _norm_ident(column.table)
    name = _norm_ident(column.name)
    if table:
        return f"{aliases.get(table, table)}.{name}"
    return name


def _aggregate_spec(agg: exp.Expression, aliases: dict[str, str]) -> str:
    """聚合表达式归一：`SUM(fact_orders.order_amount)` / `count(*)`。"""
    inner = ", ".join(
        _column_ref(column, aliases) for column in agg.find_all(exp.Column)
    )
    if not inner and isinstance(agg, exp.Count):
        inner = "*"
    return f"{_aggregate_kind(agg)}({inner})"


def _strip_qualifiers(node: exp.Expression, aliases: dict[str, str]) -> str:
    """过滤原子归一：折小写、压空白、**去掉表限定**（别名不同不算差异）。

    谓词比的是「这个条件还在不在」，不是「用哪个别名写的」——金标写 `d.year`、
    被测写 `dim_date.year`，是同一件事。
    """
    text = node.sql(dialect="postgres").replace('"', "").casefold()
    for qualifier in sorted(aliases, key=len, reverse=True):
        text = text.replace(f"{qualifier}.", "")
    return " ".join(text.split())


def _flatten_and(condition: exp.Expression) -> list[exp.Expression]:
    """把 WHERE/HAVING 的 AND 链拆成原子（`a AND b` → [a, b]）。"""
    if isinstance(condition, exp.And):
        return _flatten_and(condition.left) + _flatten_and(condition.right)
    if isinstance(condition, exp.Paren):
        return _flatten_and(condition.this)
    return [condition]


@dataclass(frozen=True)
class SqlParts:
    """一条 SQL 的结构件（只记**物理事实**，不记写法）。"""

    tables: tuple[str, ...] = ()
    join_keys: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    predicates: tuple[str, ...] = ()
    aggregate_kinds: tuple[str, ...] = ()
    aggregate_sources: tuple[str, ...] = ()
    uses_cte: bool = False
    has_aggregate: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "tables": list(self.tables),
            "join_keys": list(self.join_keys),
            "columns": list(self.columns),
            "predicates": list(self.predicates),
            "aggregate_kinds": list(self.aggregate_kinds),
            "aggregate_sources": list(self.aggregate_sources),
            "uses_cte": self.uses_cte,
            "has_aggregate": self.has_aggregate,
        }


def decompose(sql: str) -> SqlParts | None:
    """SQL → 结构件；解析失败或顶层不是 SELECT 返回 None（**不猜**）。"""
    text = str(sql or "").strip()
    if not text:
        return None
    try:
        parsed = sqlglot.parse_one(text, dialect="postgres")
    except Exception:  # noqa: BLE001 - 解析失败只表示「无法诊断」，不是被测错误
        return None
    if not isinstance(parsed, exp.Select):
        return None

    aliases = _alias_map(parsed)

    # CTE 名不是物理表：`WITH sales AS (...) ... FROM sales` 里 sales 是查询自己造的名字。
    cte_names: set[str] = set()
    with_clause = parsed.args.get("with_")
    if with_clause is not None:
        cte_names = {_norm_ident(cte.alias_or_name) for cte in with_clause.expressions}

    tables: list[str] = []
    for table in parsed.find_all(exp.Table):
        name = _norm_ident(table.name)
        if name and name not in cte_names and name not in tables:
            tables.append(name)

    join_keys: list[str] = []
    for join in parsed.find_all(exp.Join):
        on = join.args.get("on")
        if on is None:
            continue
        for equal in on.find_all(exp.EQ):
            left, right = equal.left, equal.right
            if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                continue
            # CTE 之间的 join（`refunds r JOIN sales s ON r.category = s.category`）是
            # 查询内部的逻辑连接，不是物理关联——金标用 CTE、被测用扁平写法时它必然不同，
            # 但两者的执行结果可以完全一致。只比**物理** join 键。
            # 注意要先用别名表把 `r` / `s` 还原成 CTE 名，否则按别名判会漏掉这一类。
            left_owner = aliases.get(_norm_ident(left.table), _norm_ident(left.table))
            right_owner = aliases.get(_norm_ident(right.table), _norm_ident(right.table))
            if left_owner in cte_names or right_owner in cte_names:
                continue
            pair = sorted((_column_ref(left, aliases), _column_ref(right, aliases)))
            key = f"{pair[0]}={pair[1]}"
            if key not in join_keys:
                join_keys.append(key)

    columns: list[str] = []
    # 聚合口径：**整条 SQL（含 CTE/子查询内部）**的聚合表达式——口径的真身，带表名才有意义。
    # 只看顶层投影会漏掉「预聚合再取用」这种形态（`WITH sales AS (SELECT SUM(...) AS amt)`），
    # 而那正是 T3 最该被诊断的地方。
    for agg in parsed.find_all(exp.AggFunc):
        spec = _aggregate_spec(agg, aliases)
        if spec not in columns:
            columns.append(spec)
    # 投影维度列：只认顶层、非聚合、纯列引用，且**只比列名**——表前缀与别名属于写法差异，
    # CTE 投影（`s.category`）更是没法直接解析到物理表。
    for projection in parsed.expressions:
        # `s.region AS region` 是 exp.Alias(Column)，不是 Column——先解包再判类型。
        node = projection.unalias() if isinstance(projection, exp.Alias) else projection
        if isinstance(node, exp.Column) and not list(node.find_all(exp.AggFunc)):
            name = _norm_ident(node.name)
            if name and name not in columns:
                columns.append(name)

    predicates: list[str] = []
    # 过滤条件按**整条 SQL** 收集（含 CTE/子查询）：同一个 `year = 2024` 写在 CTE 里还是
    # 写在外层，是写法差异，不是「条件丢了」。只看顶层会把预聚合写法误判成没有过滤。
    for clause in [*parsed.find_all(exp.Where), *parsed.find_all(exp.Having)]:
        condition = getattr(clause, "this", clause)
        for leaf in _flatten_and(condition):
            atom = _strip_qualifiers(leaf, aliases)
            if atom and atom not in predicates:
                predicates.append(atom)

    aggregates = list(parsed.find_all(exp.AggFunc))
    kinds = sorted({_aggregate_kind(agg) for agg in aggregates})
    sources = sorted({
        aliases.get(_norm_ident(column.table), _norm_ident(column.table))
        for agg in aggregates
        for column in agg.find_all(exp.Column)
        if _norm_ident(column.table)
    })

    return SqlParts(
        tables=tuple(sorted(tables)),
        join_keys=tuple(sorted(join_keys)),
        columns=tuple(sorted(columns)),
        predicates=tuple(sorted(predicates)),
        aggregate_kinds=tuple(kinds),
        aggregate_sources=tuple(sources),
        uses_cte=with_clause is not None,
        has_aggregate=bool(aggregates),
    )


@dataclass(frozen=True)
class SubtaskDiagnosis:
    """一次「金标 vs 被测」的子任务诊断。"""

    ok: bool
    failed: tuple[str, ...] = ()
    details: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "failed": list(self.failed), "details": self.details}


def _diff(gold: tuple[str, ...], pred: tuple[str, ...]) -> tuple[list[str], list[str]]:
    return [item for item in gold if item not in pred], [item for item in pred if item not in gold]


def diagnose(gold_sql: str, pred_sql: str) -> SubtaskDiagnosis | None:
    """金标 SQL vs 被测 SQL → 子任务诊断；任一侧无法解析返回 None。

    覆盖率语义：金标的表 / join 键 / 列 / 谓词都必须出现在被测里（多出来的只记录）。
    """
    gold = decompose(gold_sql)
    pred = decompose(pred_sql)
    if gold is None or pred is None:
        return None

    details: dict[str, dict[str, list[str]]] = {}
    failed: list[str] = []

    for name, gold_items, pred_items in (
        ("tables", gold.tables, pred.tables),
        ("join_keys", gold.join_keys, pred.join_keys),
        ("columns", gold.columns, pred.columns),
        ("predicates", gold.predicates, pred.predicates),
    ):
        missing, extra = _diff(gold_items, pred_items)
        if missing or extra:
            details[name] = {"missing": missing, "extra": extra}
        if missing:
            failed.append(name)

    structure: dict[str, list[str]] = {}
    # 只比**聚合种类**（少算一种聚合=少做一次计算，是正确性信号）。
    # 「有没有用 CTE」刻意不比：扁平写法与预聚合写法可以给出完全一致的结果，
    # 按它判失败只会制造假阳性（真机上 `complex-sql-sales-vs-returns` 撞到过）。
    if set(gold.aggregate_kinds) != set(pred.aggregate_kinds):
        structure["aggregate_kinds"] = [
            f"gold={list(gold.aggregate_kinds)}",
            f"pred={list(pred.aggregate_kinds)}",
        ]
    if structure:
        details["structure"] = structure
        failed.append("structure")

    return SubtaskDiagnosis(ok=not failed, failed=tuple(failed), details=details)


def complexity_tier(sql: str) -> int | None:
    """金标 SQL → 复杂度分档（1/2/3）；解析不了返回 None。

    - **T1**：单表、无 join；
    - **T2**：星型直连（事实表 join 维表），单来源聚合；
    - **T3**：预聚合（CTE/子查询）、多口径（两个来源各自聚合），或明细（无聚合）。
    """
    parts = decompose(sql)
    if parts is None:
        return None
    if parts.uses_cte or not parts.has_aggregate:
        return 3
    if len(parts.aggregate_sources) >= 2:
        return 3
    if parts.join_keys or len(parts.tables) > 1:
        return 2
    return 1


def tier_accuracy(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按复杂度分档聚合（只统计有金标**且有分档**的 case）。"""
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        tier = row.get("complexity_tier")
        if tier is None or row.get("result_matches") is None:
            continue
        key = f"T{tier}"
        bucket = buckets.setdefault(key, {"cases": 0, "matched": 0, "accuracy": None})
        bucket["cases"] += 1
        if row["result_matches"]:
            bucket["matched"] += 1
    for bucket in buckets.values():
        bucket["accuracy"] = (
            round(bucket["matched"] / bucket["cases"], 4) if bucket["cases"] else None
        )
    return dict(sorted(buckets.items()))


def subtask_failure_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """**结果不一致**的 case 里，首个失败子任务的计数（回答「这批主要卡在哪一步」）。

    只看 `result_matches is False` 的行：结构差异在结果一致时是**合法替代写法**
    （扁平 vs 预聚合），把它算进「失败归因」会制造假阳性——真机上一条 20 行里就撞到过。
    """
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("result_matches") is not False:
            continue
        failed = (row.get("diagnosis") or {}).get("failed") or []
        if not failed:
            continue
        head = next((name for name in SUBTASK_ORDER if name in failed), failed[0])
        counts[head] = counts.get(head, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))
