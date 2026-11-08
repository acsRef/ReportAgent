"""LLM 预算表：按任务类型给 `max_tokens`，turn 的四个旋钮集中在这里。

为什么要有这张表（plan 2026-11-08-llm-budget-and-accounting D2）：预算此前是散在
6 个文件里的 7 处字面量（`intent.py 200`、`sql_graph 1500×3`、`report_graph 1500/900`、
`conversation 1000`、`requirement_parser 1500`）+ turn 的 `budget + 900` 推算。
改一处只能靠记忆知道影响哪条链，也没法按域（requirement / execution / report / memory）
统计「这条链一共花了多少预算」。

口径澄清（别被名字骗了）：这里的 `max_tokens` 是 **completion 上限**，不是上下文窗口；
思考类模型（turn）里它是「思考 + 回答」共享的总额，所以 turn 的总额是**推算**出来的
（`turn_max_tokens`），不能直接写进表里。

env 旋钮全部在本模块解析，调用方只认函数——配置错误只在一个地方兜底。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Budget:
    """一条链的单次调用预算。

    `max_tokens`：completion 上限（思考 + 回答，如果该调用开思考）；
    `timeout_seconds`：单次调用超时（与 `reliability.timeout` 的任务级超时是两层）；
    `stage`：所属域，用于按域统计/告警（requirement / execution / report / memory）。
    """

    max_tokens: int
    timeout_seconds: float
    stage: str


# 唯一预算表：改这里就是改全局。数值来由见 plan 的 Context 节与真机采样。
TASK_BUDGETS: dict[str, Budget] = {
    # 需求域：意图分类只回一个短标签，200 够；需求解析要吐完整卡 JSON。
    "intent": Budget(200, 30, "requirement"),
    "requirement_parse": Budget(1500, 90, "requirement"),
    # 执行域：plan / generate / repair 三处都是结构化输出，同一档。
    "sql_plan": Budget(1500, 90, "execution"),
    "sql_generate": Budget(1500, 90, "execution"),
    "sql_repair": Budget(1500, 90, "execution"),
    # 报告域：plan 要出结构化 ReportSpec，narrative 只写叙述所以更小。
    "report_plan": Budget(1500, 90, "report"),
    "report_narrative": Budget(900, 60, "report"),
    # 记忆域：会话摘要。
    "memory_summary": Budget(1000, 60, "memory"),
}

# 任务名写错时的兜底：宁可给足，也不要因为 key 拼错让回答被截断。
_FALLBACK = Budget(1500, 90, "unknown")


def budget_for(task: str) -> Budget:
    """按任务名取预算；未登记的任务回落到保守默认档。"""
    budget = TASK_BUDGETS.get(task)
    if budget is None:
        logger.debug("no budget registered for task %r, using fallback", task)
        return _FALLBACK
    return budget


def max_tokens(task: str) -> int:
    """`call_llm(..., max_tokens=budgets.max_tokens("<task>"))` 的唯一出处。"""
    return budget_for(task).max_tokens


def stage_of(task: str) -> str:
    """任务所属域（requirement / execution / report / memory），用于统计。"""
    return budget_for(task).stage


# ── turn（需求轮的理解调用）旋钮 ──────────────────────────────────────────
#
# turn 是唯一开「思考」的调用，所以它的预算是三件事叠出来的：
#   思考预算（thinking_budget） + 回答余量（+900） = 总额（max_tokens）
# 再外加一个 request 级 deadline（贯穿重试），三者必须成对看，单独调一个都会出问题：
# 只调大预算不调总额 → 回答被挤没 → JSON 截断 → 表现为「理解失败」。


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r 不是数字，按 %s 处理", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r 不是整数，按 %s 处理", name, raw, default)
        return default


def thinking_budget(override: int | None = None) -> int:
    """思考预算（token）：思考阶段最多花多少（`TURN_THINKING_BUDGET`，默认 512）。"""
    if override is not None:
        return max(1, int(override))
    return max(1, _env_int("TURN_THINKING_BUDGET", 512))


def turn_max_tokens(budget: int) -> int:
    """总额（思考 + 回答共享）：`TURN_MAX_TOKENS`，未设时 = `max(1200, budget+900)`。

    2026-11-05 的教训：`TURN_MAX_TOKENS` 被配成 ≤ 预算时，回答一个 token 都拿不到
    → JSON 截断 → 表现成「理解失败」。这里兜底抬到 `预算 + 300` 并告警，
    不让配置错误静默生效。
    """
    configured = _env_int("TURN_MAX_TOKENS", 0)
    if configured > 0:
        if budget > 0 and configured < budget + 300:
            logger.warning(
                "TURN_MAX_TOKENS=%s 小于思考预算 %s + 300，回答会被截断；按 %s 兜底",
                configured, budget, budget + 300,
            )
            return budget + 300
        return configured
    return max(1200, budget + 900)


def turn_timeout() -> float:
    """turn 单次调用超时（`LLM_TURN_TIMEOUT`，默认 90s）。"""
    return _env_float("LLM_TURN_TIMEOUT", 90.0)


def turn_fast_timeout() -> float:
    """超时后「不思考」快速重试的超时（`LLM_TURN_FAST_TIMEOUT`，默认 30s）。

    2026-11-08 收口：此前是 45s，最坏 90 + 45 = 135s。改成 30s 后与
    `turn_request_budget()`（120s）对齐，最坏 ≈ 120s。
    """
    return _env_float("LLM_TURN_FAST_TIMEOUT", 30.0)


def turn_request_budget() -> float:
    """单轮总预算（`TURN_REQUEST_BUDGET`，默认 120s）：贯穿首轮 + 快速重试。

    这是「用户感知的卡住」的上界，也是跳过重试的判据——剩余时间不够就不重试，
    直接回落通用解析，不再白等一次。
    """
    return _env_float("TURN_REQUEST_BUDGET", 120.0)
