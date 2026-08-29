from __future__ import annotations

from app.llm.adapter import LLMAdapter, strip_think_tags
from app.llm.config import LLMConfig

_adapter: LLMAdapter | None = None


def get_llm_adapter() -> LLMAdapter:
    global _adapter
    if _adapter is None:
        _adapter = LLMAdapter()
    return _adapter


def generate(prompt: str | list, **kwargs) -> str:
    return get_llm_adapter().generate(prompt, **kwargs)


def generate_structured(prompt: str | list, **kwargs) -> dict:
    return get_llm_adapter().generate_structured(prompt, **kwargs)


_INTENT_TOOL_WHITELIST = {
    "group_compare",
    "trend_analysis",
    "detect_anomaly",
    "chart_advisor",
    "insight_analyst",
}

_TOOLS_BLOCK_CACHE: dict[frozenset, str] = {}


def _format_tools_for_prompt(whitelist: set[str] | None = None) -> str:
    import logging

    try:
        from app.tools import register_all_tools

        register_all_tools()
    except Exception as exc:
        logging.getLogger(__name__).warning("register_all_tools failed: %s", exc)
    allowed = whitelist if whitelist is not None else _INTENT_TOOL_WHITELIST
    cache_key = frozenset(allowed)
    cached = _TOOLS_BLOCK_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        from app.tools.registry import registry

        blocks = []
        for name, meta in registry.all_tools().items():
            if name not in allowed:
                continue
            desc = (meta.description or "").strip()
            if not desc:
                continue
            blocks.append(f"- {name}: {desc}")
        result = "\n".join(blocks)
        _TOOLS_BLOCK_CACHE[cache_key] = result
        return result
    except Exception as exc:
        logging.getLogger(__name__).warning("registry unavailable for _format_tools_for_prompt: %s", exc)
        return ""


def call_llm(prompt: str | list, **kwargs) -> str:
    import warnings
    import time

    warnings.warn("app.llm.call_llm is deprecated, use app.llm.get_llm_adapter().generate", DeprecationWarning, stacklevel=2)
    from app.llm_resilience import invoke_with_retry
    from app.llm.adapter import strip_think_tags

    llm = get_chat_llm(**kwargs)
    resp = invoke_with_retry(lambda: llm.invoke(prompt))
    raw = (getattr(resp, "content", "") or "").strip()
    text = strip_think_tags(raw)
    return text.strip() if text else raw.strip()


def get_chat_llm(**kwargs):
    from langchain_openai import ChatOpenAI

    cfg = LLMConfig()
    base = cfg.to_chat_kwargs()
    base.update(kwargs)
    return ChatOpenAI(**base)


__all__ = ["LLMAdapter", "LLMConfig", "get_llm_adapter", "generate", "generate_structured", "strip_think_tags", "_format_tools_for_prompt", "_INTENT_TOOL_WHITELIST", "call_llm"]
