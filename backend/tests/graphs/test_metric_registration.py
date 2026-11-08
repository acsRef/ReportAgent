"""指标是否「已注册」必须是语义判断（plan 2026-11-08 真机 + 用户拍板）。

真机事故：用户说「分析 2024 年华东**销售**趋势和异常」→ 卡上口径写着「销售」→
可报告性拿它跟 `{"销售额", "净销售额", ...}` 做**字符串精确匹配** → 判「指标未注册：销售」
→ 整张卡 UNSUPPORTED，用户看到「待确认 1 项（指标未注册：销售）」却还被问「要现在生成报告吗」。

用户拍板：不要做精确匹配——同义词、别名、不同写法都会漏。
判断标准应该是「注册表能不能解析它」（`resolve_metric`），不是自己拼集合。
"""
from __future__ import annotations

import pytest

from app.agent.requirement_parser import _display_metric
from app.agent.turn_understanding import (
    _registry_has_metric,
    assess_reportability,
    project_card,
)
from app.models.turn_plan import ActiveAnalysisTask, AnalysisGoal

pytestmark = pytest.mark.graphs

KNOWN = {"销售额", "净销售额", "实收金额", "订单数", "销量", "退款金额"}


def _task(metrics: list[str]) -> ActiveAnalysisTask:
    return ActiveAnalysisTask(
        revision=1, metrics=metrics, time_range="2024年",
        goals=[AnalysisGoal(type="trend")],
    )  # type: ignore[call-arg]


class TestSemanticRegistration:
    @pytest.mark.parametrize("name", ["销售", "销售额", "GMV", "销售金额", "销售收入"])
    def test_同义词与别名都算已注册(self, name: str) -> None:
        assert _registry_has_metric(name) is True
        assert assess_reportability(_task([name]), known_metrics=KNOWN).verdict == "READY"

    @pytest.mark.parametrize("name", ["毛利润", "总的卖了多少", "随便写的"])
    def test_真正不存在的指标才拦(self, name: str) -> None:
        assert _registry_has_metric(name) is False
        report = assess_reportability(_task([name]), known_metrics=KNOWN)

        assert report.verdict == "UNSUPPORTED"
        assert name in report.notes[0]

    def test_不再依赖_known_metrics_做判定(self) -> None:
        """哪怕调用方传了一个「不含销售额」的集合，也不能把销售额判成未注册。"""
        report = assess_reportability(_task(["销售额"]), known_metrics={"别的指标"})

        assert report.verdict == "READY"

    def test_resolver_可注入(self) -> None:
        report = assess_reportability(
            _task(["销售"]), metric_resolver=lambda name: False,
        )

        assert report.verdict == "UNSUPPORTED"

    def test_known_metrics_只用来给候选(self) -> None:
        report = assess_reportability(_task([]), known_metrics=KNOWN)
        blocker = report.blockers[0]

        assert blocker.key == "metric"
        assert set(blocker.options) <= KNOWN
        assert blocker.options


class TestDisplayNormalization:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("销售", "销售额"),
        ("GMV", "销售额"),
        ("销售金额", "销售额"),
        ("销售额", "销售额"),
    ])
    def test_卡上口径归一成注册表业务名(self, raw: str, expected: str) -> None:
        """归一是展示层的责任：同义词不该原样留在卡上（用户自己也看不懂「销售」）。"""
        assert _display_metric(raw) == expected

    def test_不是指标的中文原样保留(self) -> None:
        assert _display_metric("订单金额") == "订单金额"


class TestCardFromSynonym:
    def test_整张卡不再因为同义词变成_UNAVAILABLE(self) -> None:
        task = _task(["销售"])
        report = assess_reportability(task, known_metrics=KNOWN)

        card = project_card(task, reportability=report, summary="2024年华东销售趋势和异常")

        assert report.verdict == "READY"
        assert card.status == "complete"
        assert not [f for f in card.missing_fields if f.key == "unsupported"]
