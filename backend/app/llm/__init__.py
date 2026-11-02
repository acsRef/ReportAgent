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

_TOOLS_BLOCK_CACHE: str | None = None


def _format_tools_for_prompt() -> str:
    """渲染可被端到端分发的 5 个分析工具清单，供 intent / report prompt 注入。

    只保留 `_INTENT_TOOL_WHITELIST` 里的工具——plan / report 阶段能调的就这 5 个。
    2026-11-02：删掉一直没人传的 `whitelist` 参数（两处 caller 都不传；它只是
    让缓存多一个 key，对应测试 `test_whitelist_parameter_filters` 同步删除）。
    """
    global _TOOLS_BLOCK_CACHE
    if _TOOLS_BLOCK_CACHE is not None:
        return _TOOLS_BLOCK_CACHE
    import logging

    try:
        from app.tools import register_all_tools

        register_all_tools()
    except Exception as exc:
        logging.getLogger(__name__).warning("register_all_tools failed: %s", exc)
    try:
        from app.tools.registry import registry

        blocks = []
        for name, meta in registry.all_tools().items():
            if name not in _INTENT_TOOL_WHITELIST:
                continue
            desc = (meta.description or "").strip()
            if not desc:
                continue
            blocks.append(f"- {name}: {desc}")
        _TOOLS_BLOCK_CACHE = "\n".join(blocks)
        return _TOOLS_BLOCK_CACHE
    except Exception as exc:
        logging.getLogger(__name__).warning("registry unavailable for _format_tools_for_prompt: %s", exc)
        return ""


def call_llm(prompt: str | list, **kwargs) -> str:
    import warnings

    warnings.warn("app.llm.call_llm is deprecated, use app.llm.get_llm_adapter().generate", DeprecationWarning, stacklevel=2)
    return get_llm_adapter().generate(prompt, **kwargs)


def get_chat_llm(**kwargs):
    """Real-LLM ChatOpenAI 构造（legacy 出口，main path 应走 get_llm_adapter()）。

    mock 模式下禁止直构造 ChatOpenAI —— Contract E2E 必须 100% 经过 get_llm_adapter switch，
    否则任意一行 import 此函数即让真 LLM 泄漏进 Contract 测试，造成"主路径 mock + 旁路真 LLM"
    的半 mock 状态。
    """
    if os.getenv("LLM_PROVIDER") == "mock":
        raise NotImplementedError(
            "LLM_PROVIDER=mock 禁用 get_chat_llm 直构造 ChatOpenAI；"
            "Contract E2E 必须走 get_llm_adapter().generate()。详见 plan 2026-08-31-p12-review-prep.md Fix 1。"
        )
    from langchain_openai import ChatOpenAI

    base = LLMConfig().to_chat_kwargs()
    base.update(kwargs)
    return ChatOpenAI(**base)


__all__ = ["LLMAdapter", "LLMConfig", "SchemaValidationError", "StructuredParseError", "get_llm_adapter", "generate", "generate_structured", "strip_think_tags", "_format_tools_for_prompt", "_INTENT_TOOL_WHITELIST", "call_llm", "MockLLMAdapter", "MockLLMMiss"]
