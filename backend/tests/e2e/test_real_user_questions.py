"""真实问法矩阵（主干测试入口，env-gated）。

用户 2026-11-03 的要求：
- 用例要用**用户真实问法**，不是 prompt 里的示例句；
- 断言只问「能不能查出来」，不是核对卡片字段；
- 优先跑这条主干，不跑上千个单测。

运行（需 PG + ragent-py:8000 + backend:8100 + 真 LLM key）：

    REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_real_user_questions.py -s

`-s` 会打印每条问法的卡片/SQL/行数，便于人工看「答得对不对」。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest
import yaml

pytestmark = pytest.mark.e2e

BASE = "http://127.0.0.1:8100"
DATASET = Path(__file__).resolve().parents[3] / "evaluation" / "real_user_questions.yaml"


# --- SSE 驱动（与 evaluation/runner 同模式，自包含避免跨包 import） ---------

def _login(client: httpx.Client) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _stream_sse(
    client: httpx.Client, method: str, url: str, token: str, *,
    json_body: dict | None = None, timeout: float = 240.0,
) -> Iterator[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    with client.stream(method, url, json=json_body, headers=headers, timeout=timeout) as resp:
        resp.raise_for_status()
        ev_name = None
        data_buf: list[str] = []
        for line in resp.iter_lines():
            if line == "":
                if ev_name and data_buf:
                    raw = "\n".join(data_buf)
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        parsed = raw
                    yield {"event": ev_name, "data": parsed}
                ev_name, data_buf = None, []
                continue
            if line.startswith("event:"):
                ev_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_buf.append(line[len("data:"):].strip())


def _data_of(events: list[dict], name: str) -> Any:
    found = None
    for evt in events:
        if evt["event"] == name:
            found = evt["data"]
    if isinstance(found, str):
        try:
            return json.loads(found)
        except Exception:
            return found
    return found


def _fill(card: dict) -> dict:
    """fill-all：缺什么就选第一个候选，假设全部接受（与 runner 同策略）。"""
    filled = json.loads(json.dumps(card))
    for field in filled.get("missing_fields") or []:
        options = [o["value"] for o in (field.get("options") or [])]
        if not options:
            continue
        field["selected_value"] = options[0] if field.get("kind") == "single" else options[:1]
    for assumption in filled.get("assumptions") or []:
        assumption["accepted"] = True
    return filled


def _load_cases() -> list[dict]:
    data = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    return data["cases"]


CASES = _load_cases()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_real_user_question(case: dict) -> None:
    sid = f"realq-{case['id']}-{uuid.uuid4().hex[:4]}"
    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        token = _login(client)

        events = list(_stream_sse(
            client, "POST", "/api/v1/chat", token,
            json_body={"user_query": case["query"], "mode": "new", "session_id": sid},
        ))
        card = _data_of(events, "requirement") or {}
        assert card, f"没拿到需求卡：{case['query']}"

        missing_keys = [m.get("key") for m in (card.get("missing_fields") or [])]
        print(f"\n[{case['id']}] {case['query']}")
        print(f"  card: status={card.get('status')} missing={missing_keys} "
              f"methods={card.get('analysis_methods')} time={card.get('time_range')}")

        if case.get("must_clarify"):
            assert card.get("status") == "missing", (
                f"模糊问法必须先澄清，但卡片直接 complete：{case['query']}"
            )
            assert "analysis_intent" in missing_keys, (
                f"缺少「分析角度」澄清项：{missing_keys}"
            )
            return

        # 只有「未接受的假设」压着状态时也算可作答（用户点一下「接受」即可，
        # 与真实 UI 一致）；真正缺字段才算需要澄清。
        answerable = card.get("status") == "complete" or (
            card.get("status") == "missing" and not missing_keys
        )
        if not answerable:
            assert case.get("allow_clarify"), (
                f"卡片未 complete 且用例不允许澄清：{case['query']} missing={missing_keys}"
            )
            return

        patched = client.patch(
            f"/api/v1/sessions/{sid}/requirement",
            json={"requirement": _fill(card)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert patched.status_code == 200, patched.text[:300]

        cf = list(_stream_sse(client, "POST", f"/api/v1/sessions/{sid}/confirm", token))
        err = _data_of(cf, "error")
        assert not err, f"执行失败：{err}"

        snap = client.get(
            f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {token}"},
        ).json()
        version = max(v["version"] for v in snap["session"]["report_versions"])
        detail = client.get(
            f"/api/v1/sessions/{sid}/reports/{version}",
            headers={"Authorization": f"Bearer {token}"},
        ).json()["report"]

        sql = (detail.get("query_snapshot") or {}).get("sql") or ""
        rows = (detail.get("query_snapshot") or {}).get("rows") or []
        answer = (detail.get("report_payload") or {}).get("answer") or {}
        table_cols = [
            str(c.get("title") or c.get("key") or "")
            for c in ((answer.get("table") or {}).get("columns") or [])
        ]
        print(f"  sql: {' '.join(sql.split())[:180]}")
        print(f"  rows={len(rows)} cols={table_cols}")

        assert detail.get("execution_status") == "SUCCESS", (
            f"未能查出数据：{detail.get('execution_status')}"
        )
        assert len(rows) >= int(case.get("min_rows", 1)), (
            f"行数过少：{len(rows)} < {case.get('min_rows')}"
        )
        if case.get("max_rows") is not None:
            assert len(rows) <= int(case["max_rows"]), (
                f"行数过多：{len(rows)} > {case['max_rows']}"
            )
        for token_name in case.get("require_columns") or []:
            assert any(token_name in col for col in table_cols) or token_name in sql, (
                f"结果缺少列「{token_name}」：cols={table_cols}"
            )
        for forbidden in case.get("sql_must_not") or []:
            assert forbidden.upper() not in sql.upper(), (
                f"SQL 不该出现 {forbidden}：{sql[:200]}"
            )
