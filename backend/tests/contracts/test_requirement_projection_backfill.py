from __future__ import annotations

"""任务层确定性回填 + 多口径识别的契约（plan 2026-11-10-time-range-and-metric-slot-backfill）。

钉住三件事，全部来自真机事故（`explicit-region-sales-ranking` 金标 96,318,061.74
vs 实测 326,102,457.72；`session-scope-carryover` 金标华东 1 月 8.99M vs 实测 83.7M）：

1. 原话里有绝对时间范围时，**任务状态**（不是只有卡片）必须带上它——多轮继承读的是任务，
   只回填卡片等于每轮都丢掉上一轮的推导结果；
2. 原话里点名了注册表口径时，不再问「用哪个指标？」；「净销售额」不许被「销售额」抢，
   反过来两个不同口径同时出现时**两个都要**；
3. 「用哪个指标？」的候选顺序由原话决定，不是 `sorted()` 的中文码点序
   （`净` U+51C0 < `销` U+9500，曾把用户明说的「销售额」挤出候选）。
"""

from datetime import date

import pytest

from app.agent.requirement_parser import (
    resolve_time_range,
    scope_from_query,
    time_range_from_query,
)
from app.agent.requirement_slots import match_metric_terms
from app.agent.turn_understanding import (
    assess_reportability,
    project_card,
    reduce_task,
)
from app.models.turn_plan import ActiveAnalysisTask, AnalysisGoal, TurnPlan

pytestmark = pytest.mark.contracts


# --- D1 · 绝对时间范围抽取（纯函数） ---------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("2024年各区域销售额排名", "2024年"),
        ("2024年7月华东销售额", "2024年7月"),
        ("2024年7月-8月的销售额", "2024年7月-8月"),
        ("2024年7月~9月华东的销售", "2024年7月-9月"),
        ("2024年十一月销售额", "2024年11月"),
        ("看一下 2023 年 11 月的单子", "2023年11月"),
        ("2024年12月-7月（倒序也要归一）", "2024年7月-12月"),
    ],
)
def test_time_range_extracted_from_query(query: str, expected: str) -> None:
    assert time_range_from_query(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "最近卖得咋样",
        "今年销售额",
        "上个月卖了多少",
        "把订单列出来看看",
        "TOP10 2024 的门店",  # 没有「年」→ 不许把裸数字当年份
    ],
)
def test_time_range_absent_or_relative_is_not_invented(query: str) -> None:
    """相对时间词与裸数字**不在这里**换算：返回 None 交回既有链路。"""
    assert time_range_from_query(query) is None


# --- D1b · 相对年份（2026-11-11，plan sql-filter-in-join-on） ----------------
#
# 真机事故：「分析下去年华南区域的销售」里「去年」此前既不绝对、也没有默认值 →
# 模型漏抽就永久没有时间范围，SQL 一个年份谓词都没有，返回华南 2023–2026 四年合计
# 1.84 亿（2025 年真值 4784 万）。相对年份同样是原话里的**字面事实**。

_TODAY = date(2026, 11, 11)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("分析下去年华南区域的销售", "2025年"),
        ("去年每个月的情况", "2025年"),
        ("前年华东销售额", "2024年"),
        ("上一年各区域排名", "2025年"),
        ("去年和前年的对比呢", "2025年"),   # 只认第一个相对词
    ],
)
def test_relative_year_resolved(query: str, expected: str) -> None:
    assert time_range_from_query(query, today=_TODAY) == expected


@pytest.mark.parametrize(
    "query",
    [
        "今年销售额比去年增长多少",   # 「去年」是对比基线，主查询是今年
        "去年同期销售额",             # 同比语义，基线不是时间范围
        "和去年比怎么样",
        "较去年提升了多少",
        "同比表现如何",
    ],
)
def test_relative_year_in_comparison_is_baseline_not_range(query: str) -> None:
    """比较语境里的「去年」不能当时间范围，否则主查询落到去年、与问法矛盾。"""
    assert time_range_from_query(query, today=_TODAY) is None


def test_absolute_year_still_wins_over_relative() -> None:
    """原话里明写年份时，绝对写法优先（既有契约不回归）。"""
    assert time_range_from_query("2024年比去年好在哪", today=_TODAY) == "2024年"


def test_resolve_time_range_prefers_existing_value() -> None:
    """模型/状态已给值时不覆盖——相对换算只是「原来没有值」时的兜底。"""
    assert resolve_time_range("去年华南的销售", "2023年", today=_TODAY) == "2023年"



# --- D2 · 口径识别：子串消歧 + 多口径全留 ----------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("2024年各区域销售额排名", ["销售额"]),
        ("2024年净销售额多少", ["净销售额"]),          # 长词覆盖短词，不许被抢
        ("2024年扣退款销售额多少", ["净销售额"]),
        ("各区域实收金额", ["实收金额"]),
    ],
)
def test_single_metric_term_is_identified(query: str, expected: list[str]) -> None:
    assert [metric.name for metric in match_metric_terms(query)] == expected


def test_two_distinct_metrics_are_both_kept() -> None:
    """「销售额和退货金额对比」要的是两个口径。

    修复前只取「最长命中」→ 只剩「退款金额」（4 字 > 3 字），
    被测 SQL 只出 `退款金额` 一列，用户要的对比少了一半。
    """
    hits = [metric.name for metric in match_metric_terms("2024年各产品类别的销售额和退货金额对比")]
    assert hits == ["销售额", "退款金额"]


def test_both_metrics_kept_when_each_appears_on_its_own() -> None:
    """「销售额和净销售额」里「销售额」有一次是被覆盖的、一次不是 → 两个都留。"""
    hits = [metric.name for metric in match_metric_terms("销售额和净销售额都看一下")]
    assert hits == ["销售额", "净销售额"]


def test_query_without_metric_term_yields_nothing() -> None:
    assert match_metric_terms("2024年各区域排名") == []


# --- D2b · 数据范围抽取 ------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("2024年华东地区每个月的销售额趋势", ["华东"]),
        ("2024年华东销售额", ["华东"]),
        ("华东和华南对比一下销售额", ["华东", "华南"]),
        ("2024年各区域销售额排名", []),      # 「各区域」不是具体范围
        ("看一下销售额", []),
    ],
)
def test_scope_extracted_from_query(query: str, expected: list[str]) -> None:
    assert scope_from_query(query) == expected


# --- D3 · 澄清候选顺序 ------------------------------------------------------


def _task(*, metrics: list[str] | None = None, time_range: str | None = None) -> ActiveAnalysisTask:
    return ActiveAnalysisTask(
        revision=1,
        goals=[AnalysisGoal(type="ranking", metric="销售额", dimension="区域")],
        scope=[],
        metrics=list(metrics or []),
        time_range=time_range,
        granularity=None,
        detail_level=None,
    )


KNOWN = {"销售额", "净销售额", "实收金额", "客单价", "订单数", "退款金额"}


def test_metric_options_put_query_hit_first() -> None:
    """用户明说的口径必须是第一个候选（否则夹具盲选就会选错）。"""
    report = assess_reportability(
        _task(), known_metrics=KNOWN, preferred_metrics=["销售额"],
    )
    metric_blocker = next(b for b in report.blockers if b.key == "metric")
    assert metric_blocker.options[0] == "销售额"
    assert len(metric_blocker.options) <= 3


def test_metric_options_without_preference_stay_sorted() -> None:
    """没有原话命中时保持既有的确定性排序（向后兼容，不引入随机顺序）。"""
    report = assess_reportability(_task(), known_metrics=KNOWN)
    metric_blocker = next(b for b in report.blockers if b.key == "metric")
    assert metric_blocker.options == sorted(KNOWN)[:3]


# --- D4 · 任务层回填（多轮继承的真正读取源） --------------------------------


def _plan(act: str, *, goals=None, update_fields=None, clear_fields=None) -> TurnPlan:
    return TurnPlan(
        conversation_act=act,
        analysis_goals=list(goals or []),
        update_fields=dict(update_fields or {}),
        clear_fields=list(clear_fields or []),
    )


_RANKING = AnalysisGoal(type="ranking", metric="销售额", dimension="区域")


def test_backfill_lands_on_task_not_only_card() -> None:
    """真机形态：模型只给了 goals，`metrics=[]`、`time_range=null`。

    修复前只补卡片 → 任务仍是空的 → 下一轮继承什么都拿不到。
    """
    task, revision = reduce_task(
        None,
        _plan("new_task", goals=[_RANKING]),
        user_query="2024年各区域销售额排名",
    )

    assert task.time_range == "2024年"
    assert task.metrics == ["销售额"]
    # 代码补了什么必须能从审计里看出来（changes 是 before/after；新任务没有前置键 → None）
    assert revision.changes.get("time_range") == (None, "2024年")
    assert revision.changes.get("metrics") == (None, ["销售额"])


def test_second_turn_inherits_backfilled_values() -> None:
    """第 2 轮不重复说年份/区域时，继承的是**补过**的任务（这才是真缺陷的判据）。

    模型给的是 `time_range=null` / `scope=[]`（真机形态），年份与区域都得靠代码补，
    否则第 2 轮「再看月度趋势」会退化成「全部区域、全部年份」。
    """
    first, _ = reduce_task(
        None,
        _plan("new_task", goals=[_RANKING]),
        user_query="2024年华东销售额",
    )
    assert first.time_range == "2024年"
    assert first.scope == ["华东"]

    second, _ = reduce_task(
        first,
        _plan("follow_up", goals=[AnalysisGoal(type="trend", unit="month", metric="销售额")]),
        user_query="再看月度趋势",
    )

    assert second.time_range == "2024年"      # 继承，不是 None
    assert second.metrics == ["销售额"]
    assert second.scope == ["华东"]


def test_model_value_is_respected() -> None:
    """模型给了值就不覆盖——回填只在留空时发生。"""
    task, _ = reduce_task(
        None,
        _plan("new_task", goals=[_RANKING], update_fields={"time_range": "2023年"}),
        user_query="2024年各区域销售额排名",
    )
    assert task.time_range == "2023年"


def test_repeated_metric_terms_do_not_duplicate() -> None:
    """同一轮重复提到同一口径不产生重复项。"""
    task, _ = reduce_task(
        None,
        _plan("new_task", goals=[_RANKING]),
        user_query="2024年销售额，各区域销售额排名",
    )
    assert task.metrics == ["销售额"]


# --- 投影链路：任务 → 可报告性 → 卡片 --------------------------------------


def _project(task: ActiveAnalysisTask, query: str):
    report = assess_reportability(task, known_metrics=KNOWN)
    return project_card(task, reportability=report, summary=query)


def test_incident_shape_no_longer_asks_for_metric() -> None:
    task, _ = reduce_task(None, _plan("new_task", goals=[_RANKING]),
                          user_query="2024年各区域销售额排名")
    card = _project(task, "2024年各区域销售额排名")

    assert card.time_range == "2024年"
    assert card.target_metrics == ["销售额"]
    assert card.missing_fields == []
    assert card.status == "complete"


def test_multi_metric_question_keeps_both_on_card() -> None:
    task, _ = reduce_task(
        None,
        _plan("new_task", goals=[AnalysisGoal(type="compare", dimension="品类")]),
        user_query="2024年各产品类别的销售额和退货金额对比",
    )
    card = _project(task, "2024年各产品类别的销售额和退货金额对比")

    assert card.target_metrics == ["销售额", "退款金额"]
    assert card.missing_fields == []


def test_ambiguous_query_still_asks() -> None:
    """原话没有点名口径时不猜：澄清必须保留（回填不许把「没说」变成「说了」）。"""
    task, _ = reduce_task(None, _plan("new_task", goals=[_RANKING]),
                          user_query="2024年各区域排名")
    card = _project(task, "2024年各区域排名")

    assert task.metrics == []
    assert card.target_metrics == []
    assert [mf.key for mf in card.missing_fields] == ["metric"]
    assert card.status == "missing"
