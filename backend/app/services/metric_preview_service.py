"""口径预览：给「用哪个口径」这类澄清补上真实数字。

plan 2026-11-04-requirement-slot-contract Phase 2；契约分界见 CLAUDE.md §4.1。

## 为什么需要

「2024年各区域表现」这种没点名口径的问法，卡片只能列出候选（销售额 / 净销售额 / 实收金额），
用户仍要自己回忆这几个业务口径差在哪。更有效的方式是**先算出来**：

    2024年销售额，按不同口径差异较大：
      · 销售额（订单金额，未扣退款）    2.61 亿
      · 净销售额（已扣退款）           2.42 亿
      · 实收金额（仅支付成功流水）      2.47 亿

让用户做识别题而不是填空题。

## 契约边界（CLAUDE.md §4.1，越界即视为违规）

- SQL **由注册表目录拼装**：`metrics.yaml` 的 `expr` / `joins` + 已知过滤条件，
  **无 LLM、无自由列名、无 GROUP BY**，只取一个总量；
- 只服务**澄清**：结果仅进 `RequirementOption.hint`，不产出数据表、不落库、不进 Report 链路；
- 走 `tools/sql_tools.execute_sql`（只读角色 + 安全闸 + EXPLAIN + statement timeout）；
- **fail-open**：时间解析不出、表关系不支持、执行报错/超时/为空 → `ok=False`，
  调用方退回纯文字选项，**绝不展示 0 元假数**。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from app.knowledge import registry as knowledge
from app.knowledge.registry import MetricDef
from app.reliability.timeout import run_with_timeout

logger = logging.getLogger(__name__)

# 预览是「交互反馈」而不是业务查询：超过这个时间就退回纯文字（不阻塞澄清）。
PREVIEW_TIMEOUT_S = 2.5


@dataclass(frozen=True)
class MetricPreview:
    name: str
    ok: bool
    value: Decimal | None = None
    reason: str = ""


@dataclass(frozen=True)
class PreviewResult:
    ok: bool
    items: tuple[MetricPreview, ...] = ()
    reason: str = ""


# --- 时间范围 → 半开区间 -----------------------------------------------------

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_YEAR_MONTH = re.compile(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*[-~至到]\s*(\d{1,2})\s*月)?")
_YEAR = re.compile(r"((?:19|20)\d{2})\s*年")
_ISO_RANGE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*[至~-]\s*(\d{4}-\d{2}-\d{2})")
_RECENT = re.compile(r"(?:最近|近|过去)\s*(\d{1,3}|[一二三四五六七八九十]{1,3})\s*(天|日|周|个?月|年)")


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _month_bounds(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    end_year, end_month = _add_months(year, month, 1)
    return datetime.date(year, month, 1), datetime.date(end_year, end_month, 1)


def resolve_period_bounds(
    time_range: str | None, today: datetime.date | None = None,
) -> tuple[datetime.date, datetime.date] | None:
    """把卡上的时间范围解析成半开区间 `[start, end)`；解析不出返回 None（fail-open）。

    只覆盖需求层会产出的确定性写法（年份 / 年月 / 月区间 / ISO 区间 / 今年 / 最近 N 天…），
    其余一律 None —— 预览宁可不出数，也不许拿错区间算出「看起来对」的数。
    """
    text = str(time_range or "").strip()
    if not text:
        return None
    now = today or datetime.date.today()

    m = _ISO_RANGE.search(text)
    if m:
        try:
            start = datetime.date.fromisoformat(m.group(1))
            end = datetime.date.fromisoformat(m.group(2)) + datetime.timedelta(days=1)
        except ValueError:
            return None
        return (start, end) if start < end else None

    m = _YEAR_MONTH.search(text)
    if m:
        start_month = int(m.group(2))
        if not 1 <= start_month <= 12:
            return None
        end_month = int(m.group(3)) if m.group(3) else start_month
        if not 1 <= end_month <= 12:
            return None
        start = datetime.date(int(m.group(1)), start_month, 1)
        end_year, end_next = _add_months(int(m.group(1)), end_month, 1)
        end = datetime.date(end_year, end_next, 1)
        return (start, end) if start < end else None

    m = _RECENT.search(text)
    if m:
        raw = m.group(1)
        amount = int(raw) if raw.isdigit() else _CN_NUM.get(raw)
        if amount is None:
            return None
        unit = m.group(2)
        days = {"天": amount, "日": amount, "周": amount * 7}.get(unit)
        if days is None:
            if unit.endswith("月"):
                days = amount * 31
            else:
                days = amount * 366
        end = now + datetime.timedelta(days=1)
        return (end - datetime.timedelta(days=days), end)

    if "今年" in text:
        return (datetime.date(now.year, 1, 1), datetime.date(now.year + 1, 1, 1))
    if "去年" in text:
        return (datetime.date(now.year - 1, 1, 1), datetime.date(now.year, 1, 1))

    m = _YEAR.search(text)
    if m:
        year = int(m.group(1))
        return (datetime.date(year, 1, 1), datetime.date(year + 1, 1, 1))
    return None


# --- 注册表目录 → SQL --------------------------------------------------------


def _parse_fk_chains() -> dict[str, dict[str, str]]:
    """`fk_chains` 文本 → {fact: {dim: 外键列}}，数据驱动，不硬编码表名。"""
    hints = knowledge._schema_hints()  # noqa: SLF001 - 同包数据访问（registry 未暴露解析器）
    chains: dict[str, dict[str, str]] = {}
    for line in hints.get("fk_chains") or []:
        text = str(line)
        fact, _, tail = text.partition(":")
        fact = fact.strip()
        if not fact or not tail:
            continue
        mapping: dict[str, str] = {}
        for part in tail.split(","):
            piece = part.strip()
            if "→" not in piece:
                continue
            column, _, dim = piece.partition("→")
            dim = dim.strip()
            if dim.startswith("dim_"):
                mapping[dim] = column.strip()
        chains[fact] = mapping
    return chains


def _base_fact(expr: str, metric_joins: tuple[str, ...]) -> str | None:
    """指标表达式的主事实表（表达式里出现的唯一 `fact_*`）。

    只看表达式本身、不看 joins：`净销售额` 的 joins 是退款子查询，里面虽然提到
    `fact_orders`，主表仍是表达式里的 `fact_orders`。表达式跨了两张事实表
    （多个 `fact_*`）时**不猜**——返回 None 交给 fail-open。
    """
    facts = list(dict.fromkeys(re.findall(r"\b(fact_[a-z_]+)\b", expr or "")))
    if len(facts) != 1:
        return None
    return facts[0]


def build_preview_sql(
    metric: MetricDef,
    *,
    time_range: str | None,
    scope: list[str] | None = None,
    today: datetime.date | None = None,
) -> str | None:
    """注册表指标 → 一条**总量**预览 SQL；不支持此形状返回 None（fail-open）。

    支持范围（确定性的小集合，刻意不扩展）：
    - 时间：`resolve_period_bounds` 认得的写法；
    - 范围：仅当注册表 `fk_chains` 声明了「该事实表 → dim_store」且维度词是区域；
    - 无 GROUP BY、无 ORDER BY，只取一个数。
    """
    bounds = resolve_period_bounds(time_range, today)
    if bounds is None:
        return None
    base = _base_fact(metric.expr, metric.joins)
    if not base:
        return None

    hints = knowledge._schema_hints()  # noqa: SLF001
    date_columns = (hints.get("time_dimension") or {}).get("default_join") or {}
    date_column = date_columns.get(base)
    if not date_column:
        return None

    joins = list(metric.joins)
    where = [f"{base}.{date_column} >= '{bounds[0].isoformat()}'",
             f"{base}.{date_column} < '{bounds[1].isoformat()}'"]

    regions = [s for s in (scope or []) if s and s != "ALL"]
    if regions:
        chains = _parse_fk_chains()
        store_fk = chains.get(base, {}).get("dim_store")
        region_column = knowledge.dimension_column("区域")
        if not store_fk or not region_column:
            # 该事实表到 dim_store 没有声明的外键链路 → 不能安静地忽略范围（会算错）
            return None
        joins.append(
            f"LEFT JOIN dim_store ON dim_store.{store_fk} = {base}.{store_fk}"
        )
        quoted = ", ".join("'" + r.replace("'", "''") + "'" for r in regions)
        where.append(f"{region_column} IN ({quoted})")

    sql = f"SELECT {metric.expr} AS value FROM {base}"
    if joins:
        sql += "\n" + "\n".join(joins)
    sql += "\nWHERE " + " AND ".join(where)
    return sql


# --- 最高/最低的那一期（2026-11-04，plan top-period-detail-intent） ------------

# 选期是执行链的关键一步，不像澄清预览那样可以随便退回——给更宽的预算。
TOP_PERIOD_TIMEOUT_S = 6.0


@dataclass(frozen=True)
class TopPeriodResult:
    """「销售额最高的那一期」的确定性解析结果。"""

    ok: bool
    unit: str = "month"
    # 人话标签：`2024年7月` / `2024年第41周`
    label: str = ""
    # 直接可用的 SQL 过滤条件（例如 `dim_date.year = 2024`、`dim_date.month = 7`）
    filters: tuple[str, ...] = ()
    # 期列 + 期值（供 SQL 校验判断「有没有真的只取这一期」）
    period_column: str = ""
    period_value: str = ""
    value: Decimal | None = None
    # 两个方向一起要时（direction=both）：每一期各一条
    entries: tuple[TopPeriodEntry, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class TopPeriodEntry:
    direction: str          # max / min
    label: str
    filters: tuple[str, ...]
    year: str = ""
    period: str = ""
    value: Decimal | None = None


_TOP_PERIOD_ORDER = {"max": "DESC", "min": "ASC"}


def build_top_period_sql(
    metric: MetricDef,
    *,
    unit: str,
    direction: str,
    time_range: str | None,
    scope: list[str] | None = None,
    today: datetime.date | None = None,
) -> tuple[str, str] | None:
    """注册表指标 → 一条「按 unit 聚合、取极值那一期」的查询。

    返回 `(sql, bucket_column)`；形状不支持时返回 None（fail-open，交回 LLM 两段式）。
    口径表达式 / 时间桶列 / JOIN 链路全部来自 `metrics.yaml` + `schema_hints.yaml`。
    """
    bounds = resolve_period_bounds(time_range, today)
    base = _base_fact(metric.expr, metric.joins)
    if not base:
        return None
    bucket = knowledge.time_bucket_column(unit)
    if not bucket:
        return None

    hints = knowledge._schema_hints()  # noqa: SLF001
    time_cfg = hints.get("time_dimension") or {}
    dim_table = str(time_cfg.get("table") or "")
    date_column = str(time_cfg.get("date_column") or "")
    fk_column = (time_cfg.get("default_join") or {}).get(base)
    if not dim_table or not date_column or not fk_column:
        return None

    joins = [
        f"LEFT JOIN {dim_table} ON {dim_table}.{date_column} = {base}.{fk_column}",
        *metric.joins,
    ]
    where: list[str] = []
    # 用户没说时间范围时**不加年份过滤**（覆盖全部数据），而不是默认「今年」——
    # 演示库数据是 2024，默认「今年」=2026 会直接查出空结果（2026-11-04 实测）。
    if bounds is not None:
        where.extend([
            f"{dim_table}.{date_column} >= '{bounds[0].isoformat()}'",
            f"{dim_table}.{date_column} < '{bounds[1].isoformat()}'",
        ])
    regions = [s for s in (scope or []) if s and s != "ALL"]
    if regions:
        chains = _parse_fk_chains()
        store_fk = chains.get(base, {}).get("dim_store")
        region_column = knowledge.dimension_column("区域")
        if not store_fk or not region_column:
            return None
        joins.append(
            f"LEFT JOIN dim_store ON dim_store.{store_fk} = {base}.{store_fk}"
        )
        quoted = ", ".join("'" + r.replace("'", "''") + "'" for r in regions)
        where.append(f"{region_column} IN ({quoted})")

    order = _TOP_PERIOD_ORDER.get(direction, "DESC")
    year_column = knowledge.time_bucket_column("year") or f"{dim_table}.year"
    where_sql = ("\nWHERE " + " AND ".join(where)) if where else ""
    sql = (
        f"SELECT {year_column} AS years, {bucket} AS period, {metric.expr} AS value\n"
        f"FROM {base}\n"
        + "\n".join(joins)
        + where_sql
        + f"\nGROUP BY {year_column}, {bucket}"
        + f"\nORDER BY value {order}\nLIMIT 1"
    )
    return sql, bucket


def _period_filters(unit: str, year: str, period: str, bucket: str) -> tuple[str, ...]:
    """(期值) → 可直接写进 SQL 的确定性过滤条件。"""
    table = knowledge._schema_hints().get("time_dimension", {}).get("table") or ""  # noqa: SLF001
    prefix = f"{table}." if table else ""
    year_column = knowledge.time_bucket_column("year") or f"{prefix}year"
    if unit == "day":
        date_column = knowledge.time_bucket_column("day") or bucket
        return (f"{date_column} = '{period}'",)
    if unit == "year":
        return (f"{year_column} = {period}",)
    unit_column = knowledge.time_bucket_column(unit) or bucket
    return (f"{year_column} = {year}", f"{unit_column} = {period}")


def _period_label(unit: str, year: str, period: str) -> str:
    if unit == "month":
        return f"{year}年{int(float(period))}月"
    if unit == "week":
        return f"{year}年第{int(float(period))}周"
    if unit == "quarter":
        return f"{year}年第{int(float(period))}季度"
    if unit == "year":
        return f"{period}年"
    return str(period)


async def resolve_top_period(
    metric: MetricDef | None,
    *,
    unit: str,
    direction: str,
    time_range: str | None,
    scope: list[str] | None = None,
    timeout: float = TOP_PERIOD_TIMEOUT_S,
    today: datetime.date | None = None,
) -> TopPeriodResult:
    """先算出「销售额最高/最低的那一期」，再把它变成确定性过滤条件。

    为什么要自己算而不是让 LLM 写两段式 SQL：实测模型会写成
    `SELECT ... ; SELECT ...` 两条语句（sqlglot 直接判语法错），或者干脆退回全区间汇总。
    选期是**确定性事实**，算一次比赌采样可靠。

    `direction="both"`（「最多和最小」）：最高、最低各算一次，`entries` 里各一条。
    """
    if metric is None:
        return TopPeriodResult(ok=False, reason="no_metric")
    if direction == "both":
        entries: list[TopPeriodEntry] = []
        for one in ("max", "min"):
            single = await resolve_top_period(
                metric, unit=unit, direction=one, time_range=time_range,
                scope=scope, timeout=timeout, today=today,
            )
            if not single.ok:
                return TopPeriodResult(ok=False, unit=unit, reason=single.reason or "both_failed")
            entries.append(TopPeriodEntry(
                direction=one,
                label=single.label,
                filters=single.filters,
                period=single.period_value,
                value=single.value,
            ))
        return TopPeriodResult(
            ok=True,
            unit=unit,
            label="、".join(entry.label for entry in entries),
            filters=tuple(f for entry in entries for f in entry.filters),
            period_column=knowledge.time_bucket_column(unit) or "",
            period_value=",".join(entry.period for entry in entries),
            entries=tuple(entries),
        )
    built = build_top_period_sql(
        metric, unit=unit, direction=direction, time_range=time_range,
        scope=scope, today=today,
    )
    if built is None:
        return TopPeriodResult(ok=False, unit=unit, reason="unsupported_shape")
    sql, bucket = built

    from app.tools import sql_tools

    try:
        raw = await run_with_timeout(asyncio.to_thread(sql_tools.execute_sql, sql), timeout)
    except asyncio.TimeoutError:
        logger.info("top-period resolve timeout: %s", unit)
        return TopPeriodResult(ok=False, unit=unit, reason="timeout")
    except Exception as exc:  # noqa: BLE001 - 解析失败退回两段式，不阻塞执行
        logger.warning("top-period resolve failed: %s", exc)
        return TopPeriodResult(ok=False, unit=unit, reason="error")

    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return TopPeriodResult(ok=False, unit=unit, reason="bad_payload")
    if not isinstance(payload, dict) or payload.get("error"):
        return TopPeriodResult(ok=False, unit=unit, reason="query_error")
    rows = payload.get("rows") or []
    if not rows:
        return TopPeriodResult(ok=False, unit=unit, reason="empty")

    row = rows[0] if isinstance(rows[0], dict) else {}
    year = str(row.get("years") if row.get("years") is not None else "")
    period = str(row.get("period") if row.get("period") is not None else "")
    if not period:
        return TopPeriodResult(ok=False, unit=unit, reason="empty")
    from app.utils.numbers import to_decimal

    return TopPeriodResult(
        ok=True,
        unit=unit,
        label=_period_label(unit, year, period),
        filters=_period_filters(unit, year, period, bucket),
        period_column=bucket,
        period_value=period,
        value=to_decimal(row.get("value")),
    )


# --- 执行（fail-open） ------------------------------------------------------


def _format_amount(value: Decimal, unit: str = "") -> str:
    amount = float(value)
    suffix = unit or ""
    if abs(amount) >= 1e8:
        return f"{amount / 1e8:.2f} 亿{suffix}"
    if abs(amount) >= 1e4:
        return f"{amount / 1e4:.1f} 万{suffix}"
    return f"{amount:,.0f} {suffix}".strip()


async def preview_metrics(
    metrics: list[MetricDef],
    *,
    time_range: str | None,
    scope: list[str] | None = None,
    timeout: float = PREVIEW_TIMEOUT_S,
    today: datetime.date | None = None,
) -> PreviewResult:
    """并发跑几条预览查询；任何一条失败只影响它自己，全失败则整组 fail-open。"""
    if not metrics:
        return PreviewResult(ok=False, reason="no_metrics")

    executors = []
    for metric in metrics:
        sql = build_preview_sql(metric, time_range=time_range, scope=scope, today=today)
        if sql is None:
            executors.append((metric, None))
        else:
            executors.append((metric, sql))

    if all(sql is None for _, sql in executors):
        return PreviewResult(ok=False, reason="unsupported_shape")

    async def _run(metric: MetricDef, sql: str) -> MetricPreview:
        from app.tools import sql_tools

        try:
            raw = await run_with_timeout(
                asyncio.to_thread(sql_tools.execute_sql, sql), timeout,
            )
        except asyncio.TimeoutError:
            logger.info("metric preview timeout: %s", metric.name)
            return MetricPreview(name=metric.name, ok=False, reason="timeout")
        except Exception as exc:  # noqa: BLE001 - 预览失败不影响澄清主流程
            logger.warning("metric preview failed: %s: %s", metric.name, exc)
            return MetricPreview(name=metric.name, ok=False, reason="error")

        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            return MetricPreview(name=metric.name, ok=False, reason="bad_payload")
        if not isinstance(payload, dict) or payload.get("error"):
            return MetricPreview(name=metric.name, ok=False, reason="query_error")
        rows = payload.get("rows") or []
        if not rows:
            return MetricPreview(name=metric.name, ok=False, reason="empty")
        first = rows[0]
        raw_value = first.get("value") if isinstance(first, dict) else None
        if raw_value is None:
            # 有些驱动按 columns 顺序返回；取第一个非空值
            raw_value = next(
                (v for v in (first.values() if isinstance(first, dict) else []) if v is not None),
                None,
            )
        from app.utils.numbers import to_decimal

        value = to_decimal(raw_value)
        if value is None:
            return MetricPreview(name=metric.name, ok=False, reason="not_numeric")
        return MetricPreview(name=metric.name, ok=True, value=value)

    tasks = [
        _run(metric, sql) if sql is not None
        else _skip(metric)
        for metric, sql in executors
    ]
    items = tuple(await asyncio.gather(*tasks))
    ok_count = sum(1 for it in items if it.ok)
    return PreviewResult(
        ok=ok_count >= 2,
        items=items,
        reason="" if ok_count >= 2 else "insufficient_previews",
    )


async def _skip(metric: MetricDef) -> MetricPreview:
    return MetricPreview(name=metric.name, ok=False, reason="unsupported_shape")


def format_hint(preview: MetricPreview, metric: MetricDef, *, base_note: str = "") -> str:
    """选项提示：`2.61 亿 · 订单金额口径，未扣退款`。"""
    if not preview.ok or preview.value is None:
        return base_note
    amount = _format_amount(preview.value, metric.unit)
    return f"{amount} · {base_note}" if base_note else amount
