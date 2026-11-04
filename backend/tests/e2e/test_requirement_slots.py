"""需求槽位交互 e2e：BIRD-INTERACT 式的「GT + 用户模拟器 + 三度量」。

plan 2026-11-04-requirement-slot-contract Phase 4。默认 skip（需真 LLM + PG）：

    REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_requirement_slots.py -q -s

与既有 e2e 的区别：**不再盲选第一个选项**。用户模拟器只按夹具里的 ground truth 回答，
因此这三个度量才有意义：

- `priority_question_sr`：该问的槽位问到了、不该问的没问；
- `turns_used`（Efficiency）：把卡填到 complete 用了几次作答；
- `follow_up_sr` / `converged`：最终 SQL 的口径表达式是否收敛到 GT。
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import httpx
import pytest
import yaml

from evaluation.runner import _data_of, _login, _stream_sse
from evaluation.user_sim import GroundTruth, GroundTruthUser, accept_pending_assumptions, summarize

pytestmark = pytest.mark.e2e

BASE = "http://127.0.0.1:8100"
FIXTURE = Path(__file__).resolve().parents[3] / "evaluation" / "requirement_slots.yaml"


def _load_cases() -> list[dict]:
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8")) or {}
    return data.get("cases") or []


def _metric_expr(name: str) -> str | None:
    from app.knowledge import registry as knowledge

    metric = knowledge.resolve_metric(name)
    return metric.expr if metric else None


def _normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql or "").strip()


def _granularity_column(granularity: str) -> str | None:
    from app.knowledge import registry as knowledge

    return knowledge.time_bucket_column(granularity)


def _truth_from(raw: dict) -> GroundTruth:
    return GroundTruth(
        metric=raw.get("metric"),
        granularity=raw.get("granularity"),
        time_range=raw.get("time_range"),
        scope=tuple(raw.get("scope") or ()),
        analysis_intent=raw.get("analysis_intent"),
    )


def _settle_card(client, token, sid: str, card: dict, user: GroundTruthUser) -> tuple[dict, int]:
    """按模拟器逐槽回答直到 complete（或 patience 用尽）。返回 (最后一次卡, 作答次数)。"""
    turns = 0
    for _ in range(user.patience + 2):
        if card.get("status") == "complete" and not card.get("missing_fields"):
            break
        filled = user.answer_one(card)
        if filled is None:
            # 没有待问槽位、只剩没表态的假设 → 用户「就按你说的」接受
            filled = accept_pending_assumptions(card)
            if filled is None:
                break
        resp = client.patch(
            f"/api/v1/sessions/{sid}/requirement",
            json={"requirement": filled},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            break
        card = (resp.json() or {}).get("requirement") or filled
        turns += 1
    return card, turns


def _latest_sql(client, token, sid: str) -> tuple[str, dict]:
    headers = {"Authorization": f"Bearer {token}"}
    snap = client.get(f"/api/v1/sessions/{sid}", headers=headers).json()
    versions = (snap.get("session") or {}).get("report_versions") or []
    if not versions:
        return "", {}
    latest = max(v.get("version") or 0 for v in versions)
    report = client.get(f"/api/v1/sessions/{sid}/reports/{latest}", headers=headers).json()
    detail = report.get("report") or {}
    return (detail.get("query_snapshot") or {}).get("sql") or "", detail


def _run_case(client, token) -> list[dict]:
    results: list[dict] = []
    for case in _load_cases():
        sid = f"slot-e2e-{case['id'][:20]}-{uuid.uuid4().hex[:6]}"
        turns_spec = case.get("turns") or []
        pq = case.get("priority_questions") or {}
        user = GroundTruthUser(
            truth=_truth_from(turns_spec[-1].get("ground_truth") or {}),
            patience=int(case.get("patience") or 2),
            must_not_ask=tuple(pq.get("must_not_ask") or ()),
        )
        sql = ""
        turns_used = 0
        error = None
        for turn in turns_spec:
            events = list(_stream_sse(
                client, "POST", "/api/v1/chat", token,
                json_body={
                    "user_query": turn["query"],
                    "mode": turn.get("mode") or "new",
                    "session_id": sid,
                },
            ))
            err = _data_of(events, "error")
            if err:
                error = err
                break
            card = _data_of(events, "requirement") or {}
            user.observe_card(card)
            card, answers = _settle_card(client, token, sid, card, user)
            turns_used += answers
            cf = list(_stream_sse(client, "POST", f"/api/v1/sessions/{sid}/confirm", token, timeout=300.0))
            cerr = _data_of(cf, "error")
            if cerr:
                error = cerr
                break
            sql, _ = _latest_sql(client, token, sid)

        truth = user.truth
        expr = _metric_expr(truth.metric) if truth.metric else None
        sql_must = [str(tok) for tok in (turns_spec[-1].get("ground_truth") or {}).get("sql_must") or []]
        flat_sql = _normalize_sql(sql)
        if sql_must:
            # 夹具声明的语义 token（口径等价的写法差异不该判失败：净销售额的注册表
            # 表达式用 refund 子查询，模型常写等价的 JOIN 形式，两者都对）
            converged = all(tok.lower() in flat_sql.lower() for tok in sql_must)
        else:
            converged = bool(expr) and _normalize_sql(expr) in flat_sql
        if converged and truth.granularity:
            column = _granularity_column(truth.granularity)
            if column:
                converged = column.split(".")[-1].lower() in flat_sql.lower()
        asked = set(user.asked_slots)
        priority_ok = (
            all(slot in asked for slot in (pq.get("must_ask") or []))
            and not user.must_not_ask_violations
        )
        results.append({
            "id": case["id"],
            "converged": converged,
            "priority_ok": priority_ok,
            "follow_up_ok": converged,
            "turns_used": turns_used,
            "asked_slots": user.asked_slots,
            "must_not_ask_violations": user.must_not_ask_violations,
            "sql_metric_found": converged,
            "error": (error or {}).get("message") if error else None,
        })
        print(
            f"[slot-e2e] {case['id']:<32} converged={converged} priority_ok={priority_ok} "
            f"turns={turns_used} asked={user.asked_slots} err={(error or {}).get('code')}"
        )
    return results


def test_requirement_slot_interaction() -> None:
    cases = _load_cases()
    assert len(cases) >= 15, f"夹具应至少 15 条，实际 {len(cases)}"

    with httpx.Client(base_url=BASE, timeout=400.0) as client:
        token = _login(client)
        results = _run_case(client, token)

    summary = summarize(results)
    print(f"\n[slot-e2e] summary: {json.dumps(summary, ensure_ascii=False)}")

    failed = [r for r in results if not r["converged"]]
    assert not failed, f"未收敛到 GT 口径：{json.dumps(failed, ensure_ascii=False)}"
    assert summary["must_not_ask_violations"] == 0, (
        f"问了不该问的槽位：{[r for r in results if r['must_not_ask_violations']]}"
    )
    # PRQ：GT 要求必须问的槽位都要被问到（夹具里 must_ask 主要落在模糊口径用例）
    assert summary["priority_question_sr"] >= 0.9, summary
