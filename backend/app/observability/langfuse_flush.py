"""tracer → Langfuse 转换器。

把 in-memory spans / llm_calls / prompt_versions / decisions 转成 Langfuse
observation 调用（langfuse SDK v4：OTel-based `start_as_current_observation`，
自定义 trace_id 走 `trace_context`，与 PG sink 同 id 可双向关联）。
PII 在入参前过 redact()；Langfuse SDK 失败 best-effort 不抛。
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


async def flush_to_langfuse(
    tracer: "Tracer",
    langfuse_config: LangfuseConfig | None = None,
) -> None:
    """把 tracer 的 spans/llm_calls/prompt_versions/decisions 转 Langfuse。Best-effort。"""
    cfg = langfuse_config or LangfuseConfig()
    if not cfg.enabled:
        return

    langfuse = get_langfuse_client()
    if langfuse is None:
        return

    try:
        # 根 observation：带 trace_context 钉住 trace_id（与 PG trace_id 一致可关联）。
        with langfuse.start_as_current_observation(
            name=_ROOT_NAME,
            trace_context={"trace_id": tracer.trace_id},
            input=redact({"user_query": tracer.user_query}) if tracer.user_query else None,
        ):
            # LLM calls → generation observations（当前 observation 下自动成为子节点）。
            for llm_call in tracer._llm_calls:
                with langfuse.start_as_current_observation(
                    name="llm_call",
                    as_type="generation",
                    model=llm_call.model,
                    usage_details={
                        "input": llm_call.prompt_tokens,
                        "output": llm_call.completion_tokens,
                    },
                ):
                    # v4 自动记录 latency；无额外 body。
                    pass

            # Agent/tool spans → span observations，profile 数据带 prompt version metadata。
            pv_by_span: dict[str, dict] = {
                pv["span_id"]: pv for pv in tracer._prompt_versions if pv.get("span_id")
            }
            for span in tracer._spans:
                pv = pv_by_span.get(getattr(span, "span_id", None))
                with langfuse.start_as_current_observation(
                    name=span.span_name,
                    input=redact(span.input) if getattr(span, "input", None) else None,
                    metadata={
                        "prompt_name": pv.get("name"),
                        "prompt_version": pv.get("version"),
                    } if pv else None,
                ):
                    pass

            # 未挂 span 的 prompt versions / P8 D5 decisions → root metadata。
            for pv in tracer._prompt_versions:
                if not pv.get("span_id"):
                    langfuse.update_current_span(
                        metadata={"prompt_name": pv.get("name"), "prompt_version": pv.get("version")},
                    )
            for decision in tracer._decisions:
                langfuse.update_current_span(metadata={"decision": decision})

        # SDK 是 sync HTTP，经 to_thread 不阻塞事件循环；wait_for 兜底超时。
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