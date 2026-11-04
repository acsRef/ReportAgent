"""业务口径与 schema 结构注册表（数据驱动）。

设计原则：**口径是数据，不是代码**。

此前 `sql_graph._SCHEMA_TERM_HINTS` / `_FK_CHAIN_HINTS` / `_PLAN_TABLE_HINTS`
是三个 Python 字符串常量，把表名、列名、口径写死在代码里——换一套 schema 就得
改代码，加一个指标也得改代码，而且这些字符串直接进了 prompt，等于把 schema
抄了两份（一份在 DDL，一份在代码）。

现在：`metrics.yaml`（指标怎么算）+ `schema_hints.yaml`（表怎么连）是数据文件；
本模块只负责加载、解析、渲染。换 schema 改 YAML，代码零改动。

加载失败（文件缺失 / YAML 语法错）时降级为空注册表并打 warning——不能让
一个知识文件把主链路打死；调用方用 `resolve_metric()` 拿到 None 后按「未知指标」
处理。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_KNOWLEDGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class MetricDef:
    name: str
    expr: str
    synonyms: tuple[str, ...] = ()
    # 指标依赖的 JOIN 子句（数据驱动）：生成 SQL 时必须一并带上，
    # 否则 expr 里引用的别名不存在。实测：净销售额的相关子查询写法在
    # GROUP BY 下非法，改 JOIN 后由这里声明依赖。
    joins: tuple[str, ...] = ()
    unit: str = ""
    note: str = ""
    default: bool = False

    def matches(self, term: str) -> bool:
        needle = _normalize(term)
        if not needle:
            return False
        return needle == _normalize(self.name) or needle in {
            _normalize(s) for s in self.synonyms
        }


def _normalize(text: str) -> str:
    """归一：去空白、去全角空格、转小写。指标名/同义词比对都用它。"""
    return re.sub(r"\s+", "", str(text or "")).lower()


def _load_yaml(filename: str) -> dict:
    path = _KNOWLEDGE_DIR / filename
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        logger.warning("knowledge file missing: %s", path)
    except yaml.YAMLError as exc:
        logger.warning("knowledge file invalid yaml (%s): %s", path, exc)
    return {}


@lru_cache(maxsize=1)
def _metrics() -> tuple[MetricDef, ...]:
    raw = _load_yaml("metrics.yaml").get("metrics") or []
    out: list[MetricDef] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("name") or not item.get("expr"):
            logger.warning("skip malformed metric entry: %r", item)
            continue
        out.append(MetricDef(
            name=str(item["name"]),
            expr=str(item["expr"]).strip(),
            synonyms=tuple(str(s) for s in (item.get("synonyms") or [])),
            joins=tuple(str(s).strip() for s in (item.get("joins") or [])),
            unit=str(item.get("unit") or ""),
            note=str(item.get("note") or ""),
            default=bool(item.get("default") or False),
        ))
    return tuple(out)


@lru_cache(maxsize=1)
def _schema_hints() -> dict:
    return _load_yaml("schema_hints.yaml")


# --- 指标 -------------------------------------------------------------------

def all_metrics() -> tuple[MetricDef, ...]:
    return _metrics()


def metrics_mentioned_in(text: str) -> list[MetricDef]:
    """从一段自由文本里找出提到的指标（名字或同义词），**最长命中优先**。

    用途：需求卡的 `target_metrics` 常常是语义名（`sales_trend` / `anomalies`），
    解析不到注册表；但用户确认过的口径假设里会写清「→ 已选：支付金额」这类话。
    这里做确定性词面匹配，把能认出来的指标捞出来，避免退回「整张注册表」。

    2026-11-04（slot-contract Phase 3）：改最长命中优先——子串匹配下「净销售额」
    会同时命中「销售额」，口径白名单被更泛的词带偏（`metric_hints` 会把错误的
    表达式渲染进 SQL prompt）。
    """
    haystack = _normalize(text)
    if not haystack:
        return []
    hits: list[tuple[int, MetricDef]] = []
    for metric in _metrics():
        best = 0
        for name in (metric.name, *metric.synonyms):
            needle = _normalize(name)
            if needle and needle in haystack:
                best = max(best, len(needle))
        if best:
            hits.append((best, metric))
    if not hits:
        return []
    longest = max(length for length, _ in hits)
    found: list[MetricDef] = []
    seen: set[str] = set()
    for length, metric in hits:
        if length != longest or metric.name in seen:
            continue
        seen.add(metric.name)
        found.append(metric)
    return found


def resolve_metric(term: str) -> MetricDef | None:
    """指标名/同义词 → MetricDef；未命中返回 None（调用方按「未知指标」处理）。"""
    for metric in _metrics():
        if metric.matches(term):
            return metric
    return None


def resolve_metrics(terms: list[str]) -> tuple[list[MetricDef], list[str]]:
    """批量解析；返回 (已解析, 未解析的原词)。"""
    resolved: list[MetricDef] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for term in terms or []:
        metric = resolve_metric(term)
        if metric is None:
            unresolved.append(term)
        elif metric.name not in seen:
            seen.add(metric.name)
            resolved.append(metric)
    return resolved, unresolved


def render_metric_hints(only: list[str] | None = None) -> str:
    """渲染成「业务口径 → 表达式」文本，供 plan/generate prompt 使用。

    `only` 给出指标名白名单时**只渲染这些**（2026-11-03：需求卡已经解析出用户要的
    指标，却把整张注册表灌进 prompt，模型于是把销售额/净销售额/订单数/销量/客单价/
    退款率一起 SELECT——答案不聚焦，还多算了一堆没人要的列）。
    白名单为空 / 未给 → 渲染全部（没有卡片时的兜底）。
    """
    allowed = {_normalize(n) for n in (only or []) if str(n).strip()}
    lines = ["业务口径 → SQL 表达式（优先采用，禁止发明列名/指标）:"]
    rendered = 0
    for metric in _metrics():
        if allowed and _normalize(metric.name) not in allowed:
            continue
        aliases: list[str] = []
        for alias in (metric.name, *metric.synonyms[:3]):
            if alias not in aliases:
                aliases.append(alias)
        suffix = f"  # {metric.note}" if metric.note else ""
        lines.append(f"- {'/'.join(aliases)} → {metric.expr}{suffix}")
        rendered += 1
    if rendered == 0:
        return ""
    return "\n".join(lines)


# --- schema 结构 -------------------------------------------------------------

def render_fk_chain_hints() -> str:
    chains = _schema_hints().get("fk_chains") or []
    if not chains:
        return ""
    return "事实表 → 维度表外键链路:\n" + "\n".join(f"- {c}" for c in chains)


def render_table_hints() -> str:
    hints = _schema_hints()
    facts = hints.get("facts") or []
    dims = hints.get("dimensions") or []
    if not facts and not dims:
        return ""
    parts = []
    if facts:
        parts.append(f"事实表: {', '.join(facts)}")
    if dims:
        parts.append(f"维度表: {', '.join(dims)}")
    return "常用表速查:\n" + "\n".join(parts)


def render_plan_few_shot() -> str:
    """渲染 plan few-shot（数据驱动；此前写死在 sql_graph._PLAN_FEWSHOT）。"""
    items = _schema_hints().get("plan_few_shot") or []
    blocks: list[str] = []
    for idx, item in enumerate(items, 1):
        user = str(item.get("user") or "").strip()
        output = str(item.get("output") or "").strip()
        if not user or not output:
            continue
        blocks.append(f"[示例{idx}]\n用户: \"{user}\"\n输出: {output}")
    return "\n\n".join(blocks)


def render_sql_generation_rules() -> str:
    """渲染 SQL 生成规则（数据驱动；此前写死在 sql_graph._SQL_GENERATION_RULES）。"""
    rules = _schema_hints().get("sql_generation_rules") or []
    blocks = [str(r).strip() for r in rules if str(r).strip()]
    return "\n\n".join(blocks)


def dimension_column(term: str) -> str | None:
    """维度业务词 → 物理列（区域 → dim_store.region）。"""
    needle = _normalize(term)
    for item in _schema_hints().get("dimension_terms") or []:
        if _normalize(item.get("term", "")) == needle:
            return str(item.get("column") or "") or None
    return None


# --- 物理列名 → 中文标签（2026-11-03：卡片不允许出现 order_amount / region 这种列名） ---

def label_for_metric_column(column: str) -> str | None:
    """物理列 → 业务指标名（fact_orders.order_amount → 销售额）。"""
    needle = _normalize(column).split(".")[-1]
    if not needle:
        return None
    for metric in _metrics():
        if needle in _normalize(metric.expr):
            return metric.name
    return None


def label_for_dimension(column: str) -> str | None:
    """物理维度列 → 中文标签（dim_store.region → 区域；dim_date.month → 月份）。"""
    needle = _normalize(column).split(".")[-1]
    if not needle:
        return None
    for item in _schema_hints().get("dimension_terms") or []:
        col = _normalize(item.get("column", "")).split(".")[-1]
        if col and col == needle:
            return str(item.get("term") or "") or None
    buckets = (_schema_hints().get("time_dimension") or {}).get("buckets") or {}
    for granularity, col in buckets.items():
        if _normalize(col).split(".")[-1] == needle:
            return {"day": "日期", "week": "周", "month": "月份",
                    "quarter": "季度", "year": "年份"}.get(granularity, needle)
    return None


def time_bucket_column(granularity: str) -> str | None:
    """粒度 → dim_date 分组列（month → dim_date.month）。"""
    buckets = (_schema_hints().get("time_dimension") or {}).get("buckets") or {}
    return buckets.get(granularity)


def time_dimension_terms() -> tuple[str, ...]:
    """时间类维度词汇（趋势判断用；与表名无关）。"""
    return ("时间", "日期", "月份", "月", "周", "季度", "年份", "年", "天", "日")


def display_label(column: str) -> str | None:
    """物理列名/别名 → 给用户看的中文标签（认不出返回 None）。

    2026-11-04（plan 2026-11-04-requirement-slot-contract 收尾）：报告表格与图表的
    表头此前**直通 SQL 别名**，模型偶尔漏写中文别名（实测
    `SELECT dim_date.week_of_year AS 周, dim_date.week_of_year AS week_of_year`）时
    界面上就会冒出 `week_of_year` / `category` 这种物理列名。

    这里只做**确定性映射**（数据全部来自 metrics.yaml + schema_hints.yaml）：
    1. 指标列 → 业务口径名（payment_amount → 实收金额）；
    2. 维度列 / 时间桶 → 中文维度名（region → 区域；week_of_year → 周）。
    """
    name = str(column or "").strip()
    if not name:
        return None
    label = label_for_metric_column(name)
    if label:
        return label
    return label_for_dimension(name)


def clear_cache() -> None:
    """测试用：改完 YAML 需要重新加载时调用。"""
    _metrics.cache_clear()
    _schema_hints.cache_clear()
