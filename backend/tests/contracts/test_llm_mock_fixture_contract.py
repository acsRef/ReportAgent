"""防漂移闸：mock fixture 里的 canned SQL 必须满足当前 **SQL 确定性契约**。

为什么需要（plan 2026-11-18-e2e-contract-fixture-realign）：

夜间 `e2e-contract` 连红 7 天（run 16–22），根因不是产品缺陷而是**夹具漂移**——
`7a9092b` 给 `sql_graph._validate` 加了 C1「plan 要求的过滤取值必须真的出现在 SQL 里」，
但 `backend/tests/fixtures/llm_responses/` 里 3 个 case 的 canned SQL 还是加闸前的写法（缺年份谓词）：

    required filter missing, routing to repair: 缺少用户要求的过滤条件 2024 …
    MockLLMMiss case=empty-result key=sql_generate:2      ← 去 repair，但 fixture 没这一跳

结果是三条 spec（04 EMPTY / 06 报告 v2 / 07 后台执行）每晚必红、没人看。当时的四类修法里，
「改 mock keying 兜底」「砍 spec 子集」「放宽断言」都不会让它们真的绿，只会把红藏起来；
正解是**同步夹具**，并加这道闸：以后再加确定性 SQL 契约时，夹具不同步会先在 L1 门禁里红，
而不是等夜间 E2E 红、再没人看。

判据不重写：直接用 `sql_graph._missing_filter_violation`（同一个函数），否则闸门改了口径这里还是绿的。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.agent.sql_graph import _missing_filter_violation

pytestmark = pytest.mark.contracts

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "llm_responses"

# 这些 case 的 fixture **故意**是坏 SQL（用来驱动 repair / 失败路径）：
# - failed-result：spec 05 断言「坏 SQL 连败至预算耗尽 → ErrorCard」，坏本身就是被测对象；
# - retry：spec 03 断言「首跳失败 → repair 成功」，首跳必须是错的；
# - replay-object-not-found：首跳故意引用不存在的列，驱动 object_not_found 修复。
# 它们的 C1 违约是**附带**的（坏 SQL 自然不会带年份谓词），不是要保护的成功路径。
_INTENTIONALLY_BROKEN: frozenset[tuple[str, str]] = frozenset({
    ("failed-result.json", "sql_generate:1"),
    ("failed-result.json", "sql_generate:2"),
    ("failed-result.json", "sql_generate:3"),
    ("retry.json", "sql_generate:1"),
    ("replay-object-not-found.json", "sql_generate:1"),
})


def _fixture_files() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("*.json"))


def _pairs(data: dict[str, Any]) -> list[tuple[str, str, Any, Any]]:
    """配对 (seq, sql, plan)：`sql_generate:N` 用 `sql_plan:N`；没有 N 就用 `sql_plan:1`。"""
    plans = {k.split(":", 1)[1]: v for k, v in data.items() if k.startswith("sql_plan:")}
    gens = {k.split(":", 1)[1]: v for k, v in data.items() if k.startswith("sql_generate:")}
    out: list[tuple[str, str, Any, Any]] = []
    for seq, sql in sorted(gens.items()):
        plan = plans.get(seq) or plans.get("1")
        if plan is None:
            continue
        out.append((seq, f"sql_generate:{seq}", sql, plan))
    return out


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: p.name)
def test_fixture_sql_satisfies_required_filter_contract(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for seq, key, sql, plan in _pairs(data):
        if (path.name, key) in _INTENTIONALLY_BROKEN:
            continue
        violation = _missing_filter_violation(sql, plan)
        if violation:
            problems.append(f"  {key}: {violation}\n    当前 SQL: {sql}")
    assert not problems, (
        f"{path.name} 的 canned SQL 不满足「必需过滤必须出现在 SQL 里」（C1）。\n"
        "修法：给 SQL 补上 plan 要求的谓词（例如 `JOIN dim_date d ON d.full_date = o.order_date`"
        " + `WHERE d.year = 2024`）。\n"
        "如果该 case 的坏 SQL 本身就是被测对象（驱动 repair/失败路径），"
        "把它加进本文件的 `_INTENTIONALLY_BROKEN` 并注明原因。\n"
        + "\n".join(problems)
    )


def test_intentional_broken_entries_are_still_broken() -> None:
    """反向闸：`_INTENTIONALLY_BROKEN` 里的条目必须真的还在、且仍然违约。

    否则白名单会慢慢变成「历史遗留名单」——夹具修好了却还挂着豁免，将来真漂移就没人管。
    """
    stale: list[str] = []
    for name, key in sorted(_INTENTIONALLY_BROKEN):
        path = FIXTURES_DIR / name
        assert path.exists(), f"白名单指向的 fixture 不存在：{name}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert key in data, f"{name} 里没有 {key}（白名单过期，请删掉这一条）"
        plan = data.get(f"sql_plan:{key.split(':')[1]}") or data.get("sql_plan:1")
        if plan is not None and _missing_filter_violation(data[key], plan) is None:
            stale.append(f"{name}:{key}")
    assert not stale, (
        "以下白名单条目现在已满足契约（SQL 被修好了）："
        f"{stale} —— 请从 _INTENTIONALLY_BROKEN 删掉，别让豁免留着"
    )
