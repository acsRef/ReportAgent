"""tracer → Langfuse 转换器。

把 in-memory spans / llm_calls / prompt_versions / decisions 转成 Langfuse
observation 调用（langfuse SDK v4：OTel-based `start_as_current_observation`，
自定义 trace_id 走 `trace_context`，与 PG sink 同 id 可双向关联）。
PII 在入参前过 redact()；Langfuse SDK 失败 best-effort 不抛。

P13 Review 后的拓扑：
  report_agent_run (root)
  ├── llm_call (无 span_id 兜底挂 root)
  ├── <traced_node span>            # nested via OTel context
  │   ├── llm_call (matched by span_id)  ← generation
  │   └── metadata: decisions / prompt_version
  └── llm_call (匹配的 span 不存在的兜底)
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.observability.langfuse_client import get_langfuse_client
from app.observability.langfuse_config import LangfuseConfig
from app.observability.redaction import redact

if TYPE_CHECKING:
    from app.infra.trace.sdk import Tracer

logger = logging.getLogger(__name__)

# 根 observation 名：Langfuse UI 里一次请求一条 trace，trace_id == tracer.trace_id。
_ROOT_NAME = "report_agent_run"


def _langfuse_trace_id(trace_id: str) -> str:
    """把本仓库 UUID trace_id 转成 Langfuse 接受的 32 位小写 hex。

    Langfuse v4（OTel 内核）要求 trace_id 形如 `[0-9a-f]{32}`，带连字符的
    UUID 会触发 "invalid literal for int() with base 16" 并被 SDK 拒收。
    仅当是 36 位带连字符 UUID 时去连字符 + 小写（仍 32 hex，与 PG 一一可逆）；
    其它 id 原样透传。
    """
    if "-" in trace_id and len(trace_id) == 36:
        return trace_id.replace("-", "").lower()
    return trace_id


def _llm_call_obs(langfuse: Any, llm: Any):
    """包一层 LLM call generation（model + usage + latency_ms + input/output 进 observation）。"""
    return langfuse.start_as_current_observation(
        name="llm_call",
        as_type="generation",
        model=getattr(llm, "model", None),
        usage_details={
            "input": getattr(llm, "prompt_tokens", 0),
            "output": getattr(llm, "completion_tokens", 0),
        },
        # adapter 端已 redact 过一次，此处再走一遍防 span 之外的字典/列表深字段
        input=redact(getattr(llm, "input", None)) or None,
        output=redact(getattr(llm, "output", None)) or None,
        metadata={"latency_ms": getattr(llm, "latency_ms", 0)},
    )


async def flush_to_langfuse(
    tracer: "Tracer",
    langfuse_config: LangfuseConfig | None = None,
) -> None:
    """把 tracer 的 spans/llm_calls/prompt_versions/decisions 转 Langfuse。Best-effort。

    按 span_id 建 parent-child 树（Agent → LLM causal attribution）：
    - LLM call 的 span_id 命中 _spans → generation 嵌套在对应 span observation 下
    - 决策的 span_id 命中 → 聚合到 span metadata 的 decisions 列表（防覆盖 + redact）
    - prompt version 同上
    - 未命中 / 空 span_id → 兜底挂 root（aggregated）
    """
    cfg = langfuse_config or LangfuseConfig()
    if not cfg.enabled:
        return

    langfuse = get_langfuse_client()
    if langfuse is None:
        return

    try:
        # 按 span_id 分组（空串表示无归属，兜底挂 root）
        llm_by_span: dict[str, list] = {}
        dec_by_span: dict[str, list] = {}
        pv_by_span: dict[str, list] = {}
        for llm in tracer._llm_calls:
            sid = getattr(llm, "span_id", None) or ""
            llm_by_span.setdefault(sid, []).append(llm)
        for d in tracer._decisions:
            sid = d.get("span_id") or ""
            dec_by_span.setdefault(sid, []).append(d)
        for pv in tracer._prompt_versions:
            sid = pv.get("span_id") or ""
            pv_by_span.setdefault(sid, []).append(pv)

        # 根 observation：trace_context 钉住 trace_id，metadata 保留原始 UUID 反查
        with langfuse.start_as_current_observation(
            name=_ROOT_NAME,
            trace_context={"trace_id": _langfuse_trace_id(tracer.trace_id)},
            input=redact({"user_query": tracer.user_query}) if tracer.user_query else None,
            metadata={"pg_trace_id": tracer.trace_id},
        ):
            # 无匹配 span 的 LLM calls → 兜底挂 root
            for llm in llm_by_span.pop("", []):
                with _llm_call_obs(langfuse, llm):
                    pass

            # 每个 traced_node span → observation；同 span 的 decisions / pv 聚合到 metadata
            for span in tracer._spans:
                span_md: dict[str, Any] = {}
                span_decisions = dec_by_span.pop(span.span_id, [])
                if span_decisions:
                    span_md["decisions"] = [redact(d) for d in span_decisions]
                span_pvs = pv_by_span.pop(span.span_id, [])
                if span_pvs:
                    span_md["prompt_version"] = {
                        "name": span_pvs[0].get("name"),
                        "version": span_pvs[0].get("version"),
                    }
                with langfuse.start_as_current_observation(
                    name=span.span_name,
                    input=redact(span.input) if getattr(span, "input", None) else None,
                    metadata=span_md or None,
                ):
                    # 嵌套 LLM generations：沿 OTel context 自动挂到当前 span 下
                    for llm in llm_by_span.pop(span.span_id, []):
                        with _llm_call_obs(langfuse, llm):
                            pass

            # 兜底：未命中 span 的 decision / prompt version / llm_call → root
            extra_md: dict[str, Any] = {}
            remain_dec: list = []
            for ds in dec_by_span.values():
                remain_dec.extend(ds)
            if remain_dec:
                extra_md["decisions"] = [redact(d) for d in remain_dec]
            remain_pv: list = []
            for pvs in pv_by_span.values():
                remain_pv.extend(pvs)
            if remain_pv:
                extra_md["prompt_versions"] = [
                    {"name": pv.get("name"), "version": pv.get("version")}
                    for pv in remain_pv
                ]
            # 兜底 llm：对应 span 不存在的 LLM 调用作为 root generation
            remain_llm: list = []
            for lms in llm_by_span.values():
                remain_llm.extend(lms)
            for llm in remain_llm:
                with _llm_call_obs(langfuse, llm):
                    pass
            if extra_md:
                langfuse.update_current_span(metadata=extra_md)

        # SDK 是 sync HTTP，经 to_thread 不阻塞事件循环；wait_for 兜底超时
        try:
            await asyncio.wait_for(
                asyncio.to_thread(langfuse.flush),
                timeout=cfg.flush_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "langfuse flush timed out after %ss for trace_id=%s",
                cfg.flush_timeout, tracer.trace_id,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort：observability 失败不阻塞主流程
        logger.warning(
            "langfuse flush failed for trace_id=%s: %s", tracer.trace_id, exc,
        )