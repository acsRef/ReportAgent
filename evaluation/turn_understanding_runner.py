"""Turn Understanding 模型对比（plan 2026-11-05-turn-understanding-agent Phase 2）。

对同一份 `evaluation/turn_understanding.yaml`，用**同一个 prompt / 任务上下文 / 输出契约**
逐个模型跑一遍，产出：

    意图准确率 / 目标准确率 / 方向-单位准确率 / 继承-修改准确率 / JSON 合法率 / 平均延迟

用法（仓库根目录，真 LLM key）：

    python -m evaluation.turn_understanding_runner \
        --models Qwen/Qwen3.8-27B deepseek-ai/DeepSeek-V4-Flash Qwen/Qwen3.5-35B-A3B

不给 `--models` 时默认跑上面三个 + 当前主模型作为基线。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO))

import yaml  # noqa: E402

from app.agent.turn_understanding import (  # noqa: E402
    assess_reportability,
    reduce_task,
    understand_turn,
)
from app.models.turn_plan import ActiveAnalysisTask  # noqa: E402

CASES = Path(__file__).resolve().parent / "turn_understanding.yaml"

DEFAULT_MODELS = (
    "Qwen/Qwen3.8-27B",
    "deepseek-ai/DeepSeek-V4-Flash",
    "Qwen/Qwen3.5-35B-A3B",
)


def _load_cases() -> list[dict]:
    data = yaml.safe_load(CASES.read_text(encoding="utf-8")) or {}
    return data.get("cases") or []


def _task(raw: dict | None) -> ActiveAnalysisTask | None:
    if not raw:
        return None
    return ActiveAnalysisTask.model_validate(raw)


def _score_case(case: dict, plan, reasoning: str, elapsed: float) -> dict:
    expect = case.get("expect") or {}
    plan_ok = plan is not None
    act_ok = None
    goals_ok = None
    extremes_ok = None
    inherit_ok = None
    cleared_ok = None
    updated_ok = None
    task_after = None
    reportability = None

    if plan_ok:
        if expect.get("act") is not None:
            act_ok = plan.conversation_act == expect["act"]
        if expect.get("goals") is not None:
            goals_ok = {goal.type for goal in plan.analysis_goals} == set(expect["goals"])
        if expect.get("directions") is not None:
            got = {goal.direction for goal in plan.analysis_goals if goal.direction}
            extremes_ok = got == set(expect["directions"])
            if expect.get("unit"):
                units = {goal.unit for goal in plan.analysis_goals if goal.unit}
                extremes_ok = bool(extremes_ok and units == {expect["unit"]})
        prior = _task(case.get("task"))
        task_after, revision = reduce_task(prior, plan, user_query=case["query"])
        if expect.get("preserve"):
            inherit_ok = all(
                getattr(task_after, field, None) == getattr(prior, field, None)
                for field in expect["preserve"]
            ) if prior is not None else None
        if expect.get("cleared"):
            cleared_ok = all(
                getattr(task_after, field, None) in (None, [], {}) for field in expect["cleared"]
            )
        if expect.get("updated"):
            updated_ok = all(field in revision.changes for field in expect["updated"])
        reportability = assess_reportability(
            task_after, known_metrics={"销售额", "净销售额", "实收金额", "订单数", "销量", "退款金额"},
        )
        if expect.get("needs_clarification"):
            needs_ok = reportability.verdict == "NEEDS_CLARIFICATION"
            updated_ok = needs_ok if updated_ok is None else (updated_ok and needs_ok)

    return {
        "id": case["id"],
        "json_ok": plan_ok,
        "act_ok": act_ok,
        "goals_ok": goals_ok,
        "extremes_ok": extremes_ok,
        "inherit_ok": inherit_ok,
        "cleared_ok": cleared_ok,
        "updated_ok": updated_ok,
        "elapsed": elapsed,
        "has_reasoning": bool(reasoning),
        "verdict": (reportability.verdict if reportability else None),
    }


def _rate(rows: list[dict], key: str) -> str:
    values = [row[key] for row in rows if row.get(key) is not None]
    if not values:
        return "—"
    return f"{sum(1 for v in values if v)}/{len(values)}"


def run_model(model: str, cases: list[dict], *, thinking: bool, budget: int) -> dict:
    rows: list[dict] = []
    reasons: list[str] = []
    for case in cases:
        started = time.monotonic()
        plan, reasoning = understand_turn(
            case["query"],
            task=_task(case.get("task")),
            model=model,
            enable_thinking=thinking,
            thinking_budget=budget,
        )
        elapsed = time.monotonic() - started
        if reasoning:
            reasons.append(case["id"])
        rows.append(_score_case(case, plan, reasoning, elapsed))
        print(
            f"  [{model}] {case['id']:<20} json={rows[-1]['json_ok']} "
            f"act={rows[-1]['act_ok']} goals={rows[-1]['goals_ok']} "
            f"inherit={rows[-1]['inherit_ok']} {elapsed:.1f}s "
            f"reasoning={'yes' if reasoning else 'no'}"
        )
    latencies = [row["elapsed"] for row in rows]
    return {
        "model": model,
        "json": _rate(rows, "json_ok"),
        "act": _rate(rows, "act_ok"),
        "goals": _rate(rows, "goals_ok"),
        "extremes": _rate(rows, "extremes_ok"),
        "inherit": _rate(rows, "inherit_ok"),
        "cleared": _rate(rows, "cleared_ok"),
        "updated": _rate(rows, "updated_ok"),
        "median_s": round(statistics.median(latencies), 1) if latencies else 0.0,
        "reasoning_cases": len(reasons),
        "rows": rows,
    }


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=None)
    parser.add_argument("--no-thinking", action="store_true")
    parser.add_argument("--budget", type=int, default=1024)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    models = args.models or list(DEFAULT_MODELS)
    cases = _load_cases()
    print(f"cases: {len(cases)} | models: {models} | thinking={not args.no_thinking}")

    results = []
    for model in models:
        print(f"\n=== {model} ===")
        try:
            results.append(run_model(
                model, cases, thinking=not args.no_thinking, budget=args.budget,
            ))
        except Exception as exc:  # noqa: BLE001 - 单个模型不可用不影响其它
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            results.append({"model": model, "error": str(exc)[:200]})

    print("\n=== summary ===")
    header = f"{'model':<32} {'json':<6} {'act':<7} {'goals':<7} {'extreme':<8} {'inherit':<8} {'upd':<6} {'median':<7} {'CoT'}"
    print(header)
    for row in results:
        if row.get("error"):
            print(f"{row['model']:<32} ERROR {row['error']}")
            continue
        print(
            f"{row['model']:<32} {row['json']:<6} {row['act']:<7} {row['goals']:<7} "
            f"{row['extremes']:<8} {row['inherit']:<8} {row['updated']:<6} "
            f"{row['median_s']:<7} {row['reasoning_cases']}/{len(cases)}"
        )

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"\njson written: {args.json_out}")


if __name__ == "__main__":
    main()
