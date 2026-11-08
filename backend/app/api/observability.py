"""Routes for `/api/v1/observability` — 只读 trace / span / llm_call + 聚合指标。

可观测性运维闭环（见 docs/plans/2026-08-01-observability-ops.md）。
- 全部只读，复用共享 asyncpg 池（TraceRepository 内部 get_pool）。
- 需登录（与其他业务端点一致）。
- A-3（docs/plans/2026-08-04-agent-security-hardening.md）：全部查询按
  user_id 隔离——span 的 input/output 含 SQL 与查询结果，不能让任何登录
  用户读到全量用户 trace。他人 trace_id → 404 TRACE_NOT_FOUND。
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
    """聚合运维指标：trace 总量/状态分布/成功率/耗时均值与 P95/LLM 用量（仅本人）。"""
    return await _repo.get_metrics(user_id=user["id"])


@router.get("/traces")
async def list_traces(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    session_id: str | None = None,
    user: dict = Depends(get_current_user),
) -> dict:
    """trace 列表（按 start_time 倒序，仅本人），支持分页、status 过滤与按会话过滤。

    2026-11-08（plan trace-inspector D2）：`session_id` 是排查入口——用户手里通常只有
    「刚才那一轮」，不是 trace_id。
    """
    limit = max(1, min(limit, 200))   # 防一次性拉爆
    offset = max(0, offset)
    traces = await _repo.list_traces(
        user_id=user["id"], limit=limit, offset=offset, status=status,
        session_id=session_id,
    )
    return {"traces": traces, "limit": limit, "offset": offset}


def _merge_output_spans(spans: list[dict]) -> list[dict]:
    """把 `xxx_output`（span_type=DATA）的内容合并进父节点的 `output`，并从列表里去掉。

    2026-11-08（plan trace-inspector）：`@traced_node` 的 `_handle_output_span` 会为每个节点
    再开一个 DATA span，把节点产出放在它的 **input** 里。于是页面上点开节点看到
    「输出：空」——排查时最想看的恰恰是它；而且一轮 43 个 span 里一半是这种内部摘要。

    合并后：节点数减半，每个节点都有自己的输出。
    """
    outputs: dict[str, list] = {}
    kept: list[dict] = []
    for span in spans:
        if span.get("span_type") == "DATA" and span.get("parent_span_id"):
            outputs.setdefault(str(span["parent_span_id"]), []).append(span.get("input"))
            continue
        kept.append(span)
    for span in kept:
        extra = outputs.get(str(span.get("span_id")))
        if not extra:
            continue
        merged = extra[0] if len(extra) == 1 else extra
        if span.get("output"):
            merged = [span["output"], merged]
        span["output"] = merged
    return kept


@router.get("/traces/{trace_id}")
async def get_trace_detail(trace_id: str, user: dict = Depends(get_current_user)) -> dict:
    """trace 明细：trace 本体 + span 执行链路 + 关联的 LLM 调用。

    A-3：先查归属——他人 trace 与不存在的 trace 同样 404 TRACE_NOT_FOUND。
    """
    trace = await _repo.get_trace(trace_id, user_id=user["id"])
    if trace is None:
        raise HTTPException(status_code=404, detail="TRACE_NOT_FOUND")
    spans = await _repo.get_spans(trace_id)
    llm_calls = await _repo.get_llm_calls(trace_id)
    return {"trace": trace, "spans": _merge_output_spans(spans), "llm_calls": llm_calls}
