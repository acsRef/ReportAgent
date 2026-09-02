"""Real RAG MCP + PostgreSQL e2e —— P15 正式用例（带真断言，替代旧 mock/probe）。

P15 e2e：用户拍板这些边界 case 当正式测试（几百个旧单测不算数）。6 场景：

1. happy explicit        "2024年各区域销售额排名"                 → SUCCESS + 真表断言
2. repair（确定性 seam）   "2024年华东销售额" + X-E2E-Fault once
                           kind=object_not_found                  → retry 真恢复 SUCCESS
3. fail（永久 fault seam） "2024年华东销售额" + X-E2E-Fault
                           permission persistent                   → 无 SUCCESS，不伪造成功
4. 点名对象澄清             "查询 unicorn_data 表的所有数据"         → NOT complete、澄清 surface
5. schema_retrieval       "订单相关的数据都在哪些表里？"            → 结构化命中 fact_orders/fact_payments
6. multi_turn supplement  轮1 new → 轮2 supplement"再看月度趋势"    → 继承 time_range=2024年

前置（backend 需以 REPORTAGENT_E2E=1 启动，fault seam 才激活；本文件亦以此 gate）：
1. PG + 零售订单 seed（seed_business_p15prelude.sql：fact_orders/fact_payments/dim_*，全 2024）
2. ragent-py MCP（KB 7 张业务表已按新格式 ingest）
3. ReportAgent backend :8100（REPORTAGENT_E2E=1 + LLM key）
4. 本文件 pytest 运行亦需 REPORTAGENT_E2E=1（与 backend 同 gate）

执行（repo root）：
    REPORTAGENT_E2E=1 D:/miniConda/envs/agent/python.exe -m pytest \\
        evaluation/tests/test_real_rag_mcp_e2e.py -v
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Iterator

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("REPORTAGENT_E2E"),
    reason="REPORTAGENT_E2E not set; skipping real backend e2e test",
)

BASE_URL = os.getenv("REPORTAGENT_E2E_BASE_URL", "http://127.0.0.1:8100")


def _login(client: httpx.Client) -> str:
    r = client.post(
        "/api/v1/auth/login",
        json={"username": os.getenv("DEFAULT_USERNAME", "admin"),
              "password": os.getenv("DEFAULT_PASSWORD", "admin123")},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _stream_sse(
    client: httpx.Client,
    method: str,
    url: str,
    token: str,
    json_body: dict | None = None,
    timeout: float = 240.0,
    extra_headers: dict | None = None,
) -> Iterator[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    if extra_headers:
        headers.update(extra_headers)
    with client.stream(method, url, json=json_body, headers=headers, timeout=timeout) as resp:
        resp.raise_for_status()
        ev_name = None
        data_buf: list[str] = []
        for line in resp.iter_lines():
            if line == "":
                if ev_name and data_buf:
                    data_str = "\n".join(data_buf)
                    try:
                        yield {"event": ev_name, "data": json.loads(data_str)}
                    except Exception:
                        yield {"event": ev_name, "data": data_str}
                ev_name = None
                data_buf = []
                continue
            if line.startswith("event:"):
                ev_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_buf.append(line[len("data:"):].strip())


def _data_of(events: list[dict], name: str):
    """取最后一个同名事件（latest-state-wins）。"""
    hit = None
    for e in events:
        if e["event"] == name:
            hit = e["data"]
    return hit


def _fill_all(card: dict) -> dict:
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
        elif key == "granularity":
            cand = next((v for v, o in zip(values, options) if "月" in o.get("label", "")), None)
            mf["selected_value"] = cand or (values[0] if values else None)
        elif key == "comparison" and values:
            mf["selected_value"] = values[0]
        elif values:
            mf["selected_value"] = values[0]
    for a in filled.get("assumptions", []):
        a["accepted"] = True
    return filled


def _drive_chat(
    client: httpx.Client, token: str, sid: str, query: str,
    *,
    mode: str = "new",
) -> list[dict]:
    return list(_stream_sse(
        client, "POST", "/api/v1/chat", token,
        json_body={"user_query": query, "mode": mode, "session_id": sid},
    ))


def _patch_fill_all(client: httpx.Client, token: str, sid: str, card: dict) -> dict | None:
    pr = client.patch(
        f"/api/v1/sessions/{sid}/requirement",
        json={"requirement": _fill_all(card)},
        headers={"Authorization": f"Bearer {token}"},
    )
    if pr.status_code == 200:
        return pr.json().get("requirement", _fill_all(card))
    return card


def _confirm(client: httpx.Client, token: str, sid: str, fault_header: str | None = None) -> list[dict]:
    extra = {"X-E2E-Fault": fault_header} if fault_header else None
    return list(_stream_sse(client, "POST", f"/api/v1/sessions/{sid}/confirm", token,
                            extra_headers=extra))


def _get_latest_report(client: httpx.Client, token: str, sid: str) -> dict | None:
    r = client.get(f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        return None
    versions = (r.json().get("session") or {}).get("report_versions") or []
    if not versions:
        return None
    latest_v = max((v.get("version", 0) for v in versions if isinstance(v.get("version"), int)),
                   default=None)
    if latest_v is None:
        latest_v = versions[0].get("version")
    if latest_v is None:
        return None
    rr = client.get(f"/api/v1/sessions/{sid}/reports/{latest_v}",
                    headers={"Authorization": f"Bearer {token}"})
    if rr.status_code != 200:
        return None
    return (rr.json() or {}).get("report") or {}


def _report_sql(report: dict | None) -> str:
    return ((report or {}).get("query_snapshot") or {}).get("sql") or ""


def _report_status(report: dict | None) -> str:
    # detail 里 execution_status 与 report_payload 平级；兜底读 payload 内
    return ((report or {}).get("execution_status")
            or ((report or {}).get("report_payload") or {}).get("execution_status")
            or "")


def _answer_rows(report: dict | None) -> int:
    payload = (report or {}).get("report_payload") or {}
    answer = payload.get("answer") or {}
    table = answer.get("table") or {}
    rows = table.get("rows") or []
    return len(rows)


def _run_happy(client, token, sid, query, fault_header=None) -> tuple[dict, list, dict]:
    """new chat → fill-all PATCH → confirm → (card, events, report)。"""
    events = _drive_chat(client, token, sid, query)
    card = _data_of(events, "requirement")
    assert card, f"chat 未产出 requirement card；events={[e['event'] for e in events]}"
    card = _patch_fill_all(client, token, sid, card)
    events += _confirm(client, token, sid, fault_header=fault_header)
    report = _get_latest_report(client, token, sid)
    return card, events, report


@pytest.fixture(scope="module")
def http_client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        try:
            r = client.get("/health")
            if r.status_code != 200:
                pytest.skip(f"backend {BASE_URL} /health 不通")
        except Exception as exc:
            pytest.skip(f"backend {BASE_URL} 不可达: {exc}")
        yield client


@pytest.fixture(scope="module")
def auth_token(http_client):
    return _login(http_client)


class TestRealRagMcpE2E:
    """P15 正式 6 场景。"""

    def test_explicit_query_happy_path(self, http_client, auth_token):
        """happy：真实 SQL 执行 + 报告 SUCCESS + 真表/真列/真行。"""
        sid = f"e2e-happy-{uuid.uuid4().hex[:8]}"
        card, events, report = _run_happy(http_client, auth_token, sid, "2024年各区域销售额排名")
        sql = _report_sql(report)
        assert card.get("status") == "complete", f"card 未 complete: {card.get('status')}"
        assert card.get("time_range") == "2024年", f"time_range: {card.get('time_range')}"
        assert any("销售" in m for m in (card.get("target_metrics") or [])), card.get("target_metrics")
        assert _report_status(report) == "SUCCESS", f"execution: {_report_status(report)}"
        assert sql and "fact_orders" in sql and "order_amount" in sql, f"sql 未用零售真表/列: {sql[:200]}"
        assert _answer_rows(report) >= 1, "answer.table 应有行"
        assert not _data_of(events, "error"), "不应有 SSE error"

    def test_sql_repair_via_mcp_schema_retrieval(self, http_client, auth_token):
        """repair：fault seam once object_not_found → 真 retry_mcp_schema_retrieval → SUCCESS。"""
        sid = f"e2e-repair-{uuid.uuid4().hex[:8]}"
        card, events, report = _run_happy(
            http_client, auth_token, sid, "2024年华东销售额",
            fault_header="kind=object_not_found;mode=once",
        )
        sql = _report_sql(report)
        err = _data_of(events, "error")
        # 硬断言：注入一次 object_not_found 后 repair 链路真恢复，最终 SUCCESS 且无 error
        assert err is None, f"不应有 error: {err}"
        assert _report_status(report) == "SUCCESS", (
            f"repair 未恢复 SUCCESS（若 backend 未以 REPORTAGENT_E2E=1 启动，seam 不激活，"
            f"本 case 不构成确定性证明）: {_report_status(report)}"
        )
        assert sql and "fact_orders" in sql, f"sql: {sql[:200]}"
        assert _answer_rows(report) >= 1

    def test_sql_failure_permanent_fault_never_fakes_success(self, http_client, auth_token):
        """fail：permission persistent → DiagnosePolicy fail-fast → 无 SUCCESS、error 落库。"""
        sid = f"e2e-fail-{uuid.uuid4().hex[:8]}"
        card, events, report = _run_happy(
            http_client, auth_token, sid, "2024年华东销售额",
            fault_header="kind=permission;mode=persistent",
        )
        # 硬条件：不伪造成功
        assert _report_status(report) != "SUCCESS", "永久 fault 不得产出 SUCCESS report"
        err = _data_of(events, "error")
        # 终态：要么 SSE error，要么 FAILED/error 报告落库
        assert (err is not None) or (_report_status(report) in ("FAILED", "error")), (
            f"永久 fault 未以 error/FAILED 终止（seam 未激活?）：status={_report_status(report)}"
        )

    def test_requested_object_not_silently_replaced(self, http_client, auth_token):
        """点名不存在表：NOT complete、澄清 surface，禁止静默换成销售额。"""
        sid = f"e2e-nosilent-{uuid.uuid4().hex[:8]}"
        events = _drive_chat(http_client, auth_token, sid, "查询 unicorn_data 表的所有数据")
        card = _data_of(events, "requirement")
        assert card is not None, "应有 requirement card"
        assert card.get("status") != "complete", (
            "点名 unicorn_data 不得被静默软化成立即可查的 complete 卡（防最危险回归）"
        )
        keys = [a.get("key", "") for a in (card.get("assumptions") or [])]
        assert any(k.startswith("requested_object") for k in keys) or (card.get("missing_fields")), (
            f"澄清未 surface：无 requested_object assumption 也无 missing；assumptions={keys}"
        )
        # 不确认 → 不应有 SUCCESS report
        assert _report_status(_get_latest_report(http_client, auth_token, sid)) != "SUCCESS"

    def test_schema_retrieval_direct_trigger(self, http_client, auth_token):
        """问数据在哪：结构化命中订单表名，正常终止无 error。"""
        sid = f"e2e-schema-{uuid.uuid4().hex[:8]}"
        events = _drive_chat(http_client, auth_token, sid, "订单相关的数据都在哪些表里？")
        assert not _data_of(events, "error"), "schema_retrieval 不应 error"
        # 结构化断言：卡/回答文本里至少出现两订单表 token（弱文案依赖）
        blob = json.dumps([e["data"] for e in events if e["event"] in
                           ("requirement", "report", "answer")], ensure_ascii=False)
        assert "fact_orders" in blob and "fact_payments" in blob, (
            f"未结构化命中订单表：{blob[:400]}"
        )

    def test_multi_turn_supplement_inherits_scope(self, http_client, auth_token):
        """multi_turn：轮2 supplement 继承轮1 time_range/scope/metric，出 v2 SUCCESS。"""
        sid = f"e2e-multi-{uuid.uuid4().hex[:8]}"
        card1, events1, report1 = _run_happy(http_client, auth_token, sid, "2024年华东销售额")
        assert _report_status(report1) == "SUCCESS", "轮1 应先 SUCCESS"

        # 轮2 supplement
        events2 = _drive_chat(http_client, auth_token, sid, "再看月度趋势", mode="supplement")
        card2 = _data_of(events2, "requirement")
        assert card2 is not None, "轮2 应产出 card"
        assert card2.get("time_range") == "2024年", (
            f"supplement 未继承 time_range（P15 e2e bug② 修复目标）: {card2.get('time_range')}"
        )
        assert "华东" in (card2.get("scope") or []), f"supplement 未继承 scope 华东: {card2.get('scope')}"
        assert any("销售" in m for m in (card2.get("target_metrics") or [])), "supplement 未继承 metric"
        # 补齐后 confirm → v2
        card2 = _patch_fill_all(http_client, auth_token, sid, card2)
        events2 += _confirm(http_client, auth_token, sid)
        report2 = _get_latest_report(http_client, auth_token, sid)
        assert _report_status(report2) == "SUCCESS", f"轮2 未 SUCCESS: {_report_status(report2)}"
        assert "month" in (_report_sql(report2) or "").lower() or "月" in (_report_sql(report2) or ""), \
            "轮2 SQL 应按月分组（granularity=月）"
