"""Final Hardening ⑧：SQL 语义评估——canonical reference 数值比对（live）。

Review 指出的缺口：现有测试断言 SQL 文本/结构（substring、rows>0），Live E2E
只到 honest-terminal——没有任何层验证「LLM 生成的 SQL 在语义上等于用户要的
答案」。本文件用 **canonical reference SQL** 在同库同刻直接执行得基准值，
与 Agent 产物（query_snapshot.rows）做数值比对：

  - 同一 DB 上 canonical 与 agent 查询的随机 seed 完全一致 → 期望值无需硬编码；
  - 比对维度（总额标量 / 区域→金额 map / 月度序列 / Top 集合 / 分组 map）足以
    抓住 double_fact（行翻倍→值翻倍）、错误表/错误口径（值不同）、WHERE 漏年
    （值域不同）等语义级错误——SQL 文本不同但语义对 → 通过（这正是语义 vs
    字符串评测的分界）；
  - LLM 失败（FAILED/error）在本套件里 = 能力失败，显式 fail 并记录现场
    （capability 测量，不是可以绿双分支的 honest-terminal——那是 Reliability
    层的职责；本文件测的是「能不能算对」）。

前置与运行同 test_real_rag_mcp_e2e.py（PG 零售 seed + backend :8100 +
REPORTAGENT_E2E=1 + LLM key；repo root 执行）：
    REPORTAGENT_E2E=1 D:/miniConda/envs/agent/python.exe -m pytest \\
        evaluation/tests/test_semantic_sql_accuracy.py -v
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("REPORTAGENT_E2E") != "1",
    reason="REPORTAGENT_E2E != 1; skipping live semantic accuracy test",
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))  # canonical 直跑 sql_tools
from test_real_rag_mcp_e2e import (  # noqa: E402  复用 live 驱动 helpers
    _data_of,
    _get_latest_report,
    _login,
    _patch_fill_all,
    _report_status,
    _stream_sse,
)

BASE_URL = os.getenv("REPORTAGENT_E2E_BASE_URL", "http://127.0.0.1:8100")

_D = Decimal


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _close(a, b, rel: str = "1E-6") -> bool:
    x, y = _dec(a), _dec(b)
    if x is None or y is None:
        return False
    return abs(x - y) <= Decimal(rel) * max(Decimal(1), abs(x), abs(y))


def _rows(report: dict | None) -> list[dict]:
    payload = (report or {}).get("report_payload") or {}
    answer = payload.get("answer") or {}
    table = answer.get("table") or {}
    return table.get("rows") or []


def _sql(report: dict | None) -> str:
    return ((report or {}).get("query_snapshot") or {}).get("sql") or ""


def _require_success(report: dict | None, label: str) -> list[dict]:
    status = _report_status(report)
    assert status == "SUCCESS", (
        f"{label}: Agent 未达 SUCCESS（status={status}，sql={_sql(report)[:120]!r}）"
        f"——语义正确性是能力指标，LLM 失败按失败记录"
    )
    return _rows(report)


def _semantic_requirement(card: dict, *, metric: str, time_range: str = "2024年") -> dict:
    """把 LLM 产的卡改写为「权威约束卡」再 PATCH（语义评测专用）。

    P15 `_patch_fill_all` 面向业务跑通：会默认补 granularity=月、全盘接受
    assumptions（如 LLM/字典把「销售额」释义成 fact_payments.payment_amount）——
    对语义比对这是灾难：单维 case 被改成 region×month 二维、口径被改写，
    与 canonical 必然不等。语义评测的立场 = 用户把需求改准确：只保留 case
    的真实约束（指标/时间），清空发明的 missing 与 assumptions，status
    强制 complete——被测的是「SQL 生成/执行/报告层语义」，不是 requirement 解析层。
    """
    filled = json.loads(json.dumps(card))
    filled["target_metrics"] = [metric]
    filled["time_range"] = time_range
    filled["missing_fields"] = []
    filled["assumptions"] = []
    filled["status"] = "complete"
    return filled


def _run_agent(client, token, query: str, *, metric: str) -> dict:
    sid = f"semantic-{uuid.uuid4().hex[:8]}"
    events = list(_stream_sse(client, "POST", "/api/v1/chat", token,
                              json_body={"user_query": query, "mode": "new",
                                         "session_id": sid}))
    card = _data_of(events, "requirement")
    assert card, f"{query}: chat 未产出 requirement card"
    card = _semantic_requirement(card, metric=metric)
    card = _patch_fill_all(client, token, sid, card)
    events += list(_stream_sse(client, "POST", f"/api/v1/sessions/{sid}/confirm", token))
    assert any(e["event"] == "done" for e in events), f"{query}: confirm 无 done（超时/异常）"
    report = _get_latest_report(client, token, sid)
    assert report is not None, f"{query}: 无落库 report"
    return report


def _run_canonical(sql: str) -> list[dict]:
    """canonical reference 直接执行（backend 同库；sql_tools 输出 JSON）。"""
    from app.tools.sql_tools import execute_sql

    return json.loads(execute_sql(sql))["rows"]


_CANON = {
    "total_2024": (
        "SELECT SUM(o.order_amount) AS total FROM fact_orders o "
        "WHERE o.order_date >= '2024-01-01' AND o.order_date < '2025-01-01'"
    ),
    "by_region_2024": (
        "SELECT s.region AS region, SUM(o.order_amount) AS v FROM fact_orders o "
        "JOIN dim_store s ON o.store_id = s.store_id "
        "WHERE o.order_date >= '2024-01-01' AND o.order_date < '2025-01-01' "
        "GROUP BY s.region"
    ),
    # Review 修正：canonical 必须显式限 2024——gold evaluator 的 ground truth
    # 不能依赖「seed 只有 2024」这个隐含事实（未来加数据即静默失真）。
    # monthly 直接返回 (月序号 int, 金额)，便于 map 级键对齐比较。
    "monthly_2024": (
        "SELECT EXTRACT(MONTH FROM o.order_date)::int AS m, SUM(o.order_amount) AS v "
        "FROM fact_orders o "
        "WHERE o.order_date >= '2024-01-01' AND o.order_date < '2025-01-01' "
        "GROUP BY 1 ORDER BY 1"
    ),
    "top5_qty_2024": (
        "SELECT pr.product_name AS product_name FROM fact_orders o "
        "JOIN dim_product pr ON o.product_id = pr.product_id "
        "WHERE o.order_date >= '2024-01-01' AND o.order_date < '2025-01-01' "
        "GROUP BY pr.product_name ORDER BY SUM(o.quantity) DESC LIMIT 5"
    ),
    "by_paymethod_2024": (
        "SELECT o.payment_method AS pm, SUM(o.order_amount) AS v FROM fact_orders o "
        "WHERE o.order_date >= '2024-01-01' AND o.order_date < '2025-01-01' "
        "GROUP BY o.payment_method ORDER BY v DESC"
    ),
    "refund_total_2024": (
        "SELECT SUM(p.payment_amount) AS total FROM fact_payments p "
        "WHERE p.status = 'REFUNDED' "
        "AND p.payment_date >= '2024-01-01' AND p.payment_date < '2025-01-01'"
    ),
}


def _money_values(row_list: list[dict]) -> list[Decimal]:
    """金额列识别：numeric 金额在 JSON transport 是精确字符串且必含小数点
    （Decimal 全链，SUM(numeric(10,2)) 形如 "12345.67"）。int 维度列是 JSON
    number 天然排除；str 形式的维度数字（如 store_id "1001"）因无小数点
    同样排除——比「任意 str 数值」scan 严格，避免把维度误算进总额。"""
    out = []
    for r in row_list:
        for v in r.values():
            if isinstance(v, str) and "." in v and _dec(v) is not None:
                out.append(_dec(v))
    return out


def _month_key(v) -> int | None:
    """把 agent 的月份表示归一成 1..12：int / "1" / "2024-01" / "2024-01-01…"
    / "1月" / "January" 之外的未知格式返回 None（宁缺勿纵：无法对齐即失败，
    不允许 silent 漏判制造 false pass）。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if 1 <= v <= 12 else None
    s = str(v).strip()
    if not s:
        return None
    low = s.lower()
    months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    if low.startswith(tuple(months)) and (len(s) == 3 or s[3] in " ."):
        for name, num in months.items():
            if low.startswith(name):
                return num
    # 数字前缀（"1月"/"1"）
    head = ""
    for ch in s:
        if ch.isdigit():
            head += ch
        else:
            break
    if head:
        n = int(head)
        return n if 1 <= n <= 12 else None
    # "2024-01" / "2024-01-01 00:00:00" 形态
    if len(s) >= 7 and s[4:5] == "-":
        try:
            n = int(s[5:7])
            return n if 1 <= n <= 12 else None
        except ValueError:
            return None
    return None


class TestSemanticSqlAccuracy:
    """6 例语义比对：SUCCESS 则数值必须等于 canonical；LLM 失败按能力失败记。"""

    def test_total_sales_2024_matches_canonical(self, http_client, auth_token):
        report = _run_agent(http_client, auth_token, "2024年总销售额是多少", metric="销售额")
        rows = _require_success(report, "2024 总销售额")
        # LLM plan 层有权按合理粒度分组（实测常按 12 月明细返回）——语义判定
        # 用集合等价：全部金额列之和 == canonical 总额（金额列识别见
        # _money_values：只收含小数点的精确金额串，str 维度数字不误入）。
        total = sum(_money_values(rows), Decimal(0))
        canon_val = _run_canonical(_CANON["total_2024"])[0]["total"]
        assert _close(total, canon_val), (
            f"总额语义不符：agent 金额列和={total} canonical={canon_val}\n{_sql(report)[:300]}"
        )

    def test_region_sales_2024_map_matches_canonical(self, http_client, auth_token):
        report = _run_agent(http_client, auth_token, "2024年各区域销售额", metric="销售额")
        rows = _require_success(report, "区域销售额")
        # 行结构位置化（label 列, value 列）——不依赖 agent 选的中文别名
        agent = {}
        for r in rows:
            cols = list(r)
            if len(cols) >= 2:
                agent[str(r[cols[0]])] = r[cols[1]]
        canon = {r["region"]: r["v"] for r in _run_canonical(_CANON["by_region_2024"])}
        assert set(agent) == set(canon), (
            f"区域集合不符：agent={sorted(agent)} canonical={sorted(canon)}\n{_sql(report)[:300]}"
        )
        for k in canon:
            assert _close(agent[k], canon[k]), (
                f"区域 {k} 销售额不符：agent={agent[k]!r} canonical={canon[k]!r}"
            )

    def test_monthly_trend_2024_series_matches_canonical(self, http_client, auth_token):
        report = _run_agent(http_client, auth_token, "2024年各月销售额趋势", metric="销售额")
        rows = _require_success(report, "月度趋势")
        # 键对齐比较（Review 修正）：month → value map，逐月断言——sorted 值
        # 比较允许「3 月值顶替 1 月」的错位 pass；map 级对齐才能验证「哪个月
        # 对应哪个金额」。金额列=含小数点 str；月份键经 _month_key 归一
        # （int/"1"/"2024-01"/"1月"），无法归一的格式宁缺勿纵直接失败。
        def agent_month_map(row_list: list[dict]) -> dict[int, Decimal]:
            out: dict[int, Decimal] = {}
            for r in row_list:
                money = _money_values([r])
                assert len(money) == 1, f"行金额列不唯一/缺失: {r}"
                keys = [_month_key(v) for k, v in r.items() if not (
                    isinstance(v, str) and "." in v and _dec(v) is not None
                )]
                month = next((k for k in keys if k is not None), None)
                assert month is not None, f"行无法归一月份键（格式不支持）: {r}"
                assert month not in out, f"重复月份键 {month}: {r}"
                out[month] = money[0]
            return out

        agent_map = agent_month_map(rows)
        canon_map = {int(r["m"]): _dec(r["v"]) for r in _run_canonical(_CANON["monthly_2024"])}
        assert set(agent_map) == set(canon_map), (
            f"月份键集合不符 agent={sorted(agent_map)} canon={sorted(canon_map)}"
        )
        for m in sorted(canon_map):
            assert _close(agent_map[m], canon_map[m]), (
                f"{m} 月值不符 agent={agent_map[m]} canonical={canon_map[m]}"
            )

    def test_top5_products_by_quantity_matches_canonical(self, http_client, auth_token):
        report = _run_agent(http_client, auth_token, "2024年销量最高的5个商品", metric="销量")
        rows = _require_success(report, "Top5 商品")
        agent_set = {str(r[list(r)[0]]) for r in rows}
        canon_names = {r["product_name"] for r in _run_canonical(_CANON["top5_qty_2024"])}
        assert agent_set == canon_names, (
            f"Top5 集合不符 agent={agent_set} canon={canon_names}\n{_sql(report)[:300]}"
        )

    def test_payment_method_sales_2024_matches_canonical(self, http_client, auth_token):
        report = _run_agent(http_client, auth_token, "2024年各支付方式销售额", metric="销售额")
        rows = _require_success(report, "支付方式销售额")
        agent = {}
        for r in rows:
            cols = list(r)
            if len(cols) >= 2:
                agent[str(r[cols[0]])] = r[cols[1]]
        canon = {r["pm"]: r["v"] for r in _run_canonical(_CANON["by_paymethod_2024"])}
        assert set(agent) == set(canon), f"支付方式集合不符 agent={sorted(agent)} canon={sorted(canon)}"
        for k in canon:
            assert _close(agent[k], canon[k]), f"支付方式 {k} 不符 agent={agent[k]!r} canon={canon[k]!r}"

    def test_refund_total_2024_matches_canonical(self, http_client, auth_token):
        report = _run_agent(http_client, auth_token, "2024年退款金额合计", metric="退款金额")
        rows = _require_success(report, "退款合计")
        # plan 层可能按月明细返回（同 total case）——集合等价：金额列之和 == canonical
        total = sum(_money_values(rows), Decimal(0))
        canon_val = _run_canonical(_CANON["refund_total_2024"])[0]["total"]
        assert _close(total, canon_val), (
            f"退款合计不符 agent 金额列和={total} canonical={canon_val}\n{_sql(report)[:300]}"
        )


@pytest.fixture(scope="module")
def http_client():
    import httpx

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
