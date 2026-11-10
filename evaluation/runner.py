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
from evaluation.execution_accuracy import compare_result_sets
from evaluation.loader import load_all
from evaluation.schema import BaselineCase
from evaluation.task_decomposition import (
    complexity_tier,
    diagnose,
    subtask_failure_counts,
    tier_accuracy,
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
    extra_headers: dict[str, str] | None = None,
) -> Iterator[dict]:
    headers = {"Authorization": f"Bearer {token}", **(extra_headers or {})}
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

def _pick_metric_selection(
    values: list[str],
    options: list[dict],
    user_query: str = "",
    expected_metrics: tuple[str, ...] = (),
) -> list[str]:
    """选口径：**原话里逐字出现过**的优先，其次才回退第一个。

    2026-11-10（plan time-range-and-metric-slot-backfill D4）：原规则是「label 里含
    『销售』的第一个」——`净销售额` 也含「销售」且排在前面，于是把用户明说的「销售额」
    换成了净额。夹具替被测系统做决定，算出来的数字就再也不说明问题。
    """
    if not values:
        return []
    labels = [str(option.get("label") or "") for option in options]

    def _match(name: str) -> str | None:
        text = str(name or "").strip()
        if not text:
            return None
        for value, label in zip(values, labels):
            if label == text:
                return value
        for value, label in zip(values, labels):
            if text in label or label in text:
                return value
        return None

    for name in expected_metrics:
        hit = _match(name)
        if hit:
            return [hit]
    haystack = str(user_query or "")
    # 原话里逐字出现的口径，最长优先（否则「销售额」会抢在「净销售额」前面）
    best: tuple[int, str] | None = None
    for value, label in zip(values, labels):
        if label and label in haystack and (best is None or len(label) > best[0]):
            best = (len(label), value)
    if best:
        return [best[1]]
    return [values[0]]


def _fill_missing_fields(
    card: dict,
    *,
    user_query: str = "",
    expected_metrics: tuple[str, ...] = (),
) -> dict:
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
            mf["selected_value"] = _pick_metric_selection(
                values, options, user_query, expected_metrics,
            )
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
                # P14 P1 闭环：取 max(version) 而非 versions[0]——多版本 case
                # (adjust / 重新生成) 的真正最新报告，否则 observation stale
                # → dim_results 全错。
                latest_v = max(
                    (v.get("version", 0) for v in versions if isinstance(v.get("version"), int)),
                    default=None,
                )
                if latest_v is None:
                    # 兼容历史数据：version 字段不是 int，回退 index 0
                    latest_v = versions[0].get("version")
                if latest_v is not None:
                    rr = client.get(
                        f"/api/v1/sessions/{sid}/reports/{latest_v}",
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
            # Execution Accuracy 用：把真实结果行留下（截断到 500 行，评测不需要全量）。
            # 两个坑（2026-11-10 首次接入就踩到）：
            #   ① `query_snapshot.columns` 是 `[{name, type}]`，不是字符串列表；
            #   ② `query_snapshot.rows` 是 **dict 列表**（每行 {列名: 值}），直接 list()
            #      只会拿到列名——把「表头」当成一行数据。
            raw_cols = snapshot.get("columns") or []
            obs.columns = [
                c.get("name") if isinstance(c, dict) else str(c) for c in raw_cols
            ]
            raw_rows = (snapshot.get("rows") or [])[:500]
            obs.rows = [
                [row.get(c) for c in obs.columns] if isinstance(row, dict) else list(row)
                for row in raw_rows
            ]
            obs.table_present = bool(table and table.get("columns"))
            obs.table_rows = len(table.get("rows") or [])
            chart = answer.get("chart") or {}
            obs.chart_present = bool(chart) and chart.get("type") not in (None, "", "table")
    return obs, detail


def _compute_dim_results(
    sections_all: dict[str, str], deferred_all: list[str]
) -> dict[str, dict[str, int]]:
    """P14：run_case 内部 helper —— 算 11-slot dim_results dict。

    输入：sections_all（merged legacy + dispatcher）+ deferred_all。
    输出：{dim: {pass, fail, deferred}}，dim 集合 = 9 DIM_REGISTRY ∪ 4 LEGACY_KEYS = 11
    （requirement/report 在 DIM_REGISTRY 与 LEGACY_KEYS 都出现，set 去重）。

    关键点：异常路径也调这个函数，确保 dim_results shape 与正常路径一致
    （P14 P3 闭环：regression evaluator 不处理两套格式）。
    """
    from evaluation.checker import DIM_REGISTRY, build_dim_results

    all_dims = list(DIM_REGISTRY.keys()) + ["requirement", "execution", "report", "behavior"]
    seen: set[str] = set()
    unique_dims: list[str] = []
    for d in all_dims:
        if d not in seen:
            seen.add(d)
            unique_dims.append(d)
    return build_dim_results(
        sections_all,
        sorted(set(deferred_all)),
        unique_dims,
    )


def _ensure_backend_on_path() -> None:
    """`python -m evaluation.runner` 从 repo 根跑时，`app.*` 还不在 sys.path 上。

    金标 SQL 要用生产同一套只读执行器（`app.tools.sql_tools.execute_sql`）跑，
    所以这里把 `backend/` 加进来——和 sql_tools 的只读角色/安全闸保持一致，
    不另开一条数据库访问路径。
    """
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1] / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    # 金标 SQL 走 `execute_sql` → 需要 ANALYSIS_DSN（只读角色）。runner 是独立脚本，
    # 不会自动读 repo 根 .env，这里按其它评测脚本同样的方式补载（不覆盖已设变量）。
    root_env = Path(__file__).resolve().parents[1] / ".env"
    if root_env.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(root_env, override=False)
        except ImportError:  # pragma: no cover
            pass


def _resolve_golden(golden) -> tuple[list[str], list[list]] | None:
    """金标结果：优先用 `golden.sql` 现场跑一遍（跟着种子数据走），否则用内联行。"""
    if golden is None:
        return None
    if getattr(golden, "sql", None):
        _ensure_backend_on_path()
        from app.tools.sql_tools import execute_sql

        payload = json.loads(execute_sql(golden.sql))
        if payload.get("error"):
            raise RuntimeError(f"golden sql failed: {payload['error']}")
        cols = [c.get("name") for c in payload.get("columns") or []]
        rows = [[row.get(c) for c in cols] for row in payload.get("rows") or []]
        return list(cols), rows
    if getattr(golden, "rows", None):
        return list(golden.columns or []), [list(r) for r in golden.rows]
    return None


def run_case(case: BaselineCase, client: httpx.Client, token: str) -> dict[str, Any]:
    # 2026-11-02：只有「声明了故障却没有注入规格」才 skip——这类 case 是
    # 真 deferred（例如 MCP_TIMEOUT 没有注入形态）。有 `fault` 规格的一律真跑。
    if case.requires_fault_injection and case.fault is None:
        return {
            "case_id": case.id, "category": case.category, "status": "skip",
            "reason": "requires_fault_injection_without_spec", "sections": {}, "deferred": [],
            "dim_results": _compute_dim_results({}, []),  # 11-slot 全 0
            "sql_executed": False, "latency_ms": None,
        }
    fault = case.fault

    sid = f"eval-{case.id[:24]}-{uuid.uuid4().hex[:8]}"
    sections_all: dict[str, str] = {}
    deferred_all: list[str] = []
    sql_executed = any(
        (e.execution and e.execution.verdict is not None) or (e.report and (e.report.table_present or e.report.chart_present))
        for e in case.expectations
    )
    last_obs: ObservedTurn | None = None
    match = None  # Execution Accuracy：本轮结果与金标的比对结论（无金标则 None）
    complexity_tier_value: int | None = None
    diagnosis_value: dict[str, Any] | None = None
    golden_sql_value: str | None = None

    t0 = time.monotonic()
    try:
        for i, turn in enumerate(case.turns):
            events = list(_stream_sse(
                client, "POST", "/api/v1/chat", token,
                json_body={
                    "user_query": turn.query, "mode": turn.mode, "session_id": sid,
                },
                extra_headers=fault.headers_for("chat") if fault else None,
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
                filled = _fill_missing_fields(
                    card,
                    user_query=turn.query,
                    expected_metrics=tuple(
                        getattr(getattr(exp, "requirement", None), "target_metrics_contains", ()) or ()
                    ),
                )
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
                    extra_headers=fault.headers_for("confirm") if fault else None,
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

            # Execution Accuracy（2026-11-10）：有金标就比**结果**，与「跑没跑通」分开记。
            # 这是唯一能闭环的判据（BEAVER 的立场）：检索/召回只是上游手段。
            golden = exp.execution.golden if (exp and exp.execution) else None
            if golden is not None:
                gold_sql = str(getattr(golden, "sql", "") or "")
                if gold_sql:
                    golden_sql_value = gold_sql
                    # 子任务诊断 + 复杂度分档（只用金标结构推导，可复现）：
                    # 「结果不一致」拆成表/join 键/列/谓词/结构，才知道该修哪一层。
                    tier = complexity_tier(gold_sql)
                    if tier is not None:
                        complexity_tier_value = tier
                    diagnosis = diagnose(gold_sql, str(obs.sql or ""))
                    if diagnosis is not None:
                        diagnosis_value = diagnosis.as_dict()
                try:
                    resolved = _resolve_golden(golden)
                    if resolved is not None:
                        gold_cols, gold_rows = resolved
                        match = compare_result_sets(
                            gold_cols, gold_rows, obs.columns, obs.rows,
                            float_tol=golden.float_tol,
                            match_columns=golden.match_columns,
                        )
                        sections_all["execution.result_matches"] = (
                            "pass" if match.ok else f"fail:{match.reason}"
                        )
                except Exception as exc:  # noqa: BLE001 - 金标自身出错不该崩掉整批评测
                    sections_all["execution.result_matches"] = (
                        f"error:{type(exc).__name__}:{str(exc)[:120]}"
                    )

        latency_ms = (time.monotonic() - t0) * 1000.0
        status = "fail" if any(v.startswith("fail") for v in sections_all.values()) else "pass"

        return {
            "case_id": case.id, "category": case.category, "status": status,
            "sections": sections_all, "deferred": sorted(set(deferred_all)),
            "dim_results": _compute_dim_results(sections_all, deferred_all),  # P14 新增字段
            "sql_executed": sql_executed,
            "latency_ms": round(latency_ms, 1),
            # Execution Accuracy（结果对不对）——与 status（跑没跑通）是两回事
            "result_matches": (match.ok if match is not None else None),
            "execution_match": (match.as_dict() if match is not None else None),
            # 子任务诊断 + 复杂度分档：结果不对时回答「差在哪一步、这条有多难」
            "complexity_tier": complexity_tier_value,
            "diagnosis": diagnosis_value,
            # 两条 SQL 一起落盘：结果产物要能自证（否则「为什么判错」只能回数据库翻）
            "sql": (obs.sql or None),
            "golden_sql": golden_sql_value,
        }
    except Exception as exc:  # 单 case 异常不中断整批
        return {
            "case_id": case.id, "category": case.category, "status": "error",
            "reason": f"{type(exc).__name__}: {exc}",
            "sections": sections_all, "deferred": sorted(set(deferred_all)),
            "dim_results": _compute_dim_results(sections_all, deferred_all),
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
    ap.add_argument("--only-id", default=None, help="只跑指定 case id（迭代单例用）")
    ap.add_argument("--list", action="store_true", help="只列出案例不执行")
    args = ap.parse_args(argv)

    cases = load_all(args.dataset)
    if args.only_category:
        cases = [c for c in cases if c.category == args.only_category]
    if args.only_id:
        cases = [c for c in cases if c.id == args.only_id]

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
    # Execution Accuracy 作为**头号指标**单独记：只统计有金标的 case（其余为“未标注”，
    # 不能算失败也不能算通过）。与 sql_success_rate（跑通率）并列展示，避免混淆。
    scored = [r for r in results if r.get("result_matches") is not None]
    matched = sum(1 for r in scored if r["result_matches"])
    summary["execution_accuracy"] = (
        round(matched / len(scored), 4) if scored else None
    )
    summary["cases_with_golden"] = len(scored)
    summary["cases_matched"] = matched
    fail_reasons: dict[str, int] = {}
    for r in scored:
        if not r["result_matches"]:
            reason = (r.get("execution_match") or {}).get("reason", "unknown")
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
    summary["execution_failure_reasons"] = fail_reasons
    # 按难度切片 + 失败归因到子任务（BEAVER 的拆法）：只回答「哪一层错」与「越难是不是越差」，
    # 不参与 pass/fail 判定，避免把诊断当成绩。
    summary["complexity_tiers"] = tier_accuracy(results)
    summary["subtask_failures"] = subtask_failure_counts(results)
    payload = {"results": results, "summary": summary}

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
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
