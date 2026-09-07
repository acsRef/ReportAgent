"""G4（P16 手动门固化版）：Memory → SQL Agent 边界真 LLM 行为验收（env-gated）。

用户 G4 三个 case + 1 个 control（无记忆基线），验证「记忆接进生产链后 LLM 仍守边界」：

- control（OFF 基线）：无任何记忆 → "统计2024年各区域销售额" 产 GROUP BY region + SUM(order_amount)
- ① 偏好不偏 SQL：库里 stable_preference「报告图表优先使用柱状图」+ query 带「报告」词
  （触发 EXECUTION 档 semantic 召回→D4 drop）→ SQL 仍 GROUP BY region，不被图表偏好畸变
- ② 参考续 SQL：query_template（2024 区域销售额 GROUP BY region 正确 SQL）→ "去年各区域销售情况"
  → 结构复用（JOIN dim_store + GROUP BY region）+ 时间条件更新（去年=2025，≠2024）
- ③ 错误历史不诱导：query_template（COUNT(*) 订单量）→ "统计2024年各区域销售额"
  → 主聚合 SUM(金额)，不得变 COUNT(*)（ORDER COUNT 参考不切换指标）

这是 P16 唯一 env-gated（REPORTAGENT_E2E=1）真 LLM 行为层——离线 mock 无法证明「LLM 守边界」，
由本文件 + 真服务首次运行产生证据（SQL 原文原样打印；失败也打印，供人工判定 LLM 输出是否可接受）。

前置（同 P15 e2e）：
1. PG + seed_business_p15prelude.sql（fact_orders/dim_store 现役 schema，全 2024）
2. ragent-py :8000（字典 KB）+ ReportAgent backend :8100（REPORTAGENT_E2E=1 + 真 LLM key）
3. 本文件以 REPORTAGENT_E2E=1 运行（与 backend 同 gate）

执行（repo root）：
    REPORTAGENT_E2E=1 D:/miniConda/envs/agent/python.exe -m pytest \\
        evaluation/tests/test_memory_llm_sql_behavior.py -v -s
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Iterator

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("REPORTAGENT_E2E") != "1",
    reason="REPORTAGENT_E2E != 1; skipping real LLM memory behavior test",
)

_ROOT = Path(__file__).resolve().parents[2]

BASE_URL = os.getenv("REPORTAGENT_E2E_BASE_URL", "http://127.0.0.1:8100")

# --- HTTP helpers（与 test_real_rag_mcp_e2e.py 同款；G4 关注 SQL 行为） -----------


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
    timeout: float = 300.0,
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


def _run_e2e(client: httpx.Client, token: str, sid: str, query: str) -> tuple[dict, list, dict]:
    """new chat → fill-all PATCH → confirm → (card, events, report)。"""
    events = list(_stream_sse(
        client, "POST", "/api/v1/chat", token,
        json_body={"user_query": query, "mode": "new", "session_id": sid},
    ))
    card = _data_of(events, "requirement")
    assert card, f"chat 未产出 requirement card；events={[e['event'] for e in events]}"
    pr = client.patch(
        f"/api/v1/sessions/{sid}/requirement",
        json={"requirement": _fill_all(card)},
        headers={"Authorization": f"Bearer {token}"},
    )
    pr.raise_for_status()
    events += list(_stream_sse(client, "POST", f"/api/v1/sessions/{sid}/confirm", token))
    report = None
    r = client.get(f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 200:
        versions = (r.json().get("session") or {}).get("report_versions") or []
        if versions:
            latest_v = versions[-1].get("version")
            rr = client.get(
                f"/api/v1/sessions/{sid}/reports/{latest_v}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if rr.status_code == 200:
                report = (rr.json() or {}).get("report") or {}
    return card, events, report


def _report_sql(report: dict | None) -> str:
    return ((report or {}).get("query_snapshot") or {}).get("sql") or ""


def _report_status(report: dict | None) -> str:
    return ((report or {}).get("execution_status")
            or ((report or {}).get("report_payload") or {}).get("execution_status")
            or "")


# --- 种子（backend 模块直插；真 embedding + 真 PG；user=admin 保持一致） ----------

_PG_CONN_STR = os.getenv("DATABASE_URL", "postgresql://ragent:ragent@localhost:5432/ragent")


def _admin_uid() -> int:
    import psycopg2

    conn = psycopg2.connect(_PG_CONN_STR)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT id FROM app.users WHERE username=%s", (os.getenv("DEFAULT_USERNAME", "admin"),))
    uid = cur.fetchone()[0]
    conn.close()
    return uid


def _clear_memory(uid: int) -> None:
    import psycopg2

    conn = psycopg2.connect(_PG_CONN_STR)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM memory.semantic_entry WHERE user_id=%s", (uid,))
    cur.execute("DELETE FROM memory.query_template WHERE user_id=%s", (uid,))
    conn.close()


def _seed_run(coro):
    """backend 模块种子：独立事件循环 + pool init（vector codec）+ .env（真 key）。"""
    sys.path.insert(0, str(_ROOT / "backend"))

    async def _body():
        from dotenv import load_dotenv

        load_dotenv(str(_ROOT / ".env"))
        from app.infra.db.postgres import close_pool, init_pool

        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


def _seed_preference(content: str) -> None:
    uid = _admin_uid()

    async def _go():
        from app.infra.memory.memory_manager import MemoryManager

        await MemoryManager().remember_preference(
            user_id=str(uid), content=content,
            memory_type="stable_preference", importance=0.8, source="user_turn",
            status="active", confidence="high",
        )

    _seed_run(_go())


def _seed_query(question: str, sql: str, metric: str) -> None:
    uid = _admin_uid()

    async def _go():
        from app.infra.memory.memory_manager import MemoryManager

        await MemoryManager().remember_query(
            question=question, sql=sql, schema=None,
            target_metric=metric, user_id=uid,
        )

    _seed_run(_go())


# --- cases ------------------------------------------------------------------


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


@pytest.fixture()
def clean_memory():
    """每 case 前清场 admin 记忆（control 基线必须无记忆）。"""
    _clear_memory(_admin_uid())
    yield


class TestMemoryLLMSQLBehavior:
    """G4：真 LLM 下 Memory → SQL 边界。SQL 原文原样打印（证据）。"""

    def _report_sql(self, client, token, sid, query) -> str:
        card, events, report = _run_e2e(client, token, sid, query)
        sql = _report_sql(report)
        status = _report_status(report)
        print(f"\n=== query: {query}\n--- card.status={card.get('status')} "
              f"report.status={status}\n--- SQL:\n{sql}\n--- SSE error: "
              f"{_data_of(events, 'error')}")
        return sql

    def test_control_no_memory_baseline(self, http_client, auth_token, clean_memory):
        """OFF 基线：无记忆 → 正常 GROUP BY region + SUM(金额)。"""
        sql = self._report_sql(
            http_client, auth_token, f"g4-ctl-{uuid.uuid4().hex[:8]}",
            "统计2024年各区域销售额",
        )
        assert "fact_orders" in sql, f"未命中订单表: {sql}"
        assert "GROUP BY" in sql and "region" in sql.lower(), f"无区域聚合: {sql}"
        assert "SUM(" in sql.upper(), f"无金额聚合: {sql}"

    def test_preference_does_not_distort_sql(self, http_client, auth_token, clean_memory):
        """① 偏好（柱状图）不得污染 SQL——「报告」词触发 EXECUTION 档语义召回→D4 drop。"""
        _seed_preference("报告图表优先使用柱状图展示")
        sql = self._report_sql(
            http_client, auth_token, f"g4-p1-{uuid.uuid4().hex[:8]}",
            "生成2024年各区域销售额报告",
        )
        # SQL 语义不受图表偏好影响：区域聚合 + SUM(金额) 保持（不加入 chart 相关畸变——
        # 如无谓 ORDER BY / 换聚合对象）。"柱状图/报告" 文本不应出现在 SQL 里。
        assert "GROUP BY" in sql and "region" in sql.lower(), f"区域聚合丢失（偏好污染?）: {sql}"
        assert "SUM(" in sql.upper(), f"金额聚合丢失: {sql}"
        assert "柱状图" not in sql and "报告" not in sql, f"偏好文本泄漏进 SQL: {sql}"

    def test_query_memory_structure_reuse_time_updated(
        self, http_client, auth_token, clean_memory,
    ):
        """② 参考续 SQL：结构复用（JOIN + GROUP BY region）+ 时间条件更新（去年=2025≠2024）。"""
        _seed_query(
            "2024 年各区域销售额排名",
            "SELECT ds.region, SUM(fo.order_amount) AS sales "
            "FROM fact_orders fo JOIN dim_store ds ON fo.store_id = ds.store_id "
            "WHERE fo.order_date BETWEEN '2024-01-01' AND '2024-12-31' "
            "GROUP BY ds.region",
            "销售额",
        )
        sql = self._report_sql(
            http_client, auth_token, f"g4-q1-{uuid.uuid4().hex[:8]}",
            "去年各区域销售情况",
        )
        assert "GROUP BY" in sql and "region" in sql.lower(), f"结构未复用: {sql}"
        assert "JOIN" in sql.upper(), f"JOIN 结构未保持: {sql}"
        # 去年（今天 2026-09）= 2025：时间条件必须更新为新语义（允许 2025/EXTRACT 变体）
        assert "2025" in sql, f"时间条件未更新为去年（2025）: {sql}"

    def test_wrong_history_does_not_switch_to_count(
        self, http_client, auth_token, clean_memory,
    ):
        """③ COUNT(*) 订单量历史不诱导：查询销售额 → 主聚合 SUM(金额)，非 COUNT(*)。"""
        _seed_query(
            "统计订单量",
            "SELECT COUNT(*) AS order_count FROM fact_orders",
            "订单量",
        )
        sql = self._report_sql(
            http_client, auth_token, f"g4-c1-{uuid.uuid4().hex[:8]}",
            "统计2024年各区域销售额",
        )
        assert "COUNT(*)" not in sql, f"被 COUNT(*) 历史诱导（主指标错）: {sql}"
        assert "SUM(" in sql.upper(), f"金额聚合未出现: {sql}"
        assert "GROUP BY" in sql and "region" in sql.lower(), f"区域聚合丢失: {sql}"
