"""Background-task timeout E2E（P15 reliability 收口 ⑧，真调用）。

宪法 §11：后台任务超 `MAX_TASK_DURATION` → Persist FAILED → ReportVersion(error)，**不允许
永远停在 generating**。这与 `sse_disconnect`（客户端断连 ≠ 后端失败，任务照跑）**正交**——
本文件测的是「后端真超时」的显式收尾。

`MAX_TASK_DURATION` 是 import 时模块常量，无法按请求压短 → 需第二 backend 实例以低
`MAX_TASK_DURATION` 启动（如 5s），由 `REPORTAGENT_E2E_TIMEOUT_BASE_URL` 指向：
    MAX_TASK_DURATION=5 REPORTAGENT_E2E=1 D:/miniConda/envs/agent/python.exe \\
        -m uvicorn app.main:app --port 8101   # 注意：requirement chat 不走 run_with_timeout，
                                               # 低 MAX_TASK_DURATION 不影响 chat；confirm 正常
                                               # 30-90s 必然超 5s。

gate：REPORTAGENT_E2E=1 且 REPORTAGENT_E2E_TIMEOUT_BASE_URL 已设。
"""
from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest

from evaluation.tests.test_real_rag_mcp_e2e import (
    _data_of,
    _drive_chat,
    _fill_all,
    _get_latest_report,
    _report_status,
    _stream_sse,
)

_TIMEOUT_BASE_URL = os.getenv("REPORTAGENT_E2E_TIMEOUT_BASE_URL")

pytestmark = [
    pytest.mark.skipif(
        os.getenv("REPORTAGENT_E2E") != "1",
        reason="REPORTAGENT_E2E != 1; skipping real backend e2e test",
    ),
    pytest.mark.skipif(
        not _TIMEOUT_BASE_URL,
        reason="REPORTAGENT_E2E_TIMEOUT_BASE_URL not set; need low MAX_TASK_DURATION instance",
    ),
]


def _patch_fill(client: httpx.Client, token: str, sid: str, card: dict) -> dict:
    pr = client.patch(
        f"/api/v1/sessions/{sid}/requirement",
        json={"requirement": _fill_all(card)},
        headers={"Authorization": f"Bearer {token}"},
    )
    pr.raise_for_status()
    return pr.json()["requirement"]


def test_background_timeout_never_stuck_generating():
    """confirm 超过 MAX_TASK_DURATION → 显式 TASK_TIMEOUT 收尾（phase≠generating + 落库）。"""
    with httpx.Client(base_url=_TIMEOUT_BASE_URL, timeout=30.0) as client:
        try:
            r = client.get("/health")
            if r.status_code != 200:
                pytest.skip("timeout backend /health 不通")
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"timeout backend 不可达: {exc}")
        r = client.post(
            "/api/v1/auth/login",
            json={"username": os.getenv("DEFAULT_USERNAME", "admin"),
                  "password": os.getenv("DEFAULT_PASSWORD", "admin123")},
        )
        r.raise_for_status()
        token = r.json()["access_token"]

        # requirement chat（不走 run_with_timeout → 低 MAX_TASK_DURATION 不影响）
        sid = f"e2e-bgtimeout-{uuid.uuid4().hex[:8]}"
        events = _drive_chat(client, token, sid, "2024年华东销售额")
        card = _data_of(events, "requirement")
        assert card is not None, "chat 应产出 requirement card"
        _patch_fill(client, token, sid, card)

        # confirm：正常 30-90s 的链路被 MAX_TASK_DURATION=5 掐断 → 显式 timeout 收尾
        t0 = time.time()
        ev = list(_stream_sse(client, "POST", f"/api/v1/sessions/{sid}/confirm", token))
        elapsed = time.time() - t0

        err = _data_of(ev, "error")
        assert err is not None, "后台任务超时应出 SSE error"
        assert err.get("code") == "TASK_TIMEOUT", (
            f"超时应 TASK_TIMEOUT（非 QUERY_* 混淆）: {err.get('code')}"
        )
        assert err.get("kind") == "timeout"
        done = [e["data"] for e in ev if e["event"] == "done"]
        assert done and done[-1].get("final_phase") == "error", (
            f"超时终态应 error: {done}"
        )
        # 有界：不应拖到正常链路时长（5s 预算 + 少量裕量即够）
        assert elapsed < 30, f"background timeout 应有界（MAX_TASK_DURATION=5）: {elapsed:.0f}s"

        # session phase 不得停在 generating；报告/错误落库（轮询兜 DB 写入时序）
        sess = None
        for _ in range(10):
            rr = client.get(f"/api/v1/sessions/{sid}",
                            headers={"Authorization": f"Bearer {token}"})
            sess = (rr.json() or {}).get("session") or {}
            phase = sess.get("phase")  # API 层字段名是 phase（非 DB current_phase）
            if phase and phase != "generating":
                break
            time.sleep(1)
        phase = sess.get("phase")
        assert phase != "generating", "超时后 session 不得停在 generating（宪法 §11）"
        assert phase == "error", f"超时后 phase 应 error: {phase}"

        report = _get_latest_report(client, token, sid)
        assert report is not None, "超时必须有 report version 行（不停 generating）"
        assert _report_status(report) in ("error", "FAILED", ""), (
            f"超时落库不能是 SUCCESS: {_report_status(report)}"
        )
        payload = (report or {}).get("report_payload") or {}
        assert (payload.get("error") or {}).get("code") == "TASK_TIMEOUT", (
            f"落库 error.code 应 TASK_TIMEOUT: {(payload.get('error') or {}).get('code')}"
        )
