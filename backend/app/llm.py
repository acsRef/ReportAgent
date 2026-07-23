from __future__ import annotations

import os
import re
import time
import logging

from langchain_openai import ChatOpenAI

_LLM_CONFIG = {
    "model": os.getenv("LLM_MODEL", "MiniMax-M2.7-highspeed"),
    "api_key": os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY"),
    "base_url": os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1"),
    "temperature": 0.1,
}

# Whitelist of tool names exposed to the user-facing intent analysis step
# (stage 1 of the 3-stage report-generation UX). The registry is dynamic, so
# we only filter — we don't hardcode descriptions. To add or remove a tool,
# edit this set; everything else (description, schema) flows from the registry.
_INTENT_TOOL_WHITELIST = {
    "group_compare",
    "trend_analysis",
    "detect_anomaly",
    "chart_advisor",
    "insight_analyst",
}


def _format_tools_for_prompt() -> str:
    """Render the available tool list (from the dynamic registry) for stage 1.

    Falls back to an empty list if the registry hasn't been populated yet
    (e.g., during import-time inspection).
    """
    try:
        from app.tools import register_all_tools
        from app.tools.registry import registry
        # Idempotent: `register_all_tools` does dedup by tool name internally.
        register_all_tools()
    except Exception:
        pass

    try:
        from app.tools.registry import registry
        lines = []
        # all_tools() returns dict[name, ToolMetadata]
        all_tools = registry.all_tools()
        for name, meta in all_tools.items():
            if name not in _INTENT_TOOL_WHITELIST:
                continue
            # ToolMetadata.description may be long; take the first sentence-ish.
            desc = (meta.description or "").strip().split("\n", 1)[0]
            lines.append(f"- {name}: {desc}")
        return "\n".join(lines)
    except Exception as exc:  # registry not initialized yet (very early import)
        logger = logging.getLogger(__name__)
        logger.warning("registry unavailable for _format_tools_for_prompt: %s", exc)
        return ""


def get_chat_llm(**kwargs) -> ChatOpenAI:
    config = {**_LLM_CONFIG, **kwargs}
    return ChatOpenAI(**config)


def call_llm(prompt: str | list, **kwargs) -> str:
    llm = get_chat_llm(**kwargs)
    start = time.monotonic()
    resp = llm.invoke(prompt)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    text = resp.content or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # Record LLM call to observability.llm_call
    try:
        model = kwargs.get("model", _LLM_CONFIG.get("model", ""))
        usage = getattr(resp, "usage_metadata", None) or {}
        if isinstance(usage, dict):
            prompt_tokens = usage.get("input_tokens", 0) or 0
            completion_tokens = usage.get("output_tokens", 0) or 0
        else:
            prompt_tokens = getattr(usage, "input_tokens", 0) or 0
            completion_tokens = getattr(usage, "output_tokens", 0) or 0
        from app.infra.trace.sdk import _local as _tracer_local
        for _t in _tracer_local.values():
            _t.add_llm_call(model, prompt_tokens, completion_tokens, elapsed_ms)
    except Exception:
        pass

    return text.strip()
