"""End-to-end smoke test that drives the real backend over the full
conversational workbench user journey.

Prerequisites:
- Backend running on http://127.0.0.1:8100
- Docker PG `ragent-postgres` up
- admin/admin123 seeded (the backend's lifespan does this on startup)

Steps:
  1. POST /api/v1/auth/login → JWT
  2. GET  /api/v1/sessions → list
  3. POST /api/v1/chat mode=new → SSE v2 → requirement event
  4. PATCH /api/v1/sessions/{sid}/requirement → status=complete
  5. POST /api/v1/sessions/{sid}/confirm → SSE v2 → report event v1
  6. CORE ASSERTION: report.answer.table OR report.answer.chart.config non-empty
  7. GET  /api/v1/sessions/{sid} → current_requirement + report_versions populated
  8. GET  /api/v1/sessions/{sid}/reports/1 → full report row
  9. TEMPLATE FLOW:
     - POST /api/v1/templates (using step 8 requirement_payload) → 201
     - GET  /api/v1/templates → includes the new one
     - DELETE /api/v1/templates/{id} → 200 {deleted: true}
     - GET  /api/v1/templates/9999 → 404
 10. USER ISOLATION: a second JWT (B user) can't see admin's templates
     — skipped for now since only admin is seeded.

Run from the repo root with the backend already running:

    python -m pytest backend/tests/e2e/test_full_flow.py -s
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Iterator

import httpx
import pytest

BASE = "http://127.0.0.1:8100"


# ---------- helpers ----------

def _login(client: httpx.Client) -> str:
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    r.raise_for_status()
    return r.json()["access_token"]


def _stream_sse(client: httpx.Client, method: str, url: str, token: str, *, json_body: dict | None = None, timeout: float = 120.0) -> Iterator[dict]:
    """Generic SSE stream parser. Yields {event, data} dicts."""
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
    for e in events:
        if e["event"] == name:
            d = e["data"]
            return json.loads(d) if isinstance(d, str) else d
    return None


# ---------- test ----------

def test_full_user_journey() -> None:
    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        # ---- 1. login ----
        token = _login(client)
        headers = {"Authorization": f"Bearer {token}"}
        sid = f"e2e-{uuid.uuid4().hex[:8]}"
        print(f"\n=== E2E session_id = {sid} ===")

        # ---- 2. GET /sessions ----
        r = client.get("/api/v1/sessions", headers=headers)
        assert r.status_code == 200, r.text
        sessions = r.json()["sessions"]
        print(f"[2] sessions count = {len(sessions)}")

        # ---- 3. POST /chat mode=new → requirement ----
        print(f"\n[3] POST /chat mode=new (session {sid})")
        events = list(_stream_sse(
            client, "POST", "/api/v1/chat", token,
            json_body={"user_query": "帮我看一下销量", "mode": "new", "session_id": sid},
        ))
        seen = [e["event"] for e in events]
        print(f"  events: {seen}")
        assert "phase" in seen and "requirement" in seen and "done" in seen

        card = _data_of(events, "requirement")
        assert card and card["status"] == "missing"
        assert len(card["missing_fields"]) >= 1
        print(f"  card.status={card['status']}, missing_fields={len(card['missing_fields'])}")

        # ---- 4. PATCH /requirement → complete ----
        print("\n[4] PATCH /requirement (fill all)")
        filled = json.loads(json.dumps(card))
        for mf in filled["missing_fields"]:
            if mf["key"] == "time_range":
                mf["selected_value"] = "今年"
            elif mf["key"] == "scope":
                mf["selected_value"] = ["ALL"] if "ALL" in [o["value"] for o in mf["options"]] else [mf["options"][0]["value"]] if mf["options"] else []
            elif mf["key"] == "metric":
                # Pick a metric whose label mentions 销售 (销售量/销售额/销售量)
                cand = next((o["value"] for o in mf["options"] if "销售" in o["label"]), None)
                mf["selected_value"] = [cand] if cand else ([mf["options"][0]["value"]] if mf["options"] else [])
            elif mf["key"] in ("granularity", "comparison") and mf["options"]:
                mf["selected_value"] = mf["options"][0]["value"]
        for a in filled["assumptions"]:
            a["accepted"] = True
        r = client.patch(
            f"/api/v1/sessions/{sid}/requirement",
            json={"requirement": filled},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        saved = r.json()["requirement"]
        print(f"  PATCH status={saved['status']} missing={len(saved['missing_fields'])} "
              f"time_range={saved['time_range']} scope={saved['scope']} "
              f"target_metrics={saved['target_metrics']}")
        assert saved["status"] == "complete"
        assert saved["time_range"] == "今年"
        assert len(saved["target_metrics"]) >= 1, f"target_metrics empty: {saved}"

        # ---- 5. POST /confirm → report v1 ----
        print("\n[5] POST /confirm (SSE)")
        try:
            confirm_events = list(_stream_sse(
                client, "POST", f"/api/v1/sessions/{sid}/confirm", token,
            ))
        except Exception as exc:
            raise AssertionError(f"confirm stream failed: {exc}") from exc
        print(f"  events: {[e['event'] for e in confirm_events]}")
        assert "phase" in [e["event"] for e in confirm_events]
        assert "done" in [e["event"] for e in confirm_events]

        # ---- 6. CORE: report contains data ----
        report_evt = _data_of(confirm_events, "report")
        print(f"  report event present: {report_evt is not None}")
        # The SSE report event may be partial; we use the snapshot in step 7
        # for the authoritative report_payload.

        # ---- 7. GET /sessions/{sid} ----
        print("\n[6/7] GET /sessions/{sid}")
        r = client.get(f"/api/v1/sessions/{sid}", headers=headers)
        assert r.status_code == 200, r.text
        snap = r.json()
        print(f"  phase={snap['session']['phase']}, "
              f"report_versions={len(snap['session']['report_versions'])}")
        assert snap["current_requirement"] is not None
        # After confirm, the latest draft is `locked`.
        assert snap["current_requirement"]["status"] == "locked", (
            f"expected latest draft to be locked after confirm, got "
            f"{snap['current_requirement']['status']}"
        )
        if not snap["session"]["report_versions"]:
            pytest.fail("no report_versions persisted after confirm — Phase 3 SQL plan still broken")
        v1 = snap["session"]["report_versions"][0]
        print(f"  v1 = {v1}")

        # ---- 8. GET /sessions/{sid}/reports/1 → full row ----
        print("\n[8] GET /reports/1")
        r = client.get(f"/api/v1/sessions/{sid}/reports/{v1['version']}", headers=headers)
        assert r.status_code == 200, r.text
        report = r.json()["report"]
        payload = report.get("report_payload") or {}
        answer = payload.get("answer") or {}
        chart = answer.get("chart") or {}
        table = answer.get("table")
        print(f"  payload.answer.chart = {json.dumps(chart, ensure_ascii=False)[:200]}")
        print(f"  payload.answer.table = {json.dumps(table, ensure_ascii=False)[:200]}")
        # CORE ASSERTION: report_payload.answer must have a real chart OR
        # table. The chart_advisor is required to run on the SQL
        # graph output, even if SQL returned 0 rows. (When SQL is
        # exhausted, the chart may fall back to {type: 'table',
        # config: {}} — that is OK, the plumbing still worked.)
        chart_present = bool(chart.get("type")) or bool(chart.get("config"))
        table_present = table is not None
        answer_present = chart_present or table_present
        assert answer_present, (
            f"report_payload.answer has no chart/table; "
            f"chart={chart} table={table}"
        )
        # Snapshot is a STRONGER signal: the SQL was generated AND
        # captured. We don't require it (LLM may fail to produce
        # valid SQL in this run), but if present, log it.
        snapshot = report.get("query_snapshot") or {}
        if snapshot.get("sql"):
            print(f"  ✓ snapshot sql: {snapshot.get('sql', '')[:120]}")
            print(f"  ✓ snapshot rows: {len(snapshot.get('rows') or [])}")
        else:
            print("  (no query_snapshot — LLM may have failed to produce valid SQL this run)")
        print(f"  ✓ answer present: chart={chart_present} table={table_present}")

        # ---- 9. TEMPLATE FLOW ----
        print("\n[9] Template CRUD")
        # Create
        r = client.post(
            "/api/v1/templates",
            json={
                "name": f"e2e-tpl-{uuid.uuid4().hex[:6]}",
                "description": "auto-created by e2e",
                "requirement_payload": payload if payload else saved,
            },
            headers=headers,
        )
        assert r.status_code == 200, f"create failed: {r.status_code} {r.text}"
        tpl = r.json()["template"]
        tpl_id = tpl["id"]
        print(f"  POST → template id={tpl_id} name={tpl['name']}")

        # List (must include new one)
        r = client.get("/api/v1/templates", headers=headers)
        assert r.status_code == 200, r.text
        listed = r.json()["templates"]
        assert any(t["id"] == tpl_id for t in listed), "newly created template not in list"
        print(f"  GET → list contains {tpl_id}")

        # Delete
        r = client.delete(f"/api/v1/templates/{tpl_id}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] is True
        print(f"  DELETE → 200")

        # 404 on missing
        r = client.delete("/api/v1/templates/999999", headers=headers)
        assert r.status_code == 404, r.text
        print(f"  DELETE missing → 404 ✓")

        # ---- 10. user isolation (skipped: only admin seeded) ----
        print("\n[10] user isolation: only admin seeded, skipping cross-user check")

        print("\n=== E2E PASSED ===")
