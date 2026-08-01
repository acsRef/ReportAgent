"""Routes for `/api/v1/observability` — 只读 trace / span / llm_call + 聚合指标。

可观测性运维闭环（见 docs/plans/2026-08-01-observability-ops.md）。
- 全部只读，复用共享 asyncpg 池（TraceRepository 内部 get_pool）。
- 需登录（与其他业务端点一致）。
- 不做 OpenTelemetry / 告警 / 实时流。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.infra.auth.deps import get_current_user
from app.infra.trace.repository import TraceRepository

router = APIRouter(prefix="/api/v1/observability", tags=["observability"])

_repo = TraceRepository()


@router.get("/metrics")
async def get_metrics(user: dict = Depends(get_current_user)) -> dict:
    """聚合运维指标：trace 总量/状态分布/成功率/耗时均值与 P95/LLM 用量。"""
    return await _repo.get_metrics()


@router.get("/traces")
async def list_traces(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    user: dict = Depends(get_current_user),
) -> dict:
    """trace 列表（按 start_time 倒序），支持分页与按 status 过滤。"""
    limit = max(1, min(limit, 200))   # 防一次性拉爆
    offset = max(0, offset)
    traces = await _repo.list_traces(limit=limit, offset=offset, status=status)
    return {"traces": traces, "limit": limit, "offset": offset}


@router.get("/traces/{trace_id}")
async def get_trace_detail(trace_id: str, user: dict = Depends(get_current_user)) -> dict:
    """trace 明细：trace 本体 + span 执行链路 + 关联的 LLM 调用。"""
    trace = await _repo.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="TRACE_NOT_FOUND")
    spans = await _repo.get_spans(trace_id)
    llm_calls = await _repo.get_llm_calls(trace_id)
    return {"trace": trace, "spans": spans, "llm_calls": llm_calls}
