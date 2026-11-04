"""后端链路矩阵 e2e：寻常问法从头跑到尾（需求 → 澄清 → 确认 → SQL → 行数）。

plan 2026-11-04-top-period-detail-intent（配套 harness）。

为什么需要：这一天的缺陷都是「单点探针」发现的（先看 SQL、再看卡片），
而用户的原话是**整条链路**：一句寻常中文能不能落成可执行的需求、SQL 形状对不对、
结果行数/列数像不像话。矩阵化之后每次改动都能一次跑完，不再靠零散手测。

跑法（需要真 LLM + PG + backend :8100）：

    REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_chain_matrix.py -q -s

夹具：`evaluation/chain_matrix.yaml`。断言只写**意图层**（口径/时间/粒度/明细/选期/
SQL 片段/行数/列数），不钉文案。
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

pytestmark = pytest.mark.e2e

BASE = "http://127.0.0.1:8100"
FIXTURE = Path(__file__).resolve().parents[3] / "evaluation" / "chain_matrix.yaml"


def _load_cases() -> list[dict]:
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8")) or {}
    return data.get("cases") or []


def _flat(sql: str) -> str:
    return re.sub(r"\s+", " ", (sql or "")).strip().lower()


def _pick_option(field: dict, expect: dict) -> str | None:
    """按 GT 优先选选项：口径选期望指标，范围/时间选期望子串，否则第一项。

    之前的探针「盲选第一个选项」，于是口径被选成实收金额、范围被选错，
    断言就会莫名其妙地红——用户模拟必须受 ground truth 约束。
    """
    options = field.get("options") or []
    if not options:
        return None
    want_metric = str(expect.get("metric") or "")
    want_scope = str(expect.get("scope_has") or "")
    want_time = str(expect.get("time_range_has") or "")
    key = str(field.get("key") or "")
    for option in options:
        label = str(option.get("label") or "")
        value = str(option.get("value") or "")
        if key == "metric" and want_metric and want_metric in label:
            return value
        if key == "scope" and want_scope and want_scope in label:
            return value
        if key == "time_range" and want_time and want_time in label:
            return value
    return str(options[0].get("value"))


def _settle(client, token: str, sid: str, card: dict, expect: dict, patience: int) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    current = card
    for _ in range(patience + 2):
        if not (current.get("missing_fields") or []) and current.get("status") != "missing":
            break
        changed = False
        for field in current.get("missing_fields") or []:
            value = _pick_option(field, expect)
            if value is not None:
                field["selected_value"] = value
                changed = True
        for assumption in current.get("assumptions") or []:
            if assumption.get("accepted") is None:
                assumption["accepted"] = True
                changed = True
        if not changed:
            break
        resp = client.patch(
            f"/api/v1/sessions/{sid}/requirement",
            json={"requirement": current}, headers=headers,
        )
        if resp.status_code != 200:
            break
        current = (resp.json() or {}).get("requirement") or current
    return current


def _latest_version(client, token: str, sid: str) -> tuple[int | None, dict]:
    headers = {"Authorization": f"Bearer {token}"}
    snap = client.get(f"/api/v1/sessions/{sid}", headers=headers).json()
    versions = (snap.get("session") or {}).get("report_versions") or []
    if not versions:
        return None, {}
    latest = max(int(v.get("version") or 0) for v in versions)
    report = client.get(f"/api/v1/sessions/{sid}/reports/{latest}", headers=headers).json()
    return latest, report


def _run_case(client, token: str, case: dict) -> dict:
    expect = case.get("expect") or {}
    patience = int(case.get("patience") or 3)
    sid = f"matrix-{case['id'][:24]}-{uuid.uuid4().hex[:6]}"
    card: dict = {}
    error = None
    failed_turn = None

    for turn in case.get("turns") or []:
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
            failed_turn = turn["query"]
            break
        card = _data_of(events, "requirement") or {}
        card = _settle(client, token, sid, card, expect, patience)
        confirm_events = list(_stream_sse(
            client, "POST", f"/api/v1/sessions/{sid}/confirm", token, timeout=400.0,
        ))
        cerr = _data_of(confirm_events, "error")
        if cerr:
            error = cerr
            failed_turn = turn["query"]
            break

    latest, report = _latest_version(client, token, sid) if not error else (None, {})
    snapshot = ((report.get("report") or {}).get("query_snapshot") or {}) if report else {}
    sql = _flat(snapshot.get("sql"))
    rows = snapshot.get("rows") or []
    columns = snapshot.get("columns") or []

    problems: list[str] = []

    def _check(condition: bool, label: str) -> None:
        if not condition:
            problems.append(label)

    if error:
        problems.append(f"SSE error: {error.get('code')}/{str(error.get('message'))[:60]} @ {failed_turn}")
    else:
        _check(latest is not None, "没有产出报告版本")

    # --- 需求卡 ---
    if expect.get("metric"):
        _check(str(expect["metric"]) in (card.get("target_metrics") or []),
               f"口径={card.get('target_metrics')} 不含 {expect['metric']}")
    if expect.get("time_range_has"):
        _check(str(expect["time_range_has"]) in str(card.get("time_range") or ""),
               f"time_range={card.get('time_range')}")
    if expect.get("scope_has"):
        _check(str(expect["scope_has"]) in [str(s) for s in (card.get("scope") or [])],
               f"scope={card.get('scope')}")
    if expect.get("granularity"):
        _check(card.get("granularity") == expect["granularity"],
               f"granularity={card.get('granularity')} 期望 {expect['granularity']}")
    if expect.get("detail_level"):
        _check(card.get("detail_level") == expect["detail_level"],
               f"detail_level={card.get('detail_level')}")
    top = expect.get("top_period") or {}
    if top:
        got = card.get("top_period") or {}
        _check(got.get("direction") == top.get("direction") and got.get("unit") == top.get("unit"),
               f"top_period={got}")
    for slot in expect.get("no_slots") or []:
        _check(slot not in {mf.get("key") for mf in (card.get("missing_fields") or [])},
               f"仍在追问 {slot}")

    # --- SQL / 结果 ---
    for token_must in expect.get("sql_must") or []:
        _check(_flat(token_must) in sql, f"SQL 缺 {token_must}")
    for token_bad in expect.get("sql_must_not") or []:
        _check(_flat(token_bad) not in sql, f"SQL 不该有 {token_bad}")
    if expect.get("rows_min") is not None:
        _check(len(rows) >= int(expect["rows_min"]), f"行数 {len(rows)} < {expect['rows_min']}")
    if expect.get("rows_max") is not None:
        _check(len(rows) <= int(expect["rows_max"]), f"行数 {len(rows)} > {expect['rows_max']}")
    if expect.get("min_columns") is not None:
        _check(len(columns) >= int(expect["min_columns"]),
               f"列数 {len(columns)} < {expect['min_columns']}")

    return {
        "id": case["id"],
        "ok": not problems,
        "problems": problems,
        "sql": sql,
        "rows": len(rows),
        "columns": [str(c.get("name")) for c in columns],
        "card_metrics": card.get("target_metrics"),
        "session": sid,
    }


def test_chain_matrix() -> None:
    cases = _load_cases()
    assert cases, "夹具为空"

    results = []
    with httpx.Client(base_url=BASE, timeout=600.0) as client:
        token = _login(client)
        for case in cases:
            result = _run_case(client, token, case)
            results.append(result)
            status = "PASS" if result["ok"] else "FAIL"
            print(
                f"[matrix] {status} {case['id']:<24} rows={result['rows']:<4} "
                f"cols={len(result['columns'])} metrics={result['card_metrics']}"
            )
            if not result["ok"]:
                for problem in result["problems"]:
                    print(f"          · {problem}")
                print(f"          sql: {result['sql'][:400]}")

    passed = sum(1 for r in results if r["ok"])
    print(f"\n[matrix] summary: {passed}/{len(results)} passed")
    assert passed == len(results), json.dumps(
        [r for r in results if not r["ok"]], ensure_ascii=False, indent=2,
    )
