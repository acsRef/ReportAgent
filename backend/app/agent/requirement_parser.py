"""LLM-driven requirement parser.

`parse_requirement` takes a Chinese natural-language query plus the
available `SchemaContext` and produces a `RequirementCard`. The LLM is
asked only to identify which dimensions are present / missing /
ambiguous; the option labels themselves are server-side controlled
(see `requirement_options.py`).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from app.context import format_context_block
from app.llm import budgets, call_llm
from app.utils.text import safe_json_parse
from app.models.contracts import SchemaContext
from app.models.requirement import (
    RequirementAssumption,
    RequirementCard,
    RequirementFieldKey,
    RequirementFieldKind,
    RequirementMissingField,
    RequirementOption,
    RequirementStatus,
)
from app.agent import requirement_options as opts
from app.agent.prompts import build_requirement_parse_prompt
from app.knowledge import registry as knowledge

logger = logging.getLogger(__name__)


# --- LLM call --------------------------------------------------------------


# C-7: prompt 里的 schema 文本必须有界。表/列描述随业务增长可膨胀到几十 K，
# 直接挤爆 LLM 上下文窗口。单表描述截到 160 字，整体再套一个硬上限。
MAX_SCHEMA_CHARS = 8000
_MAX_TABLE_DESC = 160


def _schema_columns(schema: SchemaContext | None) -> set[str]:
    """本 session 可用字段全集：`table.column` 与裸 `column` 两种写法都收。"""
    names: set[str] = set()
    for table in (schema.tables if schema else []) or []:
        tname = str(getattr(table, "name", "") or "")
        for col in getattr(table, "columns", None) or []:
            cname = str(getattr(col, "name", "") or "")
            if not cname:
                continue
            names.add(cname)
            if tname:
                names.add(f"{tname}.{cname}")
    return names


def _option_column(opt: dict) -> str:
    col = opt.get("column")
    if col:
        return str(col)
    # 兼容：value 形如 "metric:fact_orders.order_amount" / "scope:华东"
    value = str(opt.get("value") or "")
    if ":" in value:
        tail = value.split(":", 1)[1]
        if "." in tail:
            return tail
    return ""


def _build_clarifications(
    raw_items: object, schema: SchemaContext | None,
) -> list[RequirementMissingField]:
    """把模型输出的 `clarifications` 变成受控的缺失项。

    规则（服务端兜底，不只信模型）：
    - `column` 必须存在于本 session 的 schema（`table.column` 或裸列名），否则该选项丢弃；
    - 剩下的有效选项 < 2 → 整条丢弃（说明并不歧义）；
    - 选项 value 保持模型给的前缀编码（intent:/metric:/time:/scope:），由
      requirement_service 翻译成卡体字段。
    """
    from app.models.requirement import RequirementOption

    available = _schema_columns(schema)
    out: list[RequirementMissingField] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()[:64]
        question = str(item.get("question") or item.get("label") or "").strip()[:120]
        if not key or not question:
            continue
        valid: list[RequirementOption] = []
        for opt in item.get("options") or []:
            if not isinstance(opt, dict) or not opt.get("label") or not opt.get("value"):
                continue
            column = _option_column(opt)
            if column and available and column not in available:
                logger.warning(
                    "clarification option dropped (unknown column): key=%s column=%s",
                    key, column,
                )
                continue
            valid.append(RequirementOption(
                label=str(opt["label"])[:60],
                value=str(opt["value"])[:80],
            ))
        if len(valid) < 2:
            continue
        out.append(RequirementMissingField(
            key=key, label=question, kind="single", options=valid,
        ))
    return out


def _schema_text(ctx: SchemaContext | None) -> str:
    """把 MCP 检索到的表结构渲染进 prompt。

    2026-11-03 修正：此前只渲染「表名 + 描述」，**把 columns 整个丢了**——
    MCP 明明返回了字段（data_graph 已经建出 ColumnSchema），模型却看不到
    `order_amount / region / month` 这些列，于是无法「根据可用字段判断哪里
    不清楚」，只能靠猜（实测把「2024年的销售情况」猜成了趋势分析）。
    """
    if not ctx or not ctx.tables:
        return "无可用表结构"
    lines: list[str] = []
    for t in ctx.tables:
        desc = (t.description or "")[:_MAX_TABLE_DESC]
        cols = [
            f"{c.name}:{c.type}" if getattr(c, "type", "") else str(c.name)
            for c in (t.columns or [])
        ][:_MAX_COLUMNS_PER_TABLE]
        col_text = "、".join(cols) if cols else "（无字段信息）"
        lines.append(f"- {t.name}（{desc}）\n    字段: {col_text}")
    text = "\n".join(lines)
    if len(text) > MAX_SCHEMA_CHARS:
        text = text[:MAX_SCHEMA_CHARS] + "\n...（schema 已截断）"
    return text


# C-7 同款边界：字典片段来自外部 RAG，长度不受信任。
# 超过此上限的尾部会被截断，避免撑爆 LLM 上下文窗口。
MAX_DICTIONARY_CHARS = 4000

# 单表最多渲染多少列（零售星型模型最宽的表 ~12 列，留余量但不让宽表撑爆 prompt）。
_MAX_COLUMNS_PER_TABLE = 40


def _format_prior_block(card: RequirementCard) -> str:
    """把上一轮确认卡渲染成 prompt 的「已确认需求」块（P15 e2e T2）。

    只列非空字段；granularity/comparison 不在卡体显式字段里，经 dimensions/
    analysis_methods hint 传递。空行由调用方（parse_requirement）用空串短路。
    """
    from app.models.requirement import RequirementCard as _RC  # noqa: F401  # 类型已 import
    rows = [f"- 上一轮需求目标（已确认）: {card.summary or ''}"]
    if card.time_range:
        rows.append(f"- 时间范围: {card.time_range}")
    if card.scope:
        rows.append(f"- 范围: {'/'.join(card.scope)}")
    if card.target_metrics:
        rows.append(f"- 指标: {'/'.join(card.target_metrics)}")
    if card.dimensions:
        rows.append(f"- 维度/粒度: {'/'.join(card.dimensions)}")
    if card.analysis_methods:
        rows.append(f"- 分析方法: {'/'.join(card.analysis_methods)}")
    return "\n".join(rows)


def _dim_kind(dim: str) -> str:
    """dimensions hint 的 kind 标签：granularity / comparison / base。"""
    if "粒度" in dim:
        return "granularity"
    if any(k in dim for k in ("对比", "比较", "同比", "环比")):
        return "comparison"
    return "base"


_GRANULARITY_ALIASES: dict[str, str] = {
    "day": "day", "日": "day", "每日": "day", "按日": "day", "天": "day",
    "week": "week", "周": "week", "每周": "week", "按周": "week",
    "month": "month", "月": "month", "每月": "month", "按月": "month", "月份": "month",
    "quarter": "quarter", "季度": "quarter", "按季度": "quarter",
    "year": "year", "年": "year", "按年": "year", "每年": "year",
}


def _normalize_granularity(raw: object) -> str | None:
    """把 LLM 输出的时间粒度归一成 RequirementGranularity（中文/英文/空）。

    认不出来就返回 None（不猜）——猜错粒度会让 SQL 的分组维度整体错位。
    """
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    return _GRANULARITY_ALIASES.get(text)


# --- 粒度确定性判定（2026-11-03，plan 2026-11-03-anomaly-granularity）--------
#
# 实测：「2024年哪几天销售额异常」模型返回 granularity=None → dimensions 里的宽时间词
# 「时间」被自由发挥成 GROUP BY year → 1 行，用户点名的「哪几天」彻底丢失。粒度是
# 确定性事实（用户原话就写着），不该赌采样——与 sql_graph._apply_confirmed_granularity
# 同一条理由。线索词都取多字词，避免单字「月/周」误伤。
# 判定顺序 day → week → month → quarter → year：越细的粒度越先命中。
_GRANULARITY_QUERY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("day", ("每天", "每日", "按日", "逐日", "日粒度", "单日", "按天", "哪天", "哪几天", "哪一日",
             "每一天")),
    ("week", ("每周", "按周", "逐周", "周粒度", "哪一周", "哪周", "周同比", "周环比",
              "每个星期", "每个礼拜")),
    ("month", ("每月", "按月", "各月", "月度", "哪个月", "月粒度", "每个月", "逐月")),
    ("quarter", ("每季度", "按季度", "季度粒度", "哪个季度", "每个季度")),
    ("year", ("每年", "按年", "年度", "哪一年", "每一年")),
)


def _granularity_from_query(user_query: str) -> str | None:
    """从**用户原话**判时间粒度；认不出返回 None（不猜）。"""
    text = (user_query or "").lower()
    for granularity, hints in _GRANULARITY_QUERY_HINTS:
        if any(h in text for h in hints):
            return granularity
    return None


_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_int(text: str) -> int | None:
    """「7」「十二」「二十」「三十」→ int；认不出返回 None。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    if raw == "十一":
        return 11
    if raw == "十二":
        return 12
    if "十" in raw:
        head, _, tail = raw.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(raw)


_RECENT_SPAN = re.compile(
    r"(?:最近|近|过去)\s*(\d{1,3}|[一二三四五六七八九十]{1,3})\s*(天|日|周|个?月|年)"
)
_YEAR_MONTH_SPAN = re.compile(
    r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*[-~至到]\s*(\d{1,2})\s*月)?"
)
_YEAR_SPAN = re.compile(r"((?:19|20)\d{2})\s*年")
_HALF_YEAR_HINTS = ("上半年", "下半年")


def _range_days(time_range: str | None) -> int | None:
    """把卡上的时间范围估成天数（只用于选粒度，不做业务判断）。

    认不出返回 None——调用方按「长跨度」处理（偏向周而不是日）。
    """
    text = str(time_range or "").strip()
    if not text:
        return None
    if any(k in text for k in _HALF_YEAR_HINTS):
        return 183
    m = _RECENT_SPAN.search(text)
    if m:
        amount = _cn_int(m.group(1))
        if amount is None:
            return None
        unit = m.group(2)
        if unit in ("天", "日"):
            return amount
        if unit == "周":
            return amount * 7
        if unit.endswith("月"):
            return amount * 31
        return amount * 366
    m = _YEAR_MONTH_SPAN.search(text)
    if m:
        if m.group(3):
            span = abs(int(m.group(3)) - int(m.group(2))) + 1
            return max(1, span) * 31
        return 31
    if _YEAR_SPAN.search(text):
        return 366
    if "今年" in text or "去年" in text or "全年" in text:
        return 366
    return None


# 异常检测要「够密」才检得出来（2σ 需要足够多点）；≤ 45 天按日、其余按周：
# 整年 = 52 个周点，既有足够密度又不至于把 366 个日点糊成一条噪声线。
_ANOMALY_DAY_MAX_DAYS = 45


def _default_anomaly_granularity(time_range: str | None) -> str:
    """异常类的默认粒度（确定性）：短跨度按日，其余按周。"""
    days = _range_days(time_range)
    if days is not None and days <= _ANOMALY_DAY_MAX_DAYS:
        return "day"
    return "week"


# 横截面异常的维度词 + 「各/每个/哪个/分/按」这类**复数/逐个**信号。
# 「2024年哪几天销售额异常」不含这些信号 → 走时间序列；
# 「2024年各品类销售额有没有异常」命中 → 横截面（用户要的是"哪个品类不正常"）。
_ANOMALY_CROSS_DIM_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("品类", ("品类", "类目")),
    ("区域", ("区域", "地区")),
    ("门店", ("门店", "店铺")),
    ("品牌", ("品牌",)),
    ("客户", ("客户", "客群")),
)
_ANOMALY_CROSS_SIGNALS = ("各", "每个", "哪个", "哪些", "分", "按")


def _anomaly_cross_dimension(user_query: str) -> str | None:
    """异常类问法里**本轮点名**的横截面维度（各品类/哪个区域…）；没有则 None。"""
    text = user_query or ""
    for label, words in _ANOMALY_CROSS_DIM_WORDS:
        for word in words:
            if any(f"{sig}{word}" in text for sig in _ANOMALY_CROSS_SIGNALS):
                return label
    return None


def _apply_deterministic_intent(
    card: RequirementCard, kind: str, user_query: str = "",
    top_period: dict | None = None,
) -> None:
    """把确定性判定的分析角度写进卡片（与 requirement_service._apply_intent 同语义）。

    对比/排名类问法（「华东和华南比一下」「哪个品类卖得最好」）常常不说时间范围和
    指标——这是口语常态。默认「今年 + 销售额」直接作答，比拦下来问两个问题更好；
    卡片上会显示这个默认值，用户想改再改。

    异常类另外做**形态分流**（2026-11-03）：`detect_anomaly` 是对单列做 2σ 检测，
    只有单条时间序列才有意义。所以
    - 本轮点名了横截面维度（各品类/哪个区域…）→ 横截面：只按该维度分组，时间做过滤；
    - 否则 → 时间序列：只保留时间维度（丢掉 scope 造成的常量维度），粒度按跨度确定性给
      日/周（见 `_default_anomaly_granularity`）。
    """
    dims = list(card.dimensions or [])
    methods = list(card.analysis_methods or [])
    query = user_query or ""

    def _add(items: list[str], value: str) -> list[str]:
        return items if value in items else items + [value]

    if kind in ("compare_region", "compare_category", "trend", "anomaly"):
        if not card.time_range:
            card.time_range = "今年"
        if not card.target_metrics:
            card.target_metrics = ["销售额"]

    if kind == "trend":
        card.analysis_methods = _add(methods, "trend_analysis")
        # 粒度不猜：趋势必须有时间粒度，但「按心情」这种认不出的取值不能被默认成月。
        # 留空并在 parse_requirement 里补一条粒度澄清，让用户选。
        card.dimensions = _add(dims, "时间")
        card.detail_level = None
    elif kind == "compare_region":
        card.analysis_methods = _add(methods, "group_compare")
        card.dimensions = _add(dims, "区域")
    elif kind == "compare_category":
        card.analysis_methods = _add(methods, "group_compare")
        card.dimensions = _add(dims, "品类")
    elif kind == "detail":
        card.detail_level = "detail"
        card.analysis_methods = []
        card.granularity = None
    elif kind == "top_period":
        # 2026-11-04（plan top-period-detail-intent）：「2024年里销售额最高的那个月的明细」
        # 是一次**选期**，不是「哪种视角」。用户的说法优先于模型：模型这一轮把
        # analysis_intent 判成 missing 并问「你想看哪种视角」，等于把说清楚的需求又丢了。
        spec = top_period or _top_period_from_query(query) or {
            "direction": "max", "unit": "month",
        }
        from app.models.requirement import RequirementTopPeriod

        unit = str(spec.get("unit") or "month")
        direction = str(spec.get("direction") or "max")
        card.top_period = RequirementTopPeriod(direction=direction, unit=unit)  # type: ignore[arg-type]
        # 选期本身就是「按这个单位聚合一次」，所以粒度必须落下来（否则 SQL 会按年分组）。
        card.granularity = unit  # type: ignore[assignment]
        # 选期 = 一次取极值，没有任何既有方法与之对应；继承上一轮的 anomaly_detection
        # 只会让 Execution 又去做全区间异常检测。
        card.analysis_methods = []
        card.dimensions = ["时间"]
        # 「那个月的**数据详细图**」= 明细；只说「最高的月份」则是该期汇总。
        card.detail_level = "detail" if _DETAIL_PATTERNS.search(query) else None
        if not card.time_range:
            # 和 trend/compare/anomaly 一致：用户没说时间就按「今年」理解（并写在卡上，
            # 用户随时能改）。选期查询本身也支持不带时间范围（覆盖全部数据）。
            card.time_range = "今年"
        if not card.target_metrics:
            card.target_metrics = ["销售额"]
    elif kind == "anomaly":
        cross_dim = _anomaly_cross_dimension(query)
        # 用户原话里的粒度永远是第一位（「哪几天」= day、「各月」= month）。
        explicit_granularity = _granularity_from_query(query)
        card.analysis_methods = _add(methods, "anomaly_detection")
        if cross_dim:
            # 横截面：用户问的是「哪个品类/区域不正常」，时间只做过滤。
            card.dimensions = [cross_dim]
            card.granularity = explicit_granularity or None
        else:
            # 时间序列：只要时间维度；scope 造成的常量维度在这里丢掉（否则
            # 每周多一列恒为「华东」的值，图表与 2σ 检测都会失真）。
            time_terms = set(knowledge.time_dimension_terms())
            time_dims = [d for d in dims if _is_time_dim(d, time_terms)]
            card.dimensions = _add(time_dims, "时间")
            # 方法集合同步收窄：group_compare 在单条时间序列上只会产出跨组摘要噪声。
            card.analysis_methods = ["anomaly_detection"]
            # 粒度优先级：用户原话 > 异常类确定性默认 > 模型自己的猜测。
            # 模型的猜测**不许**压过默认值——实测「2024年销售额有没有异常波动」模型
            # 给了 month（12 个点，2σ 检不出东西），默认值（整年→week）才是能用的。
            card.granularity = explicit_granularity or _default_anomaly_granularity(
                card.time_range
            )
    elif kind == "overview":
        card.analysis_methods = [m for m in methods if m == "group_compare"]
        card.granularity = None


def _is_time_dim(dim: str, time_terms: set[str]) -> bool:
    """维度是否是时间维度（中文标签或物理列名都认）。"""
    raw = str(dim or "").strip()
    if not raw:
        return False
    if raw in time_terms:
        return True
    label = knowledge.label_for_dimension(raw)
    return bool(label and label in time_terms)


_YEAR_RE = re.compile(r"(?:19|20)\d{2}")

# 明细诉求（2026-11-03）：「给我这两个月的明细数据」此前没有任何字段承载，
# SQL 仍 GROUP BY month 只出 2 行。这里做确定性识别，卡片带 detail_level。
#
# 2026-11-04（plan top-period-detail-intent）：用户原话「…那个月数据**详细图**」
# 说得很清楚，但裸词表里没有「详细」，于是 detail_level 停在 aggregate、SQL 去聚合。
# 只加「详细 + 数据/图」这类组合词——裸「详细」是叙述诉求（「详细讲一下」），不是明细。
_DETAIL_PATTERNS = re.compile(
    r"明细|逐笔|逐条|原始数据|流水|每一笔|每笔"
    r"|详细图|明细图|详细数据|明细数据|数据详细|每一条|全部字段|所有字段|清单"
)
_AGGREGATE_PATTERNS = re.compile(r"汇总|合计|总计|趋势|同比|环比|占比|排名")


def _normalize_detail_level_value(raw: object) -> str | None:
    """模型自己给的 detail_level（2026-11-04：它也想表达「这是明细」时别丢）。"""
    text = str(raw or "").strip().lower()
    return text if text in ("detail", "aggregate") else None


def _normalize_detail_level(user_query: str) -> str | None:
    """从用户原话识别「要明细还是要汇总」；都没说则返回 None（沿用上一轮）。"""
    text = str(user_query or "")
    if _DETAIL_PATTERNS.search(text):
        return "detail"
    if _AGGREGATE_PATTERNS.search(text):
        return "aggregate"
    return None


# 「最高/最低的那一期」（2026-11-04，plan 2026-11-04-top-period-detail-intent）。
# 用户说「2024 年里销售额最多的那个月的明细」——这是一次**选期**，不是「哪种视角」。
#
# 2026-11-04 返工（用户：「我的问法挺正常的，为什么就是不对」）：原来只认
# 「最多/最高/最大/最低/最少/最差 + 紧跟着的单位」，于是
# 「把**最差**的那个月的明细给我看看」这种再正常不过的话反而不认——
# 单位前面隔了「的那个」，或者极值词写在单位后面（「哪个月最差」）。
# 现在两边都扫：极值词在前（窗口内找单位）+ 单位在前（窗口内找极值词），
# 并补上口语词（卖得最好/最不好、旺季/淡季、峰值、最惨、最旺…）。
_TOP_PERIOD_UNIT_RE = r"(月份|月|星期|礼拜|周|日|天|季度|季)"
_TOP_PERIOD_UNITS: dict[str, str] = {
    "月份": "month", "月": "month",
    "周": "week", "星期": "week", "礼拜": "week",
    "日": "day", "天": "day",
    "季度": "quarter", "季": "quarter",
}
# 说明：刻意**不认「年」**——「2024年销售额最高的数值是多少」里的「年」是年份不是选期，
# 「最高的那一年」这种诉求在单年数据集里也不成立；把「年」当单位会误判成选期。
_TOP_PERIOD_MAX_WORDS = (
    "最多", "最高", "最大", "峰值", "最旺", "旺季", "最好", "最火", "最热", "卖得最好",
)
_TOP_PERIOD_MIN_WORDS = (
    "最少", "最低", "最小", "最差", "最惨", "淡季", "最冷", "最不好", "卖得最差",
)
# 「最高和最低」这种**两个方向一起要**的说法（2026-11-05：用户「销售额最多和最小」
# 此前整句落空——只认一个方向，且要求单位紧跟极值词，于是意图被丢掉，退化成
# 「2024年华东销售额按周 + 逐条明细 + 异常检测」这种四不像）
_TOP_PERIOD_BOTH_WORDS = ("和", "与", "还有", "以及", "分别", "各自", "同时")
# 出现这些词 → 是「按维度比大小」，不是「取时间上的极值期」
_TOP_PERIOD_DIMENSION_WORDS = (
    "品类", "产品", "品牌", "类别", "区域", "地区", "省份", "城市", "门店", "客户", "渠道",
)
# 极值词之后最多隔多少个非标点字符再找单位（「最多的那个月」= 4 个字符）
_TOP_PERIOD_WINDOW = 8


def _top_period_from_query(user_query: str) -> dict | None:
    """从原话识别「按哪一期取最高/最低」：{'direction': max|min|both, 'unit': month|...}。

    三种语序都认（用户怎么说话就怎么认）：
    - 「销售额**最高的**那个**月**」/「把**最差**的那个**月**的明细给我看看」；
    - 「哪**个月**最差」/「哪**天**卖得最好」；
    - 「销售额**最多和最小**的具体情况」→ `both`（两个方向一起要）。

    不认「最高的销售额」（只有极值、没提期——那是要一个数，不是选期）；
    也不认「哪个**品类**卖得最多」（那是按维度比大小，不是选时间期）。
    """
    text = str(user_query or "")
    if not text:
        return None
    # 维度词在场 → 这是「按维度比大小」，交给 compare 类意图
    if any(word in text for word in _TOP_PERIOD_DIMENSION_WORDS):
        return None

    has_max = any(word in text for word in _TOP_PERIOD_MAX_WORDS)
    has_min = any(word in text for word in _TOP_PERIOD_MIN_WORDS)
    both = has_max and has_min and any(word in text for word in _TOP_PERIOD_BOTH_WORDS)
    if both:
        # 两个方向一起要：单位没说就按月（销售额的极值期，口语默认就是月）
        unit = None
        for candidate in _TOP_PERIOD_UNITS:
            if candidate in text:
                unit = _TOP_PERIOD_UNITS[candidate]
                break
        return {"direction": "both", "unit": unit or "month"}

    candidates: list[tuple[int, str, str]] = []   # (位置, 方向, 单位)

    def _push(position: int, word: str, direction: str) -> None:
        # 极值词之后：窗口内找单位
        tail = text[position + len(word): position + len(word) + _TOP_PERIOD_WINDOW]
        tail = re.split(r"[，。；！？,.;!?]", tail)[0]
        unit_match = re.search(_TOP_PERIOD_UNIT_RE, tail)
        if unit_match:
            unit = _TOP_PERIOD_UNITS.get(unit_match.group(1))
            if unit:
                candidates.append((position, direction, unit))
                return
        # 单位在极值词之前：窗口内找单位
        head = text[max(0, position - _TOP_PERIOD_WINDOW): position]
        head = re.split(r"[，。；！？,.;!?]", head)[-1]
        unit_matches = list(re.finditer(_TOP_PERIOD_UNIT_RE, head))
        # 「2024年」这种数字+单位是年份，不是选期，跳过
        unit_matches = [
            m for m in unit_matches
            if not (m.start() > 0 and head[m.start() - 1].isdigit())
        ]
        if unit_matches:
            unit = _TOP_PERIOD_UNITS.get(unit_matches[-1].group(1))
            if unit:
                candidates.append((unit_matches[-1].start(), direction, unit))

    for word in _TOP_PERIOD_MAX_WORDS:
        for match in re.finditer(re.escape(word), text):
            _push(match.start(), word, "max")
    for word in _TOP_PERIOD_MIN_WORDS:
        for match in re.finditer(re.escape(word), text):
            _push(match.start(), word, "min")

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _, direction, unit = candidates[0]
    return {"direction": direction, "unit": unit}

    return None

_CN_MONTHS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}


# 用户明确表达「分析角度」的短语。没命中这些时，服务端不信任模型给的
# analysis_methods（2026-11-03 实测：「2024年的销售情况」被悄悄判成 trend_analysis，
# 结果按年分组只有 1 行，报告写「数据量不足」）。
#
# 注意要覆盖「各区域 / 各品类 / 哪个 / 按月」这类**隐含**的角度表达——
# 只写「对比/趋势」会把「2024年各区域销售额」误判成没表达角度，反而多问一轮。
_INTENT_PHRASES = (
    # 趋势
    "趋势", "走势", "变化", "增长", "下滑", "上涨", "下降",
    "按月", "每月", "按周", "每周", "按季度", "每季度", "按天", "每日",
    # 对比 / 分布 / 排名
    "对比", "比较", "比一下", "比比", "相比", "排名", "排行", "占比", "分布",
    "哪个", "哪些", "最高", "最低",
    "最多", "最少", "前几", "top",
    # 维度化表达（隐含 compare）
    "各区域", "各品类", "各产品", "各门店", "各省", "各地区", "各月", "各年",
    "每个区域", "每个品类", "每个门店", "分区域", "分品类", "分门店",
    # 明细
    "明细", "逐条", "逐笔", "列出来", "每一笔",
    # 异常
    "异常", "波动", "异动",
    # 总览
    "汇总", "总览", "概况", "整体情况",
)

# 从字段名推断「能做什么分析」的通用词根（与具体表名无关，换 schema 仍成立）。
_TIME_COL_HINTS = ("date", "day", "month", "year", "quarter", "week", "日期", "时间")
_REGION_COL_HINTS = ("region", "province", "city", "area", "区域", "城市", "省份")
_CATEGORY_COL_HINTS = ("category", "brand", "type", "品类", "品牌", "类型")


def _user_expressed_intent(user_query: str) -> bool:
    text = (user_query or "").lower()
    return any(p in text for p in _INTENT_PHRASES)


def _deterministic_intent(user_query: str) -> str | None:
    """从原话直接判定分析角度（确定性内核）。

    2026-11-03 实测：「2024年各区域销售额」这种话，模型仍会追问「你想看哪种视角」——
    但用户已经说了「各区域」，这就是区域对比。确定性优先：能判就直接判，不让模型再问。
    判定不出来才交给模型澄清。
    """
    text = (user_query or "").lower()

    # 先看「点了名的维度 + 比较语义」：口语常说「华东和华南比一下」，既没有
    # 「各区域」也没有「对比」两个字，但语义就是区域对比（2026-11-03 实测被漏判）。
    _region_words = ("华东", "华南", "华北", "华中", "西南", "西北", "区域", "门店")
    _compare_words = ("比", "对比", "比较", "vs", "哪个", "最好", "最高", "最多", "排名")
    if any(w in text for w in _region_words) and any(w in text for w in _compare_words):
        return "compare_region"
    _category_words = ("品类", "产品", "品牌", "类别")
    if any(w in text for w in _category_words) and any(w in text for w in _compare_words):
        return "compare_category"

    if any(p in text for p in ("明细", "逐条", "逐笔", "列出来", "每一笔")):
        return "detail"
    if any(p in text for p in ("异常", "波动", "异动")):
        return "anomaly"
    if any(p in text for p in ("各品类", "各产品", "分品类", "每个品类", "品类对比")):
        return "compare_category"
    if any(p in text for p in (
        "各区域", "各地区", "各省", "分区域", "每个区域", "区域对比", "各门店", "每个门店",
        "哪个区域", "哪家门店",
    )):
        return "compare_region"
    if any(p in text for p in (
        "趋势", "走势", "变化", "增长", "下滑", "上涨", "下降",
        "按月", "每月", "按周", "每周", "按季度", "每季度", "按天", "每日", "各月",
    )):
        return "trend"
    if any(p in text for p in ("对比", "比较", "比一下", "比比", "相比", "排名", "排行", "占比", "分布", "最高", "最低", "最多", "最少")):
        # 只说「对比/排名」但没说是哪个维度 → 无法确定比什么，交给澄清
        return None
    if any(p in text for p in ("汇总", "总览", "概况", "整体情况")):
        return "overview"
    return None


def _display_metric(term: str) -> str:
    """指标名规范化：物理列 → 业务中文名（order_amount → 销售额）。

    2026-11-03 用户实测：卡片上直接显示 `payment_amount`、`region`，没人看得懂。
    展示层必须是中文；物理列绑定留给 SQL 阶段（注册表负责翻译回去）。
    """
    text = str(term or "").strip()
    if not text:
        return text
    label = knowledge.label_for_metric_column(text)
    if label:
        return label
    if "." in text:  # 兜底：至少去掉表名前缀
        return text.split(".", 1)[1]
    return text


def _display_dimensions(dims: list[str]) -> list[str]:
    """维度标签规范化为中文并去重（region → 区域；region+区域 → 区域）。"""
    out: list[str] = []
    for dim in dims:
        raw = str(dim or "").strip()
        if not raw:
            continue
        label = knowledge.label_for_dimension(raw) or raw
        if label not in out:
            out.append(label)
    return out


def _extract_scope_from_query(user_query: str) -> list[str]:
    """从「只看华东」「仅限华南」这类说法里确定性抽范围（2026-11-03 实测补充轮丢约束）。"""
    text = user_query or ""
    if not any(k in text for k in ("只看", "仅看", "仅限", "限定", "只要", "只关注", "缩小到")):
        return []
    from app.agent.requirement_options import scope_options

    hits = [opt.value for opt in scope_options() if opt.value != "ALL" and opt.value in text]
    return hits[:1]


def _guard_analysis_methods(user_query: str, methods: list[str]) -> list[str]:
    """用户没表达分析角度 → 一律清空（不让模型替他决定看成什么）。"""
    if not methods:
        return []
    if _user_expressed_intent(user_query):
        return methods
    logger.warning(
        "analysis_methods dropped (user never expressed intent): %s | query=%r",
        methods, user_query,
    )
    return []


def _first_column_matching(schema: SchemaContext | None, hints: tuple[str, ...]) -> str:
    for table in (schema.tables if schema else []) or []:
        tname = str(getattr(table, "name", "") or "")
        for col in getattr(table, "columns", None) or []:
            cname = str(getattr(col, "name", "") or "")
            low = cname.lower()
            if any(h in low for h in hints):
                return f"{tname}.{cname}" if tname else cname
    return ""


def _default_intent_clarification(
    schema: SchemaContext | None,
) -> RequirementMissingField | None:
    """分析角度不明时，按**可用字段**生成候选（不是写死选项表）。

    只有字段真实存在才给对应选项：有日期列才能「按月趋势」，有区域/品类列才能「对比」。
    """
    from app.models.requirement import RequirementOption

    options = [RequirementOption(label="全年总览", value="intent:overview")]
    time_col = _first_column_matching(schema, _TIME_COL_HINTS)
    if time_col:
        options.append(RequirementOption(label="按月趋势", value="intent:trend"))
    region_col = _first_column_matching(schema, _REGION_COL_HINTS)
    if region_col:
        options.append(RequirementOption(label="区域对比", value="intent:compare_region"))
    category_col = _first_column_matching(schema, _CATEGORY_COL_HINTS)
    if category_col:
        options.append(RequirementOption(label="品类对比", value="intent:compare_category"))
    options.append(RequirementOption(label="逐条明细", value="intent:detail"))
    if len(options) < 2:
        return None
    return RequirementMissingField(
        key="analysis_intent",
        label="你想看哪一种视角？",
        kind="single",
        options=options[:4],
    )
_YEAR_MONTH = re.compile(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月")
_RELATIVE_SPAN = re.compile(r"这两个月|那两个月|最近两个月|这俩月|这两个月份")


_BARE_MONTH_ARABIC = re.compile(r"(?<!\d)(\d{1,2})\s*月")
_BARE_MONTH_CN = re.compile(r"([一二三四五六七八九十]{1,2})\s*月")


def _resolve_bare_month(
    card: RequirementCard, prior: RequirementCard, user_query: str,
) -> None:
    """用户原话只给了月份（「七月份」「7月」）→ 必须用上一轮的年份，覆盖模型输出。

    2026-11-03 实测：需求 prompt 注入 today（2026-11-03）之后，模型把「详细讲一下七月份」
    直接写成 `2026-07`——它自己补了一个年份，而 `_inherit_year` 只在「没有年份」时才补，
    于是年份错了。用户说的是「那七月份」，指的是上一轮讨论的年份。
    """
    text = user_query or ""
    if _YEAR_RE.search(text):
        return  # 用户明确说了年份，尊重
    month: int | None = None
    m = _BARE_MONTH_ARABIC.search(text)
    if m and 1 <= int(m.group(1)) <= 12:
        month = int(m.group(1))
    else:
        m_cn = _BARE_MONTH_CN.search(text)
        if m_cn and m_cn.group(1) in _CN_MONTHS:
            month = _CN_MONTHS[m_cn.group(1)]
    if month is None:
        return
    prior_year = _YEAR_RE.search(str(prior.time_range or ""))
    if not prior_year:
        return
    card.time_range = f"{prior_year.group(0)}年{month}月"


def _resolve_relative_month_span(
    card: RequirementCard, prior: RequirementCard, user_query: str = "",
) -> None:
    """「这两个月」→ 用上一轮讨论的月份往前推一个月（确定性）。

    2026-11-03 实测：先问 7 月、再问 8 月，然后说「给我这两个月的明细数据」——
    模型有时按「最近两个月」算成 9/10 月，有时干脆不产出时间范围（沿用 8 月）。
    所以判定必须看**用户原话**，不能看模型输出的 time_range。
    """
    text = f"{user_query} {card.time_range or ''}"
    if not _RELATIVE_SPAN.search(text):
        return
    match = _YEAR_MONTH.search(str(prior.time_range or ""))
    if not match:
        return
    year, month = int(match.group(1)), int(match.group(2))
    if month <= 1:
        return
    card.time_range = f"{year}年{month - 1}月-{month}月"


def _inherit_year(card: RequirementCard, prior: RequirementCard) -> None:
    """补充轮只说了月份（「七月份」）时，把上一轮的时间范围里的**年份**带过来。

    2026-11-02 实测缺陷：首轮「2024年华东销售趋势」，追问「详细讲一下七月份」→
    新卡 time_range='七月份'（丢了年份）→ SQL 生成器按 today(2026) 解析成
    `order_date >= '2026-07-01'`，静默返回**另一年**的数字。这类"数字看着正常
    但年份错了"的错最危险，必须在需求层确定性补齐。
    """
    current = card.time_range
    if not current or _YEAR_RE.search(str(current)):
        return
    prior_text = str(prior.time_range or "")
    match = _YEAR_RE.search(prior_text)
    if not match:
        return
    year = match.group(0)
    text = str(current).strip()
    # 「七月份」「七月」→ 7月，让下游更容易解析
    cn = re.match(r"^([一二三四五六七八九十]{1,2})月", text)
    if cn and cn.group(1) in _CN_MONTHS:
        card.time_range = f"{year}年{_CN_MONTHS[cn.group(1)]}月"
    else:
        card.time_range = f"{year}年{text}"


def _merge_prior_card(
    card: RequirementCard, prior: RequirementCard, user_query: str = "",
) -> None:
    """supplement 继承：presence（新值非空）覆盖 / absence（空）继承 prior。

    P15 e2e T2 语义：第 1 轮已确认约束默认沿用，只有本轮明确改写才覆盖。
    comparison 不在卡体显式字段，走 dimensions kind 标签并集（同 kind 覆盖）；
    granularity 自 2026-10-28 起是卡体字段（granularity），直接按 presence 继承。
    """
    if not card.target_metrics and prior.target_metrics:
        card.target_metrics = list(prior.target_metrics)
    if card.time_range is None and prior.time_range:
        card.time_range = prior.time_range
    if user_query or card.time_range:
        # 只给了月份 → 用上一轮年份（优先于模型自己补的年份）
        _resolve_bare_month(card, prior, user_query)
        # 指代（「这两个月」）必须先于年份继承判断：它可能直接改写时间范围。
        _resolve_relative_month_span(card, prior, user_query)
    if card.time_range and not _RELATIVE_SPAN.search(str(card.time_range)):
        # 只说了月份 → 继承上一轮的年份（否则按 today 解析成另一年）
        _inherit_year(card, prior)
    if card.granularity is None and prior.granularity:
        card.granularity = prior.granularity
    if card.detail_level is None:
        card.detail_level = prior.detail_level
    if not card.scope and prior.scope:
        card.scope = list(prior.scope)
    if not card.analysis_methods and prior.analysis_methods:
        card.analysis_methods = list(prior.analysis_methods)
    # dimensions：base 维度并集去重（新 + prior 缺失的 base 都保留）；
    # granularity/comparison hint 同 kind 被本轮表达时覆盖（不追加旧值）。
    new_dims = list(card.dimensions)
    prior_dims = list(prior.dimensions)
    kind_new = {_dim_kind(d) for d in new_dims}
    for d in prior_dims:
        k = _dim_kind(d)
        if d in new_dims:
            continue
        if k == "base" or k not in kind_new:
            new_dims.append(d)
    card.dimensions = new_dims
    # missing_fields 重算：被继承/派生满足的 key 从 missing 移除
    satisfied: set[str] = set()
    if card.time_range:
        satisfied.add("time_range")
    if card.scope:
        satisfied.add("scope")
    if card.target_metrics:
        satisfied.add("metric")
    if any(_dim_kind(d) == "granularity" for d in card.dimensions):
        satisfied.add("granularity")
    if any(_dim_kind(d) == "comparison" for d in card.dimensions):
        satisfied.add("comparison")
    if card.granularity:
        satisfied.add("granularity")
    card.missing_fields = [mf for mf in card.missing_fields if mf.key not in satisfied]


def _call_llm_for_parse(
    user_query: str,
    schema: SchemaContext | None,
    conversation_context: str | None = None,
    dictionary_context: str | None = None,
    assembled_context: str | None = None,  # P4c: 优先于 conversation_context，含 selective recall
    prior_block: str = "",  # P15 e2e T2: 上一轮已确认约束块（空则行为不变）
) -> dict:
    """Call the LLM and parse the JSON response. Returns {} on parse failure."""
    dictionary_block = ""
    if dictionary_context:
        bounded = dictionary_context[:MAX_DICTIONARY_CHARS]
        dictionary_block = f"【数据字典参考】\n{bounded}"
    prompt = build_requirement_parse_prompt(
        user_query=user_query,
        schema_text=_schema_text(schema),
        dictionary_block=dictionary_block,
        prior_block=prior_block,
        # 缺了今天几号，「今年/上月」这类相对时间会被模型按示例里的 2024 猜
        # （2026-11-03 实测：问「今年」返回 2024 年）。
        today=datetime.now().strftime("%Y-%m-%d"),
    )
    # 分层对话上下文前置：让需求解析感知先前轮次（如「再按产品细分」隐含的范围）。
    # P4c 优先用 assembled_context（含 recall + conversation 全景），否则 fallback 到 conversation_context。
    injected = assembled_context or conversation_context
    if injected:
        prompt = f"{format_context_block(injected)}\n\n{prompt}"
    try:
        raw = call_llm(prompt, max_tokens=budgets.max_tokens("requirement_parse"))
    except Exception as exc:
        logger.warning("parse_requirement LLM failed: %s query=%r", exc, user_query)
        return {}
    parsed = safe_json_parse(raw) if isinstance(raw, str) else raw
    logger.warning("parse_requirement parsed: %s", parsed)
    return parsed if isinstance(parsed, dict) else {}


# --- Public entry point ---------------------------------------------------


def parse_requirement(
    *,
    user_query: str,
    schema_context: SchemaContext | None,
    prior_card: RequirementCard | None = None,
    conversation_context: str | None = None,
    dictionary_context: str | None = None,
    assembled_context: str | None = None,  # P4c: 由 Requirement Agent 传（ContextBundle.assembled_context）
) -> RequirementCard:
    """Parse a user query into a RequirementCard.

    Strategy:
    1. Call LLM to get a structured analysis (summary, metrics, missing
       fields, assumptions, confidence).
    2. For each missing field, build a server-side controlled
       `RequirementMissingField` with the canonical options.
    3. For each assumption, build a `RequirementAssumption` with
       `accepted=None`.
    4. If `prior_card` is supplied, merge the new analysis on top of it
       (for `mode=supplement`). For now, the new analysis wins.
    5. If there are no missing fields and assumptions are all resolved,
       status='complete'; else 'missing'.
    """
    parsed = _call_llm_for_parse(
        user_query, schema_context,
        conversation_context, dictionary_context,
        assembled_context,
        prior_block=_format_prior_block(prior_card) if prior_card is not None else "",
    )
    if not parsed:
        # LLM gave nothing usable; fall back to a 'missing' card with all
        # dimensions empty so the frontend can render a complete form.
        return _fallback_missing_card(user_query)

    missing_keys: list[RequirementFieldKey] = []
    raw_missing = parsed.get("missing_fields") or []
    for k in raw_missing:
        if k in ("time_range", "scope", "metric", "granularity", "comparison"):
            missing_keys.append(k)  # type: ignore[arg-type]

    missing_fields: list[RequirementMissingField] = [
        opts.build_missing_field(key=k) for k in missing_keys
    ]
    # 2026-11-03：模型现场生成的澄清项（基于 MCP 返回的真实字段）。
    # 选项里带 column 的，必须在该 session 的 schema 里真实存在，否则丢弃——
    # 防止模型编造字段；有效候选不足 2 个说明其实不歧义，整条丢弃。
    clarified = _build_clarifications(parsed.get("clarifications"), schema_context)
    if clarified:
        existing_keys = {mf.key for mf in clarified}
        missing_fields = clarified + [mf for mf in missing_fields if mf.key not in existing_keys]
    # 分析角度仍然没确定（用户没说、模型也没问，且上一轮也没定过）→ 按可用字段补一条追问。
    # 这样「2024年的销售情况」不会再被静默跑成「按年趋势 1 行」；而「改看华南」这类
    # 已确定视角的补充轮不会被反复追问。
    prior_has_intent = prior_card is not None and (
        bool(prior_card.analysis_methods) or bool(prior_card.detail_level)
    )
    if not prior_has_intent and not any(mf.key == "analysis_intent" for mf in missing_fields):
        if not _guard_analysis_methods(user_query, ["probe"]):
            intent_mf = _default_intent_clarification(schema_context)
            if intent_mf is not None:
                missing_fields.insert(0, intent_mf)

    assumptions: list[RequirementAssumption] = []
    for raw in parsed.get("assumptions") or []:
        if not isinstance(raw, dict):
            continue
        alts: list = []
        for opt in raw.get("alternatives") or []:
            if isinstance(opt, dict) and "label" in opt and "value" in opt:
                from app.models.requirement import RequirementOption
                alts.append(RequirementOption(
                    label=str(opt["label"])[:60],
                    value=str(opt["value"])[:60],
                ))
        # Trust the LLM's explicit `accepted` flag (True/False). If the
        # LLM omits it or sends null, leave the assumption unresolved
        # so the frontend prompts the user.
        raw_accepted = raw.get("accepted", None)
        if raw_accepted is True:
            accepted: bool | None = True
        elif raw_accepted is False:
            accepted = False
        else:
            accepted = None
        assumptions.append(RequirementAssumption(
            key=str(raw.get("key", "assumption"))[:64],
            text=str(raw.get("text", ""))[:300],
            accepted=accepted,
            alternatives=alts,
        ))

    target_metrics = [str(m)[:64] for m in parsed.get("target_metrics") or []]
    scope = [str(s)[:64] for s in parsed.get("scope") or []]
    dimensions = [str(d)[:64] for d in parsed.get("dimensions") or []]
    analysis_methods = [str(a)[:64] for a in parsed.get("analysis_methods") or []]
    analysis_methods = _guard_analysis_methods(user_query, analysis_methods)

    confidence_raw = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    summary = str(parsed.get("summary") or user_query)[:300]
    time_range = parsed.get("time_range")
    if time_range is not None:
        time_range = str(time_range)[:64]
    granularity = _normalize_granularity(parsed.get("granularity"))
    # 2026-11-03：用户原话里明说的粒度优先于模型输出——实测「哪几天」模型返回 None，
    # 于是按 year 分组只出 1 行（见 plan 2026-11-03-anomaly-granularity）。
    explicit_granularity = _granularity_from_query(user_query)
    if explicit_granularity:
        granularity = explicit_granularity
    # 明细 / 汇总：**只看用户原话**（2026-11-05 修正）。
    # 之前允许模型自己给 detail_level，结果「具体情况是什么」被模型当成「要看逐条明细」——
    # 用户看到的是「逐条明细」，这属于把模型的猜测当成用户的要求。明细只能由用户说。
    detail_level = _normalize_detail_level(user_query)

    # status: complete requires NO missing fields AND all assumptions
    # explicitly accepted. If the LLM left assumptions unresolved
    # (accepted=None), the frontend must prompt the user to accept/reject
    # them first, so the card stays in 'missing' state from the
    # server's perspective.
    unresolved = [a for a in assumptions if a.accepted is None]
    if missing_fields or unresolved:
        status: RequirementStatus = "missing"
    else:
        status = "complete"

    card = RequirementCard(
        id=f"draft-{int(datetime.now().timestamp() * 1000)}",
        version=(prior_card.version if prior_card else 0) + 1,
        status=status,
        summary=summary,
        target_metrics=target_metrics,
        time_range=time_range,
        granularity=granularity,
        detail_level=detail_level,
        scope=scope,
        dimensions=dimensions,
        analysis_methods=analysis_methods,
        expected_blocks=[],
        missing_fields=missing_fields,
        assumptions=assumptions,
        confidence=confidence,
        confirmed_at=None,
    )
    # P15 e2e T2：supplement 继承（presence 覆盖 / absence 继承 prior），随后重算
    # missing_fields（_merge_prior_card 内按继承结果移除已满足 key）+ status。
    if prior_card is not None:
        _merge_prior_card(card, prior_card, user_query=user_query)

    # 2026-11-03 确定性优先：能从原话直接判定分析角度时，服务端拍板——
    # 丢掉模型多余的「你想看哪种视角」追问，并把 intent 落到卡体字段。
    # 「2024年各区域销售额」这类明确问法不该再多问一轮。
    # 2026-11-04：最高/最低的那一期（+是不是要明细）也走这条确定性通道——它比
    # 「哪种视角」更具体，模型问那句话等于把说清楚的需求丢了。
    top_period = _top_period_from_query(user_query)
    det_intent = "top_period" if top_period else _deterministic_intent(user_query)
    if det_intent is not None:
        _apply_deterministic_intent(
            card, det_intent, user_query=user_query, top_period=top_period,
        )
        card.missing_fields = [
            mf for mf in card.missing_fields if mf.key != "analysis_intent"
        ]
        # 明细模式不需要「指标」和「粒度」：逐条返回原始列，不聚合。
        if det_intent == "detail":
            card.missing_fields = [
                mf for mf in card.missing_fields if mf.key not in ("metric", "granularity")
            ]
        # 趋势必须有粒度，但认不出/没给的时候不猜——补一条粒度澄清让用户选。
        # 「已经有粒度」的三种来源都算：卡体字段 / dimensions 的「粒度:X」标签 /
        # 用户已接受的 granularity 假设（老卡片的承载方式）。
        gran_known = bool(card.granularity) or any(
            _dim_kind(d) == "granularity" for d in card.dimensions
        ) or any(
            a.accepted is True and a.key == "granularity" for a in card.assumptions
        )
        if det_intent == "trend" and not gran_known and not any(
            mf.key == "granularity" for mf in card.missing_fields
        ):
            card.missing_fields.append(opts.build_missing_field(key="granularity"))

    # 卡片展示层规范化：物理列名 → 中文，维度去重（2026-11-03）
    card.target_metrics = [_display_metric(m) for m in card.target_metrics]
    card.dimensions = _display_dimensions(card.dimensions)
    # 补充轮常见说法「只看华东」：模型经常漏抽，这里确定性补上
    explicit_scope = _extract_scope_from_query(user_query)
    if explicit_scope:
        card.scope = explicit_scope

    # LLM 会自相矛盾：既输出了某个维度的值，又把它列进 missing_fields
    # （2026-10-28 实测：「granularity: 月」且 missing_fields 含 granularity）。
    # 卡体已经有答案的维度一律不再追问，否则用户会被要求回答一个已经答过的字段。
    _drop_satisfied_missing_fields(card)

    # 槽位三态 + 口径确定性落位（2026-11-04，plan 2026-11-04-requirement-slot-contract）。
    # 顺序很重要：必须先 `_drop_satisfied_missing_fields` 再补「口径歧义」，
    # 否则澄清项会被「卡体已有 target_metrics」判成已满足而丢掉。
    prior_metric = None
    if prior_card is not None and prior_card.target_metrics:
        prior_metric = str(prior_card.target_metrics[0])
    slot = _apply_slot_resolutions(card, user_query, prior_metric=prior_metric)

    # 最高期用的口径跟卡体最终一致（口径可能刚被槽位落位/默认改写，
    # 所以必须放在槽位处理之后——否则 chip 会显示模型原始词 sales）。
    if card.top_period is not None and card.target_metrics:
        card.top_period.metric = str(card.target_metrics[0])

    # 置信度闸：理解得够确定就直接做，不确定才给选项（2026-11-04）。
    _confidence, _ask = _apply_confidence_gate(
        card, user_query, model_confidence=confidence,
    )

    unresolved = [a for a in card.assumptions if a.accepted is None]
    if card.missing_fields or unresolved:
        card.status = "missing"
    else:
        card.status = "complete"
    return card


def _apply_slot_resolutions(
    card: RequirementCard, user_query: str, *, prior_metric: str | None = None,
):
    """把槽位三态写进卡片：口径确定性落位 + 歧义时补带候选的澄清项。

    口径优先级：**用户原话命中注册表 > 上一轮已确认 > 注册表 default > 模型猜**。
    实测「2024年各区域销售额」模型会给 `实收金额`——注册表里 `销售额` 是
    `default: true`，用户也明确说了「销售额」，不该让模型改。
    supplement 轮用户没提口径时沿用上一轮（`inherited`），**不再重复问**。
    """
    from app.agent.requirement_slots import (
        match_metric_terms,
        resolve_metric_slot,
        slot_order as build_slot_order,
        slot_states as build_slot_states,
    )

    slot = resolve_metric_slot(
        user_query, list(card.target_metrics or []), inherited=prior_metric,
    )

    if slot.state == "filled" and slot.value:
        # 用户/注册表拍板的口径覆盖模型输出（显示层中文名由 _display_metric 之后统一）
        card.target_metrics = [slot.value]
        # 口径已确定 → 连带清掉「指标」澄清项。模型经常既给了口径又把它列进
        # missing_fields（`_drop_satisfied_missing_fields` 在槽位落位前跑过一次，
        # 这里口径被确定性改写后要再清一遍，避免用户被问一个已经答过的字段）。
        card.missing_fields = [mf for mf in card.missing_fields if mf.key != "metric"]
    elif slot.state == "ambiguous":
        # 2026-11-04（用户：「不一定总是要给选项啊，意图清晰的为什么要给选项」）：
        # 用户**这一轮压根没提口径**（「最多月份的那个月的明细」），这不是歧义，是缺省——
        # 按注册表默认口径直接前进，把「按了什么口径」写在卡片上让用户随时改，
        # 而不是拦下来问一道选择题。只有用户确实说了个一词多义的说法（「金额」）才问。
        candidates = _metric_candidate_options(slot)
        named_terms = match_metric_terms(user_query)
        default_metric = _default_registry_metric()
        if not named_terms and default_metric is not None and candidates:
            card.target_metrics = [default_metric.name]
            note = default_metric.note or default_metric.unit or ""
            card.assumptions.append(
                RequirementAssumption(
                    key=f"metric_default:{default_metric.name}",
                    text=(
                        f"默认按「{default_metric.name}」口径"
                        + (f"（{note}）" if note else "")
                        + "；要换口径直接说，例如「净销售额」"
                    ),
                    accepted=True,
                    alternatives=[o for o in candidates if o.label != default_metric.name][:2],
                )
            )
            card.missing_fields = [mf for mf in card.missing_fields if mf.key != "metric"]
        elif candidates:
            question = "用哪个口径？" if len(candidates) > 1 else "指标口径"
            card.missing_fields = [
                mf for mf in card.missing_fields if mf.key != "metric"
            ]
            card.missing_fields.insert(
                0,
                RequirementMissingField(
                    key="metric",
                    label=question,
                    kind="single",
                    options=candidates,
                    state="ambiguous",
                ),
            )

    states = build_slot_states(card, metric_slot=slot)
    card.slot_states = {k: str(v) for k, v in states.items()}
    card.slot_order = build_slot_order(states)
    return slot


def _metric_candidate_options(slot) -> list:
    """口径候选 → 澄清选项（label 干净、value 稳定为 `metric:<name>`、差异说明进 hint）。"""
    options: list = []
    for candidate in slot.candidates[:3]:
        hint = candidate.hint or candidate.unit or None
        options.append(
            RequirementOption(
                label=candidate.name,
                value=f"metric:{candidate.name}",
                hint=hint or None,
            )
        )
    return options


def _default_registry_metric():
    """注册表里标了 `default: true` 的指标（缺省口径）。"""
    return next((m for m in knowledge.all_metrics() if m.default), None)


# --- 澄清的置信度闸（2026-11-04，用户：「应该是一个置信度，过了之后就是有选择框
# 否则就是直接按照用户说的才对」）------------------------------------------------
#
# 设计：**要不要问你**不该由「某个槽位是否为空」决定，而应由「我对这句话的理解有多
# 确定」决定。
#   - 理解确定（置信度 ≥ 阈值）→ 直接按你说的做，把假设写在卡上（随时可改）；
#   - 理解不确定（置信度 < 阈值）→ 才给选项。
# 阈值可用 `REQUIREMENT_CLARIFY_CONFIDENCE` 调（默认 0.75）。
_CLARIFY_CONFIDENCE_ENV = "REQUIREMENT_CLARIFY_CONFIDENCE"
_DEFAULT_CLARIFY_CONFIDENCE = 0.75


def _clarify_threshold() -> float:
    import os

    raw = os.getenv(_CLARIFY_CONFIDENCE_ENV, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_CLARIFY_CONFIDENCE
    return max(0.0, min(1.0, value))


# 「问一个数」的说法：这类话虽然没说角度，但意图是明确的（要一个量），不该追问视角。
_QUANTITY_ASK_RE = re.compile(r"是多少|多少钱|多少|合计|总共|一共|总量|总计")


def _decision_confidence(
    card: RequirementCard, user_query: str, model_confidence: float,
) -> float:
    """理解置信度 = 模型自评 + **原话里的确定性证据**，没有证据则封顶。

    为什么不能只用模型自评：实测「看一下今年的销售情况」这类模糊问法它也给 0.95，
    同一句话不同采样在 0.7~0.95 之间跳。而「用户到底说没说」是可以确定性判定的。

    证据（都来自用户原话）：
    - 明确的分析角度（趋势/对比/排名/各区域/异常…）+0.15；
    - 明细/汇总 +0.10；「最高/最低的那一期」+0.15；明说的粒度 +0.05；
    - 问一个数（多少/合计/总共）+0.10；点名口径 +0.05；给了年份 +0.05。

    **没有角度类证据**（既没说趋势/对比，也没说明细/选期/粒度/数量）时，
    置信度封顶 0.6（低于默认阈值 0.75）——这种话本来就该给选项。
    """
    score = float(model_confidence or 0.0)
    text = str(user_query or "")

    from app.agent.requirement_slots import match_metric_terms

    angle_evidence = False
    if _deterministic_intent(text):
        score += 0.15
        angle_evidence = True
    if _normalize_detail_level(text) == "detail":
        score += 0.10
        angle_evidence = True
    if _top_period_from_query(text):
        score += 0.15
        angle_evidence = True
    if _granularity_from_query(text):
        score += 0.05
        angle_evidence = True
    if _QUANTITY_ASK_RE.search(text):
        score += 0.10
        angle_evidence = True
    if match_metric_terms(text):
        score += 0.05
    if _YEAR_RE.search(text):
        score += 0.05

    if not angle_evidence:
        score = min(score, 0.6)
    return max(0.0, min(1.0, round(score, 2)))


def _apply_confidence_gate(
    card: RequirementCard, user_query: str, *, model_confidence: float,
) -> tuple[float, bool]:
    """按置信度决定「直接做」还是「给选项」。返回 (置信度, 是否还要问)。

    - 置信度 ≥ 阈值：把仅剩的「分析角度」追问撤掉（用户已经说清楚了），
      记一条已接受假设说明「按你说的做」，后续可随时改；
    - 置信度 < 阈值：保留追问（这次是真的不确定，需要选项）。
    """
    confidence = _decision_confidence(card, user_query, model_confidence)
    card.confidence = confidence
    threshold = _clarify_threshold()
    ask = confidence < threshold
    logger.info(
        "clarify gate: confidence=%.2f threshold=%.2f ask=%s query=%s",
        confidence, threshold, ask, user_query[:40],
    )
    if ask:
        return confidence, True

    dropped = [
        mf for mf in card.missing_fields
        if mf.key == "analysis_intent"
        or mf.key.startswith("intent") or mf.key.startswith("analysis")
    ]
    if dropped:
        card.missing_fields = [mf for mf in card.missing_fields if mf not in dropped]
        card.assumptions.append(
            RequirementAssumption(
                key="intent_default:按原话执行",
                text="按你原话里的分析方式执行（不再追问视角）；要换角度直接说",
                accepted=True,
            )
        )
    return confidence, False


def _drop_satisfied_missing_fields(card: RequirementCard) -> None:
    """移除卡体已能回答的 missing_fields（time_range / scope / metric / granularity）。"""
    satisfied: set[str] = set()
    if card.time_range:
        satisfied.add("time_range")
    if card.scope:
        satisfied.add("scope")
    if card.target_metrics:
        satisfied.add("metric")
    if card.granularity:
        satisfied.add("granularity")
    if satisfied:
        card.missing_fields = [
            mf for mf in card.missing_fields if mf.key not in satisfied
        ]


def _fallback_missing_card(user_query: str) -> RequirementCard:
    """When the LLM is unavailable or its output cannot be parsed, return a
    card that asks the user to fill in EVERY dimension. This is a safer
    default than guessing.
    """
    all_keys: list[RequirementFieldKey] = [
        "time_range", "scope", "metric", "granularity", "comparison",
    ]
    return RequirementCard(
        id=f"draft-{int(datetime.now().timestamp() * 1000)}",
        version=1,
        status="missing",
        summary=user_query[:300],
        missing_fields=[opts.build_missing_field(key=k) for k in all_keys],
        assumptions=[],
        confidence=0.0,
        confirmed_at=None,
    )
