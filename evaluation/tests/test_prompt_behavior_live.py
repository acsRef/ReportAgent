"""Prompt 行为评测（live，env-gated）：验「prompt 好不好」，不验「prompt 里有没有那句话」。

为什么需要（plan 2026-11-18-live-ci-pipeline，用户原话「测试的例子应该不是实时的吧」）：

- 离线套件里有 78 处断言 prompt **文案**（`system_contract` 里有没有某句），那是**冻结历史**：
  改一个字就假红，改坏行为却可能照绿；
- 本文件把 prompt 当**被测对象**，断言的是**它驱动出来的行为**：对话行为分类、分析目标类型、
  多轮继承/改写、否决词、是否过度澄清。

判据全部是确定性的（闭集枚举 / 集合包含 / 归一后相等 / 子串），**不引入 LLM 裁判**；
每例在用例文件里都写了 `breaks_when`（什么改动会让它红），没有它就不收。

跑法（需要仓库根 `.env` 里的真 LLM key，不需要后端进程）：

    RUN_PROMPT_BEHAVIOR=1 pytest evaluation/tests/test_prompt_behavior_live.py -q -s

`-s` 会打印每例的 plan 摘要与失败原因；失败时额外打印模型原文，便于判断是 prompt 退化还是
用例本身写错了。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PROMPT_BEHAVIOR") != "1",
    reason="RUN_PROMPT_BEHAVIOR != 1; skipping live prompt behavior suite",
)

_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _ROOT / "backend"
for _p in (str(_ROOT), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CASES_PATH = _ROOT / "evaluation" / "prompt_behavior_cases.json"


def _load_cases() -> list[dict[str, Any]]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _build_task(seed: dict[str, Any] | None):
    """从用例 JSON 造一个 ActiveAnalysisTask（多轮用例用）。"""
    if not seed:
        return None
    from app.models.turn_plan import ActiveAnalysisTask

    return ActiveAnalysisTask.model_validate(seed)


def _plan_dict(plan) -> dict[str, Any]:
    return plan.model_dump(mode="json")


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _goal_field(goal: dict[str, Any], field: str) -> Any:
    return goal.get(field)


def judge(criteria: list[dict[str, Any]], plan_dict: dict[str, Any]) -> list[str]:
    """返回失败原因列表（空 = 全过）。纯确定性，无模型参与。"""
    problems: list[str] = []
    goals = plan_dict.get("analysis_goals") or []
    goal_types = {str(g.get("type")) for g in goals}
    plan_text = json.dumps(plan_dict, ensure_ascii=False)

    for crit in criteria:
        kind = crit["type"]
        if kind == "enum_exact":
            observed = plan_dict.get(crit["path"])
            if str(observed or "").strip().lower() != str(crit["expected"]).strip().lower():
                problems.append(f"{crit['path']}={observed!r}，期望 {crit['expected']!r}")
        elif kind == "goals_empty":
            if goals:
                problems.append(f"期望没有分析目标，实际 {len(goals)} 个：{goal_types}")
        elif kind == "goal_types_include":
            missing = [t for t in crit["expected"] if t not in goal_types]
            if missing:
                problems.append(f"缺少目标类型 {missing}（实际 {sorted(goal_types)}）")
        elif kind == "goal_types_exclude":
            bad = [t for t in crit["expected"] if t in goal_types]
            if bad:
                problems.append(f"不应出现目标类型 {bad}（实际 {sorted(goal_types)}）")
        elif kind == "goal_any":
            where = crit.get("where") or {}
            matched = [
                g for g in goals
                if all(str(g.get(k)) == str(v) for k, v in where.items())
            ]
            if not matched:
                problems.append(f"没有目标满足 {where}（目标：{goals}）")
            else:
                expect = crit.get("expect") or {}
                hit = any(
                    all(str(g.get(k)) == str(v) for k, v in expect.items())
                    for g in matched
                )
                if not hit:
                    problems.append(f"匹配到 {where} 的目标字段不符，期望 {expect}")
        elif kind == "goal_dimension_contains":
            dims = [str(g.get("dimension") or "") for g in goals]
            if not any(any(token in dim for token in crit["expected"]) for dim in dims):
                problems.append(f"目标维度里没有 {crit['expected']}（实际 {dims}）")
        elif kind == "metric_mentioned":
            metrics = [str(g.get("metric") or "") for g in goals]
            if not any(any(token in m for token in crit["expected"]) for m in metrics):
                problems.append(f"目标指标里没有 {crit['expected']}（实际 {metrics}）")
        elif kind == "update_fields_contains":
            field = crit["field"]
            value = (plan_dict.get("update_fields") or {}).get(field)
            text = _as_text(value)
            missing = [t for t in crit["expected"] if str(t) not in text]
            if value is None or missing:
                problems.append(f"update_fields.{field} 缺少 {missing}（实际 {value!r}）")
        elif kind == "update_fields_absent":
            field = crit["field"]
            if field in (plan_dict.get("update_fields") or {}):
                problems.append(f"update_fields 不应改写 {field}")
        elif kind == "plan_json_contains_any":
            if not any(str(t) in plan_text for t in crit["expected"]):
                problems.append(f"plan 里找不到任一词 {crit['expected']}")
        elif kind == "plan_json_excludes":
            bad = [t for t in crit["expected"] if str(t) in plan_text]
            if bad:
                problems.append(f"plan 里出现了不该有的 {bad}")
        elif kind == "no_blocking_uncertainty":
            blocking = [
                u for u in (plan_dict.get("uncertainties") or []) if u.get("blocking")
            ]
            if blocking:
                fields = [u.get("field") for u in blocking]
                problems.append(f"不应阻塞澄清，却 blocking 了 {fields}")
        else:  # pragma: no cover - 用例文件写错时直接暴露
            problems.append(f"未知判据类型：{kind}")
    return problems


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
async def test_prompt_behavior(case: dict[str, Any]) -> None:
    from app.agent.turn_understanding import understand_turn_async

    # 为什么要多采样：prompt 行为是概率性的。实测同一个用例连跑两次，PB-04（占比）
    # 偶尔被判成 ranking、PB-06（打招呼）偶尔被判成分析任务——单次采样做 CI 只会变成噪音。
    # 规则：采样 N 次（默认 3），**多数正确**即通过；失败时打印每一采样的判定与 plan 原文。
    samples = max(1, int(os.getenv("RUN_PROMPT_BEHAVIOR_SAMPLES", "3")))
    task = _build_task(case.get("task"))

    verdicts: list[list[str]] = []
    plans: list[dict[str, Any]] = []
    replies: list[str] = []
    for _ in range(samples):
        plan, reply = await understand_turn_async(
            case["input"],
            task=task,
            history=case.get("history"),
        )
        replies.append(reply or "")
        if plan is None:
            verdicts.append([f"模型没有产出可用计划（原文：{(reply or '')[:300]}）"])
            plans.append({})
            continue
        plan_dict = _plan_dict(plan)
        plans.append(plan_dict)
        verdicts.append(judge(case["criteria"], plan_dict))

    passed = sum(1 for v in verdicts if not v)
    print(f"\n[{case['id']}] {case['input']}  → {passed}/{samples} 采样通过")
    for idx, (problems, plan_dict) in enumerate(zip(verdicts, plans), 1):
        act = plan_dict.get("conversation_act")
        goals = [g.get("type") for g in plan_dict.get("analysis_goals") or []]
        mark = "✓" if not problems else "✗"
        print(f"  {mark} #{idx} act={act} goals={goals} updates={plan_dict.get('update_fields')}")
        if problems:
            print(f"     问题：{problems}")
            print(f"     原文：{replies[idx - 1][:400]}")

    assert passed * 2 > samples, (
        f"{case['id']} 多采样多数不通过（{passed}/{samples}）：{case['breaks_when']}\n"
        f"各采样 plan={json.dumps(plans, ensure_ascii=False)}"
    )
