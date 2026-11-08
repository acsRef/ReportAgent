"""LLM 预算表（plan 2026-11-08-llm-budget-and-accounting D2）。

预算此前是散在 6 个文件里的 7 处字面量，改一处只能靠记忆知道影响哪条链。
本组用例钉住三件事：

1. 表的形状与取值（任务 → max_tokens / stage），未登记任务有保守兜底；
2. turn 的四个旋钮（思考预算 / 总额 / 单次超时 / 快速重试超时 / 单轮预算）语义不变；
3. **接线**：6 个 caller 不再自带字面量，`reasoning_client` 的旧函数是薄委托。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.llm import budgets, reasoning_client

pytestmark = pytest.mark.contracts

_APP = Path(__file__).resolve().parents[2] / "app"


class TestBudgetTable:
    def test_每个任务都有预算且带域(self) -> None:
        for task, budget in budgets.TASK_BUDGETS.items():
            assert budget.max_tokens > 0, task
            assert budget.timeout_seconds > 0, task
            assert budget.stage in {"requirement", "execution", "report", "memory"}, task

    def test_取值与真机采样一致(self) -> None:
        assert budgets.max_tokens("intent") == 200
        assert budgets.max_tokens("requirement_parse") == 1500
        assert budgets.max_tokens("sql_plan") == 1500
        assert budgets.max_tokens("sql_generate") == 1500
        assert budgets.max_tokens("sql_repair") == 1500
        assert budgets.max_tokens("report_plan") == 1500
        assert budgets.max_tokens("report_narrative") == 900
        assert budgets.max_tokens("memory_summary") == 1000

    def test_未登记任务回落到保守默认(self) -> None:
        # key 拼错时宁可给足，也不要让回答被截断
        assert budgets.max_tokens("sql_genrate") == 1500
        assert budgets.stage_of("sql_genrate") == "unknown"

    def test_按域取值(self) -> None:
        assert budgets.stage_of("intent") == "requirement"
        assert budgets.stage_of("sql_repair") == "execution"
        assert budgets.stage_of("report_narrative") == "report"
        assert budgets.stage_of("memory_summary") == "memory"


class TestTurnKnobs:
    def test_思考预算默认与环境覆盖(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TURN_THINKING_BUDGET", raising=False)
        # 2026-11-08：512 在多轮追问里每次都顶满（界面上永远「预算用尽」），默认抬到 1536
        assert budgets.thinking_budget() == 1536
        monkeypatch.setenv("TURN_THINKING_BUDGET", "2048")
        assert budgets.thinking_budget() == 2048
        assert budgets.thinking_budget(1024) == 1024

    def test_总额跟随预算(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TURN_MAX_TOKENS", raising=False)
        assert budgets.turn_max_tokens(512) == 1412
        assert budgets.turn_max_tokens(1024) == 1924

    def test_总额显式配置优先(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TURN_MAX_TOKENS", "4000")
        assert budgets.turn_max_tokens(512) == 4000

    def test_总额低于预算时兜底(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TURN_MAX_TOKENS", "600")
        assert budgets.turn_max_tokens(1024) == 1324

    def test_三个时间旋钮的默认值(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("LLM_TURN_TIMEOUT", "LLM_TURN_FAST_TIMEOUT", "TURN_REQUEST_BUDGET"):
            monkeypatch.delenv(name, raising=False)
        assert budgets.turn_timeout() == 90.0
        assert budgets.turn_fast_timeout() == 30.0
        assert budgets.turn_request_budget() == 120.0
        # 最坏耗时口径：首轮 + 快速重试不超过单轮预算
        assert budgets.turn_fast_timeout() + 90.0 == budgets.turn_request_budget()

    def test_环境变量盖得住(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_TURN_TIMEOUT", "150")
        monkeypatch.setenv("LLM_TURN_FAST_TIMEOUT", "20")
        monkeypatch.setenv("TURN_REQUEST_BUDGET", "200")
        assert budgets.turn_timeout() == 150.0
        assert budgets.turn_fast_timeout() == 20.0
        assert budgets.turn_request_budget() == 200.0

    @pytest.mark.parametrize("bad", ["abc", "", "  "])
    def test_脏值回默认不炸(
        self, monkeypatch: pytest.MonkeyPatch, bad: str,
    ) -> None:
        monkeypatch.setenv("TURN_REQUEST_BUDGET", bad)
        assert budgets.turn_request_budget() == 120.0
        monkeypatch.setenv("LLM_TURN_TIMEOUT", bad)
        assert budgets.turn_timeout() == 90.0
        monkeypatch.setenv("TURN_THINKING_BUDGET", bad)
        assert budgets.thinking_budget() == 1536


class TestNoScatteredLiterals:
    """接线守卫：预算字面量不允许再散落在 caller 里（否则本表形同虚设）。"""

    _CALLERS = (
        "agent/intent.py",
        "agent/sql_graph.py",
        "agent/report_graph.py",
        "agent/requirement_parser.py",
        "memory/conversation.py",
    )

    def test_caller_里没有_token_字面量(self) -> None:
        offenders: list[str] = []
        for rel in self._CALLERS:
            text = (_APP / rel).read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if "max_tokens=" not in stripped:
                    continue
                if "budgets.max_tokens(" in stripped:
                    continue
                offenders.append(f"{rel}: {stripped}")
        assert offenders == [], "预算必须走 app/llm/budgets.py：" + "; ".join(offenders)

    def test_reasoning_client_旧函数是薄委托(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TURN_MAX_TOKENS", raising=False)
        monkeypatch.delenv("TURN_THINKING_BUDGET", raising=False)
        monkeypatch.delenv("LLM_TURN_TIMEOUT", raising=False)
        assert reasoning_client.resolve_max_tokens(512) == budgets.turn_max_tokens(512)
        assert reasoning_client.resolve_thinking_budget() == budgets.thinking_budget()
        assert reasoning_client.turn_timeout() == budgets.turn_timeout()

    def test_预算表是唯一出处(self) -> None:
        source = (_APP / "llm/budgets.py").read_text(encoding="utf-8")
        # 表本身必须自洽：这行改动等于改全局，所以三个字段都要在
        assert "TASK_BUDGETS: dict[str, Budget]" in source
        assert "def max_tokens(task: str) -> int" in source
