"""调度 + 判定：Case → 实时观测 → 逐条 check → verdict。

**判定分三层**（见 `docs/evaluation/methodology.md`）：

1. `deterministic`：字段/集合/命中等可复算断言，进 pass 率；
2. `gold`：金标（现跑 SQL / 现查 doc_id）比对，进 pass 率；
3. `advisory` / `deferred`：启发式或本轮没覆盖的层——**不进 pass 率**，页面显式标注。

这条区分是平台最要紧的地方：宁可显示「未覆盖」，也不给假 pass。
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from eval_platform import adapters, registry

_LAYER_DET = "deterministic"
_LAYER_GOLD = "gold"
_LAYER_DEFERRED = "deferred"

_STATUS_PASS = "pass"
_STATUS_FAIL = "fail"
_STATUS_SKIP = "deferred"


def _norm(text: Any) -> str:
    """时间/文本归一：去空白、去「年/月/日」尾缀，便于跨写法比较。"""
    raw = str(text or "").strip().lower()
    raw = re.sub(r"\s+", "", raw)
    return raw.replace("年", "").replace("月", "").replace("日", "")


def _term_eq(expected: str, observed: str) -> bool:
    """指标/维度等值比较：去空白、去括号里的单位说明后**要求相等**。

    不能退化成子串：`销售额` 与 `净销售额` 是两个口径，子串匹配会把「口径被换」这种真缺陷判成 pass
    （见 risk-case-library RA-R1）。
    """
    def norm(value: str) -> str:
        text = re.sub(r"\s+", "", str(value or ""))
        return re.split(r"[（(]", text)[0].strip().lower()

    return norm(expected) == norm(observed)


def _contains_term(expected: str, observed_list: list[str]) -> bool:
    return any(_term_eq(expected, got) for got in observed_list)


def _check(name: str, layer: str, status: str, *, expected: Any = None,
           observed: Any = None, detail: str = "") -> dict[str, Any]:
    return {
        "name": name, "layer": layer, "status": status,
        "expected": expected, "observed": observed, "detail": detail,
    }


# --- ReportAgent：需求理解层 -------------------------------------------------


def _judge_requirement(expectation: dict[str, Any], card: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if "status" in expectation:
        checks.append(_check(
            "requirement.status", _LAYER_DET,
            _STATUS_PASS if card.get("status") == expectation["status"] else _STATUS_FAIL,
            expected=expectation["status"], observed=card.get("status"),
        ))
    wanted_metrics = expectation.get("target_metrics_contains") or []
    if wanted_metrics:
        got = [str(m) for m in (card.get("target_metrics") or [])]
        missing = [m for m in wanted_metrics if not _contains_term(m, got)]
        checks.append(_check(
            "requirement.target_metrics_contains", _LAYER_DET,
            _STATUS_PASS if not missing else _STATUS_FAIL,
            expected=wanted_metrics, observed=got,
            detail=f"缺：{missing}" if missing else "",
        ))
    if "time_range_equals" in expectation:
        want = _norm(expectation["time_range_equals"])
        got = _norm(card.get("time_range"))
        checks.append(_check(
            "requirement.time_range_equals", _LAYER_DET,
            _STATUS_PASS if want and (want in got or got in want) else _STATUS_FAIL,
            expected=expectation["time_range_equals"], observed=card.get("time_range"),
        ))
    wanted_scope = expectation.get("scope_contains") or []
    if wanted_scope:
        got = [str(x) for x in (card.get("scope") or [])]
        missing = [s for s in wanted_scope if not _contains_term(s, got)]
        checks.append(_check(
            "requirement.scope_contains", _LAYER_DET,
            _STATUS_PASS if not missing else _STATUS_FAIL,
            expected=wanted_scope, observed=got,
            detail=f"缺：{missing}" if missing else "",
        ))
    wanted_dims = expectation.get("dimensions_contains") or []
    if wanted_dims:
        got = [str(x) for x in (card.get("dimensions") or [])]
        missing = [d for d in wanted_dims if not _contains_term(d, got)]
        checks.append(_check(
            "requirement.dimensions_contains", _LAYER_DET,
            _STATUS_PASS if not missing else _STATUS_FAIL,
            expected=wanted_dims, observed=got,
            detail=f"缺：{missing}" if missing else "",
        ))
    deferred_keys = [k for k in ("execution", "report", "behavior") if k in expectation]
    if deferred_keys:
        checks.append(_check(
            "execution/report/behavior", _LAYER_DEFERRED, _STATUS_SKIP,
            expected=deferred_keys,
            detail="需 confirm + 真 SQL + 报告（20–40s/例），本轮不跑；不算 pass",
        ))
    return checks


async def _run_reportagent(case: registry.Case) -> dict[str, Any]:
    session_id = f"eval-{case.id}-{uuid.uuid4().hex[:8]}"
    call = await adapters.reportagent_requirement(case.query, session_id=session_id)
    card = call.data.get("requirement")
    if not isinstance(card, dict):
        err = call.data.get("error") or {}
        return {
            "verdict": "error",
            "latency_ms": call.latency_ms,
            "checks": [_check(
                "requirement", _LAYER_DET, _STATUS_FAIL,
                observed=err, detail=f"没有产出需求卡：{err.get('message') or err}",
            )],
            "observation": {"error": err, "events": call.raw.get("event_names")},
            "session_id": session_id,
        }
    checks = _judge_requirement(case.expectations.get("requirement") or {}, card)
    return {
        "verdict": _verdict(checks),
        "latency_ms": call.latency_ms,
        "checks": checks,
        "observation": {
            "requirement": {
                "status": card.get("status"),
                "summary": card.get("summary"),
                "target_metrics": card.get("target_metrics"),
                "time_range": card.get("time_range"),
                "granularity": card.get("granularity"),
                "scope": card.get("scope"),
                "dimensions": card.get("dimensions"),
                "detail_level": card.get("detail_level"),
            },
            "events": call.raw.get("event_names"),
        },
        "session_id": session_id,
    }


# --- ragent-py：检索层 -------------------------------------------------------


def _judge_retrieval(case: registry.Case, items: list[dict[str, Any]], *, degraded: bool,
                     top_k: int) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    returned_docs = [str(it.get("document_id") or "") for it in items]
    returned_text = "\n".join(str(it.get("text") or "") for it in items)

    if case.gold_documents:
        hits = [d for d in case.gold_documents if d in returned_docs]
        checks.append(_check(
            f"retrieval.doc_hit@{top_k}", _LAYER_GOLD,
            _STATUS_PASS if hits else _STATUS_FAIL,
            expected=list(case.gold_documents), observed=returned_docs,
            detail=f"命中 {len(hits)}/{len(set(case.gold_documents))} 个金标文档",
        ))
        if len(set(case.gold_documents)) > 1:
            ratio = len(set(hits)) / len(set(case.gold_documents))
            checks.append(_check(
                f"retrieval.doc_recall@{top_k}", _LAYER_GOLD,
                _STATUS_PASS if ratio >= 0.67 else _STATUS_FAIL,
                expected="≥ 0.67", observed=round(ratio, 2),
                detail="跨文档题：至少命中 2/3 金标文档",
            ))

    keywords = [str(e.get("keyword", "")).strip() for e in case.gold_evidence if e.get("keyword")]
    if keywords:
        flat = returned_text.replace(" ", "").replace("\u3000", "")
        hit_kw = [k for k in keywords if k.replace(" ", "") in flat]
        checks.append(_check(
            "retrieval.evidence_keyword_hit", _LAYER_GOLD,
            _STATUS_PASS if hit_kw else _STATUS_FAIL,
            expected=keywords, observed=hit_kw,
            detail="金标证据关键词是否出现在召回文本里",
        ))

    if degraded:
        checks.append(_check(
            "retrieval.degraded", _LAYER_DET, _STATUS_FAIL,
            observed=True, detail="服务降级（embedding/向量检索失败，走了退路），结论不可用",
        ))

    if case.gold_judgment or not case.gold_evidence:
        checks.append(_check(
            "answer.correctness", _LAYER_DEFERRED, _STATUS_SKIP,
            expected="拒答/纠偏/口径类语义判定",
            detail="需生成回答 + 裁判或人工评审；本轮只测检索层，不算 pass",
        ))
    return checks


async def _run_ragent(case: registry.Case, *, top_k: int) -> dict[str, Any]:
    call = await adapters.ragent_retrieve(case.query, top_k=top_k)
    items = call.data.get("items") or []
    checks = _judge_retrieval(case, items, degraded=bool(call.data.get("degraded")), top_k=top_k)
    return {
        "verdict": _verdict(checks),
        "latency_ms": call.latency_ms,
        "checks": checks,
        "observation": {
            "kb_ids": call.data.get("kb_ids"),
            "degraded": call.data.get("degraded"),
            "items": [
                {
                    "document_id": it.get("document_id"),
                    "chunk_id": it.get("chunk_id"),
                    "title": (it.get("title") or "")[:80],
                    "score": round(float(it.get("score") or 0), 4),
                    "preview": (it.get("text") or "")[:160],
                }
                for it in items
            ],
        },
    }


def _verdict(checks: list[dict[str, Any]]) -> str:
    """pass 率只看 deterministic / gold 两层的 fail；全 deferred 记 deferred。"""
    graded = [c for c in checks if c["layer"] in (_LAYER_DET, _LAYER_GOLD)]
    if not graded:
        return "deferred"
    if any(c["status"] == _STATUS_FAIL for c in graded):
        return "fail"
    return "pass"


async def run_case(case_id: str, *, top_k: int = 5) -> dict[str, Any]:
    """按 case 所属项目分派执行，返回可直接落库的结果。"""
    ds = registry.load_dataset()
    case = ds.find(case_id)
    if case is None:
        raise KeyError(f"case not found: {case_id}")
    if case.project == registry.PROJECT_RA:
        result = await _run_reportagent(case)
    elif case.project == registry.PROJECT_RAG:
        result = await _run_ragent(case, top_k=top_k)
    else:  # pragma: no cover - 目前只有两个项目
        raise KeyError(f"unknown project: {case.project}")
    result.update({
        "case_id": case.id,
        "project": case.project,
        "title": case.title,
        "category": case.category,
        "tier": case.tier,
        "gold_source": case.gold_source,
        "coverage": {
            "graded": [c["name"] for c in result["checks"] if c["layer"] in (_LAYER_DET, _LAYER_GOLD)],
            "deferred": [c["name"] for c in result["checks"] if c["layer"] == _LAYER_DEFERRED],
        },
    })
    return result
