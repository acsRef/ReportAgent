"""P0 baseline runner —— 驱动真实 API 跑 Golden Set（baseline-lock plan T4/T5）。

用法（需 PG + MCP + backend :8100 + LLM key，与 REPORTAGENT_E2E 同门）：

    python -m evaluation.runner \
        --base-url http://127.0.0.1:8100 \
        --out  evaluation/results/baseline-2026-08-25.json \
        --md   evaluation/results/baseline-2026-08-25.md

驱动模式拷贝自 backend/tests/e2e/test_full_flow.py 的 httpx SSE 解析
（evaluation/ 终将成为独立 harness，不反向 import backend 测试目录）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import httpx

from evaluation.checker import ObservedTurn, check_turn, summarize
from evaluation.loader import load_all
from evaluation.schema import BaselineCase

# P14：模块级显式 import 9 子包，确保 DIM_REGISTRY 在 CLI / pytest / 单脚本等不同入口
# 都注册完整（test_subpackage_layout 在 pytest 路径下帮过一次，但 CLI 直跑不保证）。
from evaluation import (  # noqa: E402, F401  —— register_dim side-effect
    e2e,
    frontend,
    memory,
    repair,
    report,
    requirement,
    retrieval,
    sql,
    tool_selection,
)

DEFAULT_DATASET = Path(__file__).resolve().parent / "baseline_cases.json"


# ---------- SSE 驱动（模式来自 e2e/test_full_flow.py，自包含拷贝） ----------

def _login(client: httpx.Client) -> str:
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _stream_sse(
    client: httpx.Client, method: str, url: str, token: str, *,
    json_body: dict | None = None, timeout: float = 180.0,
) -> Iterator[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    with client.stream(method, url, json=json_body, headers=headers, timeout=timeout) as resp:
        resp.raise_for_status()
        ev_name = None
        data_buf: list[str] = []
        for line in resp.iter_lines():
            if line == "":
                if ev_name and data_buf:
                    data_str = "\n".join(data_buf)
                    try:
                        parsed = json.loads(data_str)
                    except Exception:
                        parsed = data_str
                    yield {"event": ev_name, "data": parsed}
                ev_name = None
                data_buf = []
                continue
            if line.startswith("event:"):
                ev_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_buf.append(line[len("data:"):].strip())


def _data_of(events: list[dict], name: str) -> Any:
    """返回**最后一个**同名事件 —— latest-state-wins。

    runner 会在 PATCH 后把权威卡追加为新的 requirement 事件；
    判定必须读最新状态，而不是链路上第一帧。
    """
    found = None
    for e in events:
        if e["event"] == name:
            d = e["data"]
            found = json.loads(d) if isinstance(d, str) else d
    return found


# ---------- 单 case 执行 ----------

def _fill_missing_fields(card: dict) -> dict:
    """e2e fill-all 策略：把卡补成 complete（时间用 2024年 保种子数据命中）。

    无条件执行：填 selected_value、接受全部 assumptions。即使 missing_fields
    为空也要 PATCH —— LLM 可能产出「missing_fields=[] 但 status=missing」
    的卡（服务端按表单态重算，未接受的 assumption 会压住 status）。
    """
    filled = json.loads(json.dumps(card))
    for mf in filled.get("missing_fields", []):
        key = mf.get("key")
        options = mf.get("options") or []
        values = [o["value"] for o in options]
        if key == "time_range":
            mf["selected_value"] = "2024年" if "2024年" in values else (
                values[0] if values else "2024年"
            )
        elif key == "scope":
            mf["selected_value"] = ["ALL"] if "ALL" in values else (values or [])
        elif key == "metric":
            cand = next((v for v, o in zip(values, options) if "销售" in o.get("label", "")), None)
            mf["selected_value"] = ([cand] if cand else [values[0]]) if values else []
        elif key in ("granularity", "comparison") and values:
            mf["selected_value"] = values[0]
        elif values:
            mf["selected_value"] = values[0]
    for a in filled.get("assumptions", []):
        a["accepted"] = True
    return filled


def _observe_turn(events: list[dict], sid: str, client: httpx.Client,
                  token: str, executed: bool) -> tuple[ObservedTurn, dict | None]:
    """从 SSE 事件组装观测；executed 时再拉报告快照。返回 (observed, report_detail)。"""
    names = [e["event"] for e in events]
    card = _data_of(events, "requirement") or {}
    err = _data_of(events, "error") or {}

    obs = ObservedTurn(
        sse_events=names,
        card_status=card.get("status"),
        missing_fields_count=len(card.get("missing_fields") or []),
        target_metrics=card.get("target_metrics") or [],
        time_range=card.get("time_range"),
        scope=card.get("scope") or [],
        dimensions=card.get("dimensions") or [],
        error_code=(err.get("code") if isinstance(err, dict) else None),
    )

    detail = None
    if executed:
        r = client.get(f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            snap = r.json().get("session") or {}
            versions = snap.get("report_versions") or []
            if versions:
                v1 = versions[0].get("version")
                rr = client.get(
                    f"/api/v1/sessions/{sid}/reports/{v1}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if rr.status_code == 200:
                    detail = (rr.json() or {}).get("report") or {}
        if detail:
            snapshot = detail.get("query_snapshot") or {}
            answer = (detail.get("report_payload") or {}).get("answer") or {}
            table = answer.get("table") or {}
            obs.sql = snapshot.get("sql")
            obs.row_count = len(snapshot.get("rows") or [])
            obs.table_present = bool(table and table.get("columns"))
            obs.table_rows = len(table.get("rows") or [])
            chart = answer.get("chart") or {}
            obs.chart_present = bool(chart) and chart.get("type") not in (None, "", "table")
    return obs, detail


def run_case(case: BaselineCase, client: httpx.Client, token: str) -> dict[str, Any]:
    if case.requires_fault_injection:
        return {
            "case_id": case.id, "category": case.category, "status": "skip",
            "reason": "requires_fault_injection", "sections": {}, "deferred": [],
            "sql_executed": False, "latency_ms": None,
        }

    sid = f"eval-{case.id[:24]}-{uuid.uuid4().hex[:8]}"
    sections_all: dict[str, str] = {}
    deferred_all: list[str] = []
    sql_executed = any(
        (e.execution and e.execution.verdict is not None) or (e.report and (e.report.table_present or e.report.chart_present))
        for e in case.expectations
    )
    last_obs: ObservedTurn | None = None

    t0 = time.monotonic()
    try:
        for i, turn in enumerate(case.turns):
            events = list(_stream_sse(
                client, "POST", "/api/v1/chat", token,
                json_body={
                    "user_query": turn.query, "mode": turn.mode, "session_id": sid,
                },
            ))
            exp_idx = i if len(case.expectations) == len(case.turns) else len(case.expectations) - 1
            exp = case.expectations[exp_idx]
            needs_exec = bool(sql_executed and exp and (
                (exp.execution and exp.execution.verdict is not None)
                or exp.report is not None
            ))

            card = _data_of(events, "requirement")
            # 有执行/报告期望的案例：无条件 fill-all + accept-all 后 PATCH
            # （幂等；即使 missing_fields=[] 也可能因未接受的 assumptions 而
            # status=missing —— 服务端会按表单态重算 status）。
            if card and needs_exec:
                filled = _fill_missing_fields(card)
                pr = client.patch(
                    f"/api/v1/sessions/{sid}/requirement",
                    json={"requirement": filled},
                    headers={"Authorization": f"Bearer {token}"},
                )
                saved_card = pr.json().get("requirement", filled) if pr.status_code == 200 else filled
                # 用 PATCH 后的权威卡更新 requirement 观测。
                events = events + [{
                    "event": "requirement",
                    "data": json.dumps(saved_card, ensure_ascii=False),
                }]
                cf_events = list(_stream_sse(
                    client, "POST", f"/api/v1/sessions/{sid}/confirm", token,
                ))
                events = events + cf_events

            obs, _detail = _observe_turn(
                events, sid, client, token, executed=needs_exec,
            )
            last_obs = obs
            # check_turn 吃 dict；TurnExpectation 是 Pydantic 模型。
            exp_dict = exp.model_dump() if hasattr(exp, "model_dump") else (exp or {})
            sections, deferred = check_turn(obs, exp_dict)
            sections_all.update(sections)
            deferred_all.extend(deferred)

        latency_ms = (time.monotonic() - t0) * 1000.0
        status = "fail" if any(v.startswith("fail") for v in sections_all.values()) else "pass"

        # P14：dim_results = DIM_REGISTRY 9 + LEGACY 4（{requirement, report} 重叠，set 去重）
        from evaluation.checker import DIM_REGISTRY, build_dim_results
        all_dims = list(DIM_REGISTRY.keys()) + ["requirement", "execution", "report", "behavior"]
        seen: set[str] = set()
        unique_dims: list[str] = []
        for d in all_dims:
            if d not in seen:
                seen.add(d)
                unique_dims.append(d)
        dim_results = build_dim_results(
            sections_all,
            sorted(set(deferred_all)),
            unique_dims,
        )

        return {
            "case_id": case.id, "category": case.category, "status": status,
            "sections": sections_all, "deferred": sorted(set(deferred_all)),
            "dim_results": dim_results,  # P14 新增字段
            "sql_executed": sql_executed,
            "latency_ms": round(latency_ms, 1),
        }
    except Exception as exc:  # 单 case 异常不中断整批
        return {
            "case_id": case.id, "category": case.category, "status": "error",
            "reason": f"{type(exc).__name__}: {exc}",
            "sections": sections_all, "deferred": sorted(set(deferred_all)),
            "dim_results": {},
            "sql_executed": sql_executed,
            "latency_ms": round((time.monotonic() - t0) * 1000.0, 1),
        }


# ---------- Markdown 渲染 ----------

def render_markdown(results: list[dict], summary: dict | None) -> str:
    lines = [
        "# Baseline Run",
        "",
        "| case_id | category | status | fail/skip 明细 | deferred | latency_ms |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        notes = []
        fails = {k: v for k, v in (r.get("sections") or {}).items() if v != "pass"}
        if fails:
            notes.append(json.dumps(fails, ensure_ascii=False))
        if r.get("reason"):
            notes.append(str(r["reason"]))
        lines.append(
            f"| {r['case_id']} | {r['category']} | {r['status']} "
            f"| {'; '.join(notes)} | {', '.join(r.get('deferred') or [])} "
            f"| {r.get('latency_ms') if r.get('latency_ms') is not None else ''} |"
        )
    if summary:
        lines += ["", "## Summary", ""]
        for k, v in summary.items():
            lines.append(f"- **{k}**: {v}")
    return "\n".join(lines) + "\n"


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ReportAgent P0 baseline runner")
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--base-url", default="http://127.0.0.1:8100")
    ap.add_argument("--out", default=None, help="JSON 结果路径")
    ap.add_argument("--md", default=None, help="Markdown 报告路径")
    ap.add_argument("--only-category", default=None)
    ap.add_argument("--list", action="store_true", help="只列出案例不执行")
    args = ap.parse_args(argv)

    cases = load_all(args.dataset)
    if args.only_category:
        cases = [c for c in cases if c.category == args.only_category]

    if args.list:
        for c in cases:
            print(f"{c.id:40s} {c.category:20s} fi={c.requires_fault_injection}")
        return 0

    base = args.base_url.rstrip("/")
    with httpx.Client(base_url=base, timeout=30.0) as probe:
        health_ok = False
        try:
            health_ok = probe.get("/health").status_code == 200
        except Exception:
            pass
    if not health_ok:
        print(f"backend {base} 不可达（/health 失败）。需要 PG + MCP + backend 在跑，"
              f"同 REPORTAGENT_E2E 前提。", file=sys.stderr)
        return 2

    results: list[dict] = []
    with httpx.Client(base_url=base, timeout=30.0) as client:
        token = _login(client)
        for n, case in enumerate(cases, 1):
            print(f"[{n}/{len(cases)}] {case.id} ... ", flush=True)
            result = run_case(case, client, token)
            print(f"  -> {result['status']}", flush=True)
            results.append(result)

    summary = summarize(results)
    payload = {"results": results, "summary": summary}

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"wrote {args.out}")
    md = render_markdown(results, summary)
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(md, encoding="utf-8")
        print(f"wrote {args.md}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
