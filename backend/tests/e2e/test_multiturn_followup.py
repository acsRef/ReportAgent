"""多轮对话真链路测试：同一个会话里连续追问，每轮都必须真的改查询。

用户 2026-11-03 的诉求：「测试的时候要新开一个东西，用来测试多轮对话的具体项，
能不能够继续查出来」——所以这份用例**自建会话**（`e2e-multiturn-<uuid>`），
不读任何已有数据，跑完即可丢弃。

覆盖的回归（每一条都对应一个真实踩过的坑）：

| 轮次 | 输入 | 必须成立 |
|---|---|---|
| 1 | 2024年华东销售趋势和异常 | 12 行、按月分组（不是按年） |
| 2 | 详细讲一下七月份 | 卡片继承 **2024 年**（不是按 today 算成 2026）、SQL 收窄到 month=7 |
| 3 | 那八月份呢 | 继续可用：month=8，一年仍是 2024 |
| 4 | 给我这两个月的明细数据 | 「这两个月」= 刚聊的 7-8 月（不是「最近两个月」）；明细模式：**不聚合** |

运行（需 PG + ragent-py :8000 + backend :8100 + 真 LLM key）：

    REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_multiturn_followup.py -s
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Iterator

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE = "http://127.0.0.1:8100"


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
    """fill-all：把缺失项选上、假设全部接受（与 runner 同策略）。"""
    filled = json.loads(json.dumps(card))
    for field in filled.get("missing_fields") or []:
        options = [o["value"] for o in (field.get("options") or [])]
        if field.get("key") == "time_range" and "2024年" in options:
            field["selected_value"] = "2024年"
        elif options:
            field["selected_value"] = options[0] if field.get("kind") == "single" else options[:1]
    for assumption in filled.get("assumptions") or []:
        assumption["accepted"] = True
    return filled


def _run_turn(client: httpx.Client, token: str, sid: str, query: str, mode: str) -> tuple[dict, dict]:
    events = list(_stream_sse(
        client, "POST", "/api/v1/chat", token,
        json_body={"user_query": query, "mode": mode, "session_id": sid},
    ))
    card = _data_of(events, "requirement") or {}
    assert card, f"第 {query!r} 轮没拿到需求卡"

    patched = client.patch(
        f"/api/v1/sessions/{sid}/requirement",
        json={"requirement": _fill(card)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert patched.status_code == 200, patched.text[:300]
    saved = patched.json().get("requirement") or card

    cf = list(_stream_sse(
        client, "POST", f"/api/v1/sessions/{sid}/confirm", token,
    ))
    err = _data_of(cf, "error")
    assert not err, f"第 {query!r} 轮执行失败: {err}"

    snap = client.get(
        f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {token}"},
    ).json()
    version = max(v["version"] for v in snap["session"]["report_versions"])
    detail = client.get(
        f"/api/v1/sessions/{sid}/reports/{version}",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["report"]
    return saved, detail


def test_multiturn_followups_keep_narrowing_and_switching_to_detail() -> None:
    sid = f"e2e-multiturn-{uuid.uuid4().hex[:8]}"
    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        token = _login(client)

        # 1) 全年趋势
        card, report = _run_turn(client, token, sid, "2024年华东销售趋势和异常", "new")
        assert card.get("granularity") == "month"
        assert len(report["query_snapshot"]["rows"]) == 12, "应按月 12 行"
        assert "GROUP BY" in report["query_snapshot"]["sql"].upper()

        # 2) 追问单月：年份必须继承 2024（不是按 today 解析成 2026）
        card, report = _run_turn(client, token, sid, "详细讲一下七月份", "supplement")
        assert "2024" in str(card.get("time_range")), card.get("time_range")
        assert "7" in str(card.get("time_range")), card.get("time_range")
        sql = report["query_snapshot"]["sql"]
        assert "month = 7" in sql.replace("=", "= ").replace("=  ", "= ").lower() or "month = 7" in sql.lower(), sql
        assert len(report["query_snapshot"]["rows"]) == 1

        # 3) 再换一个月：仍然可用
        card, report = _run_turn(client, token, sid, "那八月份呢", "supplement")
        assert "8" in str(card.get("time_range")), card.get("time_range")
        assert len(report["query_snapshot"]["rows"]) == 1

        # 4) 指代 + 明细：这两个月 = 7-8 月，且不聚合
        card, report = _run_turn(client, token, sid, "给我这两个月的明细数据", "supplement")
        assert card.get("detail_level") == "detail", card
        assert "7" in str(card.get("time_range")) and "8" in str(card.get("time_range"))
        sql = report["query_snapshot"]["sql"].upper()
        assert "GROUP BY" not in sql, f"明细模式不应聚合：{sql}"
        assert "LIMIT" in sql
        assert len(report["query_snapshot"]["rows"]) > 10, "明细应返回多行原始记录"
