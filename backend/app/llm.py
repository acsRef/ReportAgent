from __future__ import annotations

import os
import re
import time

from langchain_openai import ChatOpenAI

_LLM_CONFIG = {
    "model": os.getenv("LLM_MODEL", "MiniMax-M2.7-highspeed"),
    "api_key": os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY"),
    "base_url": os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1"),
    "temperature": 0.1,
}


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
