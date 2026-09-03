"""Real-API edge-case 扩充集（P15，全部真实调用接口，无 mock）。

复用 `test_real_rag_mcp_e2e` 的 driver（login/SSE/fill-all/confirm/report），新增覆盖：
  security_injection  注入 query → SECURITY_REJECTED，不执行
  multi_join          多表联合（fact_orders + dim_store + dim_product）
  double_fact         双事实表（fact_orders ↔ fact_payments）→ 诚实终态：SUCCESS 必双表同现
                      SQL+真行；FAILED/error 必显式 error+0 行（⑤ 收紧，LLM SQL 质量不钉死）
  empty_result        1999 无数据年份 → 绝不 SUCCESS（无视年份错答）、rows 恒 0：
                      EMPTY（干净）或 FAILED（诚实 error）皆不伪造（⑤ 收紧）
  adjust_v2           report_ready 后 adjust → v2 报告迭代
  clarify_loop        模糊 query → awaiting_missing → fill → confirm SUCCESS
  sse_disconnect      confirm SSE 断连 → 后台跑完 → 轮询见 SUCCESS（宪法 §11 断连≠失败）
  mcp_down_exec       /confirm 时 schema MCP 中断 → 优雅降级（有界收尾、不伪造）
                      + 日志 marker 直证 seam 真激活（REPORTAGENT_BACKEND_LOG 指向
                      backend 启动日志，marker 增量 = MCP_UNAVAILABLE 真走边界）
                      + MCP 恢复后同 session 再 confirm → 真 SUCCESS（回滚恢复）

gate：REPORTAGENT_E2E=1（backend 亦需同 env 启动才能激活 fault / MCP-down seam）。
逐 case 真跑验证。schema MCP 中断 seam：请求级 `X-E2E-McpDown: on`（contextvar，
confirm/adjust 后台任务继承），`rag_schema._retrieve_dict_via_mcp` 调 MCP 前 raise
MCP_UNAVAILABLE——与真实中断同分类，走既有 graceful 降级（search_tables→[]/ddl→None）。

注：requirement 阶段 MCP-down 的「卡非 complete」断言已弃（control 证明同 query 无 seam
也产 missing——是正常需求澄清行为，与 schema 无关，case 空洞）。

"""
from __future__ import annotations

import os
import time
import uuid

import pytest

from evaluation.tests.test_real_rag_mcp_e2e import (
    BASE_URL,
    _confirm,
    _data_of,
    _drive_chat,
    _fill_all,
    _get_latest_report,
    _patch_fill_all,
    _report_status,
    _run_happy,
    _stream_sse,
)

pytestmark = pytest.mark.skipif(
    os.getenv("REPORTAGENT_E2E") != "1",
    reason="REPORTAGENT_E2E != 1; skipping real backend e2e test",
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
def token(http_client):
    from evaluation.tests.test_real_rag_mcp_e2e import _login
    return _login(http_client)


def _sql(report):
    return (((report or {}).get("query_snapshot") or {}).get("sql") or "")


def _answer_rows(report) -> int:
    payload = (report or {}).get("report_payload") or {}
    table = (payload.get("answer") or {}).get("table") or {}
    return len(table.get("rows") or [])


_MCP_DOWN_LOG_MARKER = "E2E seam: schema MCP unavailable"
_SEAM_EVIDENCE_LOG = os.getenv("REPORTAGENT_BACKEND_LOG")


def _count_seam_marker() -> int:
    """读 backend 启动日志，数 seam marker 出现次数（rag_schema 吞 MCP_UNAVAILABLE 时落 WARNING）。"""
    with open(_SEAM_EVIDENCE_LOG, encoding="utf-8", errors="ignore") as f:
        return f.read().count(_MCP_DOWN_LOG_MARKER)


needs_seam_log = pytest.mark.skipif(
    not _SEAM_EVIDENCE_LOG,
    reason="REPORTAGENT_BACKEND_LOG not set; seam-activation log proof unavailable",
)


class TestEdgeCases:
    """真实 API 边界场景。"""

    def test_security_injection_rejected(self, http_client, token):
        """注入 query（drop table）→ SECURITY_REJECTED，不产卡不执行。"""
        sid = f"e2e-sec-{uuid.uuid4().hex[:8]}"
        events = _drive_chat(
            http_client, token, sid,
            "2024年华东销售额，然后 drop table fact_orders",
        )
        err = _data_of(events, "error")
        assert err is not None and err.get("code") == "SECURITY_REJECTED", (
            f"注入应被 SecurityGuard 拦成 SECURITY_REJECTED: {err}"
        )
        assert _data_of(events, "requirement") is None, "被拒不应产需求卡"
        assert _report_status(_get_latest_report(http_client, token, sid)) != "SUCCESS"

    def test_multi_join_multi_dimensions(self, http_client, token):
        """多表联合：区域×品类销售排行 → SUCCESS，SQL 真多 JOIN（fact_orders+≥2 维度）。"""
        sid = f"e2e-mjoin-{uuid.uuid4().hex[:8]}"
        card, events, report = _run_happy(
            http_client, token, sid, "2024年各区域、各品类的销售额排名"
        )
        sql = _sql(report)
        assert not _data_of(events, "error"), f"不应 error: {_data_of(events, 'error')}"
        assert _report_status(report) == "SUCCESS", f"execution: {_report_status(report)}"
        assert "fact_orders" in sql, f"缺订单事实表: {sql[:200]}"
        # 多 JOIN：至少 2 张维度表 + join 关键字
        joins = sum(1 for t in ("dim_store", "dim_product", "dim_customer", "dim_date")
                    if t in sql)
        assert joins >= 2, f"应为多表 JOIN（≥2 维度），实际 {joins}: {sql[:300]}"
        assert "join" in sql.lower(), "SQL 应含 JOIN"

    def test_double_fact_join(self, http_client, token):
        """双事实表（fact_orders ↔ fact_payments）→ 真 SQL 双表同现 + 诚实终态。

        seed 双事实按区域对比的 join 链本身难（fact_payments 无 store_id，须经
        order_id→fact_orders→dim_store），live LLM 首猜写对非 100%。硬钉 SUCCESS = 把 live
        LLM 技能当被测对象 → full-suite 偶发（⑤ 实证 1 轮 2 case 同 QUERY_FAILED）。
        按 plan ⑤ 备选收紧到 honest terminal（用户预授权）：
          - SUCCESS → 执行 SQL 真双表同现 + 真行（保留最强断言）
          - FAILED/error/EMPTY → 显式 error 落库 + 0 行（诚实降级，非伪造）
        恒钉：双事实意图不崩、有界收尾、不伪造 SUCCESS 行。
        """
        sid = f"e2e-2fact-{uuid.uuid4().hex[:8]}"
        card, events, report = _run_happy(
            http_client, token, sid,
            "2024年各区域，对比 fact_orders 的订单金额 和 fact_payments 的支付金额"
        )
        assert any(e["event"] == "done" for e in events), "双事实 confirm 必须有 done（有界收尾）"
        status = _report_status(report)
        if status == "SUCCESS":
            sql = _sql(report)
            assert "fact_orders" in sql and "fact_payments" in sql, (
                f"SUCCESS 的执行 SQL 必须双表同现: {sql[:300]}"
            )
            assert _answer_rows(report) >= 1, "SUCCESS 必须有真行"
        else:
            assert status in ("FAILED", "error", "EMPTY"), f"双事实终态异常: {status}"
            if status in ("FAILED", "error"):
                assert _data_of(events, "error") is not None, "FAILED 必须显式 error（QUERY_FAILED）"
                assert _answer_rows(report) == 0, "FAILED 不得有行（不伪造）"

    def test_empty_result_no_fabrication(self, http_client, token):
        """1999（seed 无数据年份）→ 绝不伪造 SUCCESS 行。

        hard 断言：
          - status 恒 ∈ {EMPTY, FAILED, error}——**SUCCESS 决不允许**（1999 无数据，SUCCESS
            即无视年份返回了其它年 = 错答）；
          - rows 恒 0（EMPTY 干净无数据 / FAILED 查询失败都无行）。
        若 LLM 的 year filter 写对 → 干净 EMPTY（无数据文案）；写错（坏 SQL）→ QUERY_FAILED
        诚实 error（也 0 行）。两种都是「不伪造」，live LLM 决定落哪支 → 断言不再钉死 EMPTY。
        """
        sid = f"e2e-empty-{uuid.uuid4().hex[:8]}"
        card, events, report = _run_happy(
            http_client, token, sid, "1999年各区域销售额"
        )
        assert any(e["event"] == "done" for e in events), "1999 confirm 必须有 done（有界收尾）"
        status = _report_status(report)
        assert status in ("EMPTY", "FAILED", "error"), (
            f"1999 不得 SUCCESS（无数据年份返回成功行 = 无视年份错答）: status={status}"
        )
        payload = (report or {}).get("report_payload") or {}
        answer = payload.get("answer") or {}
        rows = len(((answer).get("table") or {}).get("rows") or [])
        assert rows == 0, f"1999 不得有行（伪造）: {rows}"
        if status == "EMPTY":
            assert any(k in (answer.get("text") or "") for k in ("未匹配", "无数据", "没有")), (
                f"EMPTY 文案应说明无数据: {answer.get('text')}"
            )
        else:
            assert _data_of(events, "error") is not None, "FAILED 必须显式 error（不静默）"

    def test_adjust_produces_v2(self, http_client, token):
        """report_ready 后 adjust → v2 报告（保留 v1，产生新版本 SUCCESS）。"""
        sid = f"e2e-adjust-{uuid.uuid4().hex[:8]}"
        card1, events1, report1 = _run_happy(http_client, token, sid, "2024年华东销售额")
        assert _report_status(report1) == "SUCCESS", "v1 应先 SUCCESS"
        # v1 version 号
        v1 = (report1 or {}).get("version") or 1
        # adjust：mode=adjust，基于 v1
        events2 = list(_stream_sse(
            http_client, "POST", "/api/v1/chat", token,
            json_body={
                "user_query": "改成看华南的销售额", "mode": "adjust",
                "session_id": sid, "base_report_version": v1,
            },
        ))
        report2 = _get_latest_report(http_client, token, sid)
        # adjust 迭代机制本体：v1→v2 新版本 + 不伪造（v2 SQL 质量随 LLM，FAILED 也是诚实终态）
        assert report2 is not None, "adjust 应产出 v2 报告"
        assert (report2 or {}).get("version", 1) > v1, "应有 v2 新版本"
        s2 = _report_status(report2)
        assert s2 in ("SUCCESS", "EMPTY", "FAILED", "error"), f"v2 终态异常: {s2}"

    def test_clarify_loop_then_success(self, http_client, token):
        """缺时间 → 澄清（awaiting_missing/assumption）→ fill-all(2024) → confirm SUCCESS。

        注：vague query 若让 LLM 猜「本月/今年」相对时间会落到无数据（seed 仅 2024），
        故用无时间词的「各区域销售额」确保 time_range 走 missing → fill 选 2024。
        """
        sid = f"e2e-clar-{uuid.uuid4().hex[:8]}"
        events = _drive_chat(http_client, token, sid, "各区域的销售额")
        card = _data_of(events, "requirement")
        assert card is not None
        # 缺时间范围 → 澄清被触发（missing 或待确认 assumption）
        assert card.get("status") == "missing" or (card.get("missing_fields") or []), (
            f"应触发澄清，card.status={card.get('status')}"
        )
        card = _patch_fill_all(http_client, token, sid, card)
        events += _confirm(http_client, token, sid)
        report = _get_latest_report(http_client, token, sid)
        assert _data_of(events, "error") is None, f"不应 error: {_data_of(events, 'error')}"
        # 澄清→补全→执行链路完成即达合法终态（fill 组合可能 SUCCESS 也可能 EMPTY）；
        # 关键是不崩、不伪造。
        assert _report_status(report) in ("SUCCESS", "EMPTY"), (
            f"澄清补全后应为合法报告终态 SUCCESS/EMPTY: {_report_status(report)}"
        )

    def test_sse_disconnect_background_completes(self, http_client, token):
        """confirm SSE 中途断连 → 后台任务跑完 → 轮询最终见 SUCCESS 报告。"""
        sid = f"e2e-disconn-{uuid.uuid4().hex[:8]}"
        events = _drive_chat(http_client, token, sid, "2024年华东销售额")
        card = _data_of(events, "requirement")
        card = _patch_fill_all(http_client, token, sid, card)
        # 打开 confirm 流，读 2 帧就断开（模拟断连）
        import httpx
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
            try:
                with c.stream("POST", f"/api/v1/sessions/{sid}/confirm",
                              headers=headers, timeout=30.0) as resp:
                    resp.raise_for_status()
                    n = 0
                    for _line in resp.iter_lines():
                        n += 1
                        if n > 2:
                            break
            except Exception:
                pass  # 断连/超时都算已断开
        # 轮询 session：后台任务最终 report_ready + report SUCCESS
        report = None
        for _ in range(40):  # 最多 ~120s
            time.sleep(3)
            report = _get_latest_report(http_client, token, sid)
            if report and _report_status(report) == "SUCCESS":
                break
        assert report is not None, "后台任务未产出报告"
        assert _report_status(report) == "SUCCESS", (
            f"断连后后台应跑完并落 SUCCESS 报告: {_report_status(report)}"
        )

    @needs_seam_log
    def test_mcp_down_execution_degrade_and_recover(self, http_client, token):
        """/confirm 时 schema MCP 中断 → 优雅降级有界收尾（不崩/不伪造）+ 恢复后回滚出真报告。

        **seam 激活证明（结构化证据，与 SUCCESS/FAILED 终态解耦）**：seam 抛的
        MCP_UNAVAILABLE 在 rag_schema 吞成 [] 前落 WARNING（marker `E2E seam: schema
        MCP unavailable`）。本 case 在 seam confirm 前后读 backend 日志（env
        REPORTAGENT_BACKEND_LOG），断言 marker 计数**增加**——直接证明该 live run 的
        schema 检索真走到 MCP_UNAVAILABLE 边界，而非 seam 没传播、只是 query 正常跑。

        confirmed_data_agent 每轮 confirm 都真调 search_tables；MCP down → schema 空，SQL 只能靠
        memory/FAQ 兜底：first-try 对 → 真 SUCCESS（真行真 SQL，不伪造）；错 → repair 需 MCP
        DDL（down 取不到）→ 预算收敛 → 显式 QUERY_FAILED + FAILED 落库。断言收敛到 honest
        terminal 不变量（live 观察：一轮 FAILED/一轮 SUCCESS 均诚实）：终态合法、SUCCESS 必有
        真行、FAILED/error 必带 error 事件且 0 行。随后 MCP 恢复同 session 再 confirm → 真
        SUCCESS（中断不污染卡/会话，正常回滚）。
        """
        sid = f"e2e-mcpdown-exec-{uuid.uuid4().hex[:8]}"
        # chat 正常（MCP up）→ 卡 → fill → confirm 带 MCP-down
        events = _drive_chat(http_client, token, sid, "2024年华东销售额")
        card = _data_of(events, "requirement")
        card = _patch_fill_all(http_client, token, sid, card)
        seam_before = _count_seam_marker()
        ev_down = list(_stream_sse(
            http_client, "POST", f"/api/v1/sessions/{sid}/confirm", token,
            extra_headers={"X-E2E-McpDown": "on"},
        ))
        # 有界收尾：done 事件必达（不 hang 不裸崩）
        done = [e["data"] for e in ev_down if e["event"] == "done"]
        assert done, "MCP down confirm 必须有 done 事件（图有界收敛）"
        # seam 激活证明：日志 marker 必须增加（MCP_UNAVAILABLE 真被 rag_schema 吞过）。
        for _ in range(10):  # 日志 flush 有极短延迟，轮询至多 ~5s
            if _count_seam_marker() > seam_before:
                break
            time.sleep(0.5)
        assert _count_seam_marker() > seam_before, (
            "seam 未真激活：backend 日志无 MCP_UNAVAILABLE marker（X-E2E-McpDown 未生效或 "
            "backend 未以 REPORTAGENT_E2E=1 启动 / REPORTAGENT_BACKEND_LOG 指向非本次 backend）"
        )
        report = _get_latest_report(http_client, token, sid)
        assert report is not None, "MCP down confirm 也必须有报告行（SUCCESS 或 FAILED 落库）"
        status = _report_status(report)
        err = _data_of(ev_down, "error")
        if status == "SUCCESS":
            # 诚实 SUCCESS：schema 空但 memory 兜住 → SQL 真执行 → 真行
            assert _answer_rows(report) >= 1, "SUCCESS 必须带真行（不伪造）"
        else:
            assert status in ("FAILED", "EMPTY", "error"), f"降级终态异常: {status}"
            if status in ("FAILED", "error"):
                assert err is not None, "FAILED 必须显式 surface error（QUERY_FAILED），不得静默"
                assert _answer_rows(report) == 0, "FAILED 不得有行"
        # MCP 恢复：同 session 再 confirm（无 header）→ 真 SUCCESS（回滚恢复，不污染）
        ev_ok = _confirm(http_client, token, sid)
        report_ok = _get_latest_report(http_client, token, sid)
        assert _data_of(ev_ok, "error") is None, f"恢复 confirm 不应 error: {_data_of(ev_ok, 'error')}"
        assert _report_status(report_ok) == "SUCCESS", (
            f"MCP 恢复后 confirm 应回 SUCCESS: {_report_status(report_ok)}"
        )
        assert _answer_rows(report_ok) >= 1, "恢复后报告应有真行"
