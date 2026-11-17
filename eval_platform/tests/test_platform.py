"""平台自测（不依赖两个被评测服务：适配层只在 live 才用）。"""
from __future__ import annotations

import pytest

from eval_platform import adapters, registry, runners

pytestmark = pytest.mark.contracts


# --- 资产加载 ---------------------------------------------------------------


def test_dataset_loads_both_projects() -> None:
    ds = registry.load_dataset()
    ra = ds.by_project(registry.PROJECT_RA)
    rag = ds.by_project(registry.PROJECT_RAG)
    assert len(ra) >= 20, f"ReportAgent baseline 应该 ≥20 例，实际 {len(ra)}"
    assert len(rag) == 65, f"ragent 测试集应该 65 题，实际 {len(rag)}"
    assert len(ds.assets) >= 4
    assert len(ds.risks) >= 5


def test_ragent_gold_metadata_kept() -> None:
    ds = registry.load_dataset()
    rag = ds.by_project(registry.PROJECT_RAG)
    assert sum(1 for c in rag if c.gold_documents) >= 60
    human = [c for c in rag if c.gold_source.startswith("human_verified")]
    assert human, "至少要保留人工核验标记，否则证据档位无从区分"
    assert all(c.tier == registry.TIER_HUMAN for c in human)
    unverified = [c for c in rag if not c.gold_source.startswith("human_verified")]
    assert all(c.tier == registry.TIER_REFERENCE for c in unverified)


def test_risk_library_covers_five_classes() -> None:
    risks = registry.load_dataset().risks
    kinds = {r["risk"] for r in risks}
    # 误答 / 错误执行 / 不可追溯 / 幻觉 / 越权 五类至少各一条（语义相近的归并看前缀）
    for needle in ("误答", "错误执行", "不可追溯", "幻觉", "越权"):
        assert any(needle in k for k in kinds), f"缺少风险类别：{needle}（现有 {sorted(kinds)}）"


def test_summary_numbers_are_consistent() -> None:
    s = registry.summary()
    assert s["cases"][registry.PROJECT_RAG] == 65
    assert s["assets"] >= 4
    assert s["human_verified"] >= 1
    assert "versions" in s


# --- 判定逻辑 ---------------------------------------------------------------


def _card(**kw):
    base = {
        "status": "complete", "summary": "2024 年华东销售额", "target_metrics": ["销售额"],
        "time_range": "2024年", "granularity": "month", "scope": ["华东"],
        "dimensions": ["月份"], "detail_level": None,
    }
    base.update(kw)
    return base


class TestRequirementJudge:
    def test_all_fields_pass(self) -> None:
        checks = runners._judge_requirement(
            {"status": "complete", "target_metrics_contains": ["销售额"],
             "time_range_equals": "2024"},
            _card(),
        )
        graded = [c for c in checks if c["layer"] in ("deterministic", "gold")]
        assert graded and all(c["status"] == "pass" for c in graded)

    def test_time_range_normalized(self) -> None:
        """`2024年` 与 `2024` 应视为同一个时间范围（归一后比较）。"""
        checks = runners._judge_requirement({"time_range_equals": "2024年"}, _card(time_range="2024"))
        assert checks[0]["status"] == "pass"

    def test_metric_missing_fails(self) -> None:
        checks = runners._judge_requirement(
            {"target_metrics_contains": ["净销售额"]}, _card(target_metrics=["销售额"]),
        )
        assert checks[0]["status"] == "fail"
        assert "净销售额" in checks[0]["expected"]

    def test_execution_expectation_is_deferred_not_pass(self) -> None:
        checks = runners._judge_requirement({"status": "complete", "execution": {"verdict": "SUCCESS"}}, _card())
        deferred = [c for c in checks if c["layer"] == "deferred"]
        assert deferred, "执行层没跑就该显式 deferred"
        assert deferred[0]["status"] == "deferred"


class TestRetrievalJudge:
    CASE = registry.Case(
        id="RAG-Q01", project=registry.PROJECT_RAG, suite="t", title="t", category="A",
        query="q", gold_documents=("doc-a", "doc-b"),
        gold_evidence=({"keyword": "732.22"},), gold_source="human_verified_2026-08-23",
    )

    def test_doc_hit_and_keyword(self) -> None:
        items = [
            {"document_id": "doc-a", "text": "营业收入 732.22 亿元", "score": 0.8},
            {"document_id": "doc-x", "text": "无关", "score": 0.2},
        ]
        checks = runners._judge_retrieval(self.CASE, items, degraded=False, top_k=5)
        names = {c["name"]: c["status"] for c in checks}
        assert names["retrieval.doc_hit@5"] == "pass"
        assert names["retrieval.doc_recall@5"] == "fail"   # 只中 1/2
        assert names["retrieval.evidence_keyword_hit"] == "pass"

    def test_miss_and_degraded(self) -> None:
        items = [{"document_id": "doc-z", "text": "无关", "score": 0.1}]
        checks = runners._judge_retrieval(self.CASE, items, degraded=True, top_k=5)
        names = {c["name"]: c["status"] for c in checks}
        assert names["retrieval.doc_hit@5"] == "fail"
        assert names["retrieval.degraded"] == "fail"


class TestVerdict:
    def test_deferred_only_is_not_pass(self) -> None:
        checks = [runners._check("x", "deferred", "deferred")]
        assert runners._verdict(checks) == "deferred"

    def test_any_graded_fail_wins(self) -> None:
        checks = [runners._check("a", "deterministic", "pass"), runners._check("b", "gold", "fail")]
        assert runners._verdict(checks) == "fail"

    def test_pass_requires_all_graded_pass(self) -> None:
        checks = [runners._check("a", "deterministic", "pass"), runners._check("b", "deferred", "deferred")]
        assert runners._verdict(checks) == "pass"


# --- 适配层 -----------------------------------------------------------------


def test_parse_sse_frames() -> None:
    wire = (
        'event: phase\ndata: {"phase": "parsing"}\n\n'
        'event: requirement\ndata: {"status": "complete"}\n\n'
        'event: done\ndata: \n\n'
    )
    events = adapters._parse_sse_events(wire)
    assert [e["event"] for e in events] == ["phase", "requirement", "done"]
    assert events[1]["data"]["status"] == "complete"


def test_service_unavailable_is_typed() -> None:
    assert issubclass(adapters.ServiceUnavailable, RuntimeError)


# --- 清理安全（2026-11-17 真事故回归） ---------------------------------------


def test_ragent_docs_filters_client_side(monkeypatch) -> None:
    """`GET /documents` 不接受 kb_id，必须客户端过滤——否则清理会把全库删掉。"""
    import asyncio

    from eval_platform import adapters

    payload = [
        {"document_id": "a", "kb_id": "kb-temp"},
        {"document_id": "b", "kb_id": "kb-prod"},
    ]

    class _Resp:
        status_code = 200

        def json(self):
            return payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            return _Resp()

    async def fake_login(force: bool = False):
        return "tok"

    async def fake_rag_client(token: str, timeout: float = 0):
        return _Client()

    monkeypatch.setattr(adapters, "ragent_login", fake_login)
    monkeypatch.setattr(adapters, "_rag_client", fake_rag_client)

    docs = asyncio.run(adapters.ragent_docs("kb-temp"))
    assert [d["document_id"] for d in docs] == ["a"], "必须只返回本库文档"


# --- 场景测试（不真的打服务：只测注册表与判定辅助） ------------------------


class TestScenarios:
    def test_registry_covers_incremental_delete_isolation(self) -> None:
        from eval_platform import scenarios

        ids = {s["id"] for s in scenarios.list_scenarios()}
        assert ids == {"rag-incremental-upsert", "rag-delete-no-dirty-recall", "rag-kb-isolation"}
        assert all(s["steps"] for s in scenarios.list_scenarios())

    def test_hit_helper(self) -> None:
        from eval_platform import scenarios

        items = [{"document_id": "d1", "text": "关键标识：ALPHA12"}]
        assert scenarios._hit(items, "ALPHA12", "d1") == (True, True)
        assert scenarios._hit(items, "ALPHA12", "d2") == (False, True)
        assert scenarios._hit(items, "BETA99") == (True, False)

    def test_index_problem_distinguishes_timeout_from_failure(self) -> None:
        """入库超时是**工具上限**，不是产品失败——判定必须分开，否则会误报。"""
        from eval_platform import scenarios

        assert scenarios._index_problem({"status": "indexed", "chunk_count": 1})[0] == "pass"
        assert scenarios._index_problem({"status": "failed"})[0] == "fail"
        assert scenarios._index_problem({"status": "timeout"})[0] == "timeout"

    def test_run_verdict_timeout_is_error_not_fail(self) -> None:
        from eval_platform import scenarios

        run = scenarios._Run()
        run.step("入库", False, "超时", status="timeout")
        run.step("可召回", False, "跳过", status="skipped")
        assert run.verdict == "error"


# --- HTTP 面（TestClient，不触发 live 调用） --------------------------------


def test_http_surface() -> None:
    from fastapi.testclient import TestClient

    from eval_platform.app import app

    client = TestClient(app)
    cases = client.get("/api/cases?project=ragent&limit=5").json()
    assert len(cases) == 5 and all(c["project"] == "ragent" for c in cases)

    risk = client.get("/api/risk").json()
    assert any("幻觉" in r["risk"] for r in risk)

    docs = client.get("/api/docs").json()
    assert {d["name"] for d in docs} >= {"methodology", "evidence-weights"}

    md = client.get("/api/docs/methodology").text
    assert "判定标准" in md

    assert client.get("/api/cases/nope").status_code == 404

    scen = client.get("/api/scenarios").json()
    assert {s["id"] for s in scen} >= {"rag-incremental-upsert"}
