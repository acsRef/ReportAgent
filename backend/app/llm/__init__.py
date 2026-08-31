from __future__ import annotations

import os

from app.llm.adapter import LLMAdapter, SchemaValidationError, StructuredParseError, strip_think_tags
from app.llm.config import LLMConfig
from app.llm.mock import MockLLMAdapter, MockLLMMiss

_adapter: LLMAdapter | None = None


def get_llm_adapter() -> LLMAdapter:
    """P12 D3 env switch：`LLM_PROVIDER=mock` → MockLLMAdapter（Contract E2E 离真实 key）。

    默认（含 unset）→ 真实 LLMAdapter。mock 模式缺少 LLM_MOCK_DIR/LLM_MOCK_CASE 时
    from_env 抛 MockLLMMiss 明确失败，不允许静默兜底。
    """
    global _adapter
    if os.getenv("LLM_PROVIDER") == "mock":
        if not isinstance(_adapter, MockLLMAdapter):
            _adapter = MockLLMAdapter.from_env()
        return _adapter
    if not isinstance(_adapter, LLMAdapter):
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

    warnings.warn("app.llm.call_llm is deprecated, use app.llm.get_llm_adapter().generate", DeprecationWarning, stacklevel=2)
    return get_llm_adapter().generate(prompt, **kwargs)


def get_chat_llm(**kwargs):
    from langchain_openai import ChatOpenAI

    base = LLMConfig().to_chat_kwargs()
    base.update(kwargs)
    return ChatOpenAI(**base)


__all__ = ["LLMAdapter", "LLMConfig", "SchemaValidationError", "StructuredParseError", "get_llm_adapter", "generate", "generate_structured", "strip_think_tags", "_format_tools_for_prompt", "_INTENT_TOOL_WHITELIST", "call_llm", "get_chat_llm", "MockLLMAdapter", "MockLLMMiss"]
