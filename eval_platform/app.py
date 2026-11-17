"""评测平台 HTTP 入口（端口 8200）。

页面里每一块都对应 JD 的一条要求：

| 页面 | JD 条款 |
|---|---|
| 总览     | 被测对象 / 能力版本 / 覆盖 |
| 实时测试 | 目标·指标·用例·判定标准；可追溯记录 |
| 资产     | 来源 / 覆盖 / 版本 / 复用说明 |
| 风险库   | 误答·幻觉·越权·错误执行·不可追溯 |
| 历史     | 证据状态 + 运行环境 + 版本 |
| 方法     | 方法论 + 证据分量 + 职责边界 |
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse

from eval_platform import adapters, registry, runners, scenarios, store

DOCS_DIR = Path(os.getenv(
    "EVAL_DOCS", str(registry.RA_REPO / "docs" / "evaluation"),
))
STATIC_DIR = Path(__file__).with_name("static")

DOC_ORDER = ("methodology", "asset-registry", "risk-case-library", "evidence-weights")

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    store.init_db()
    yield


app = FastAPI(title="AI Eval Platform", version="0.1.0", lifespan=_lifespan)


def _env_snapshot() -> dict[str, Any]:
    """运行环境指纹：写进每条记录，回答「这条结论在什么环境成立」。"""
    ds = registry.load_dataset()
    return {
        "ra_base": adapters.RA_BASE,
        "rag_base": adapters.RAG_BASE,
        "ra_repo": str(registry.RA_REPO),
        "rag_repo": str(registry.RAG_REPO),
        "versions": registry.summary()["versions"],
        "assets": [a.id for a in ds.assets],
        "cases_total": len(ds.cases),
    }


@app.get("/api/health")
async def health() -> dict[str, Any]:
    ra = await adapters.reportagent_health()
    rag = await adapters.ragent_health()
    return {"reportagent": ra, "ragent": rag, "platform": "ok"}


@app.get("/api/overview")
async def overview() -> dict[str, Any]:
    data = registry.summary()
    data["run_stats"] = store.stats()
    data["health"] = await health()
    return data


@app.get("/api/assets")
async def assets() -> list[dict[str, Any]]:
    return [a.__dict__ for a in registry.load_dataset().assets]


@app.get("/api/cases")
def cases(
    project: str | None = None,
    tier: str | None = None,
    q: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict[str, Any]]:
    items = registry.load_dataset().cases
    if project:
        items = [c for c in items if c.project == project]
    if tier:
        items = [c for c in items if c.tier == tier]
    if q:
        needle = q.lower()
        items = [
            c for c in items
            if needle in c.id.lower() or needle in c.title.lower() or needle in c.category.lower()
        ]
    return [c.brief() for c in items[:limit]]


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str) -> dict[str, Any]:
    case = registry.load_dataset().find(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    return {
        **case.brief(),
        "expectations": case.expectations,
        "gold_documents": list(case.gold_documents),
        "gold_evidence": list(case.gold_evidence),
    }


@app.post("/api/run")
async def run(case_id: str, top_k: int = Query(default=5, ge=1, le=20)) -> dict[str, Any]:
    """实时跑一条用例。服务不可用时返回 `unavailable`（200），不伪装成 fail。"""
    try:
        result = await runners.run_case(case_id, top_k=top_k)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except adapters.ServiceUnavailable as exc:
        return {
            "case_id": case_id,
            "status": "unavailable",
            "message": str(exc),
            "hint": "被评测服务未启动或凭据不通；历史资产与文档仍可查看",
        }
    env = _env_snapshot()
    run_id = store.save_run(result, env=env)
    return {"status": "ok", "run_id": run_id, "result": {**result, "run_id": run_id}, "env": env}


@app.get("/api/runs")
def runs(limit: int = Query(default=50, ge=1, le=300)) -> list[dict[str, Any]]:
    return store.list_runs(limit)


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    row = store.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="RUN_NOT_FOUND")
    return row


@app.get("/api/scenarios")
def scenario_list() -> list[dict[str, Any]]:
    """场景测试（多步、有状态）：增量更新 / 删除 / 跨库隔离。"""
    return scenarios.list_scenarios()


@app.post("/api/scenarios/{scenario_id}/run")
async def scenario_run(scenario_id: str) -> dict[str, Any]:
    try:
        result = await scenarios.run_scenario(scenario_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except adapters.ServiceUnavailable as exc:
        return {"status": "unavailable", "message": str(exc),
                "hint": "ragent 服务未启动或凭据不通；历史与文档仍可查看"}
    env = _env_snapshot()
    run_id = store.save_run(
        {**result, "case_id": scenario_id, "title": result["name"], "tier": "运行时实测"},
        env=env,
    )
    return {"status": "ok", "run_id": run_id, "result": {**result, "run_id": run_id}, "env": env}


@app.get("/api/risk")
def risk() -> list[dict[str, Any]]:
    return registry.load_dataset().risks


@app.get("/api/docs")
def docs() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for name in DOC_ORDER:
        path = DOCS_DIR / f"{name}.md"
        if path.exists():
            title = path.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
            items.append({"name": name, "title": title})
    return items


@app.get("/api/docs/{name}", response_class=PlainTextResponse)
def doc(name: str) -> str:
    if name not in DOC_ORDER:
        raise HTTPException(status_code=404, detail="DOC_NOT_FOUND")
    path = DOCS_DIR / f"{name}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="DOC_NOT_WRITTEN_YET")
    return path.read_text(encoding="utf-8")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/static/{name}")
def static_file(name: str) -> FileResponse:
    path = STATIC_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    return FileResponse(path)
