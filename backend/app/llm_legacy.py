from __future__ import annotations

import os
import re
import time
import logging

from langchain_openai import ChatOpenAI

from app.llm_resilience import invoke_with_retry

_LLM_CONFIG = {
    "model": os.getenv("LLM_MODEL", "MiniMax-M2.7-highspeed"),
    "api_key": os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY"),
    "base_url": os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1"),
    "temperature": 0.1,
    "max_retries": 0,
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


# C-8: 同一次 call_llm 链路里工具描述块被反复重拼（遍历整个注册表）。
# 每个调用方的白名单是固定的，渲染结果可按白名单缓存。注册表在进程内
# 不变（register_all_tools 幂等），故缓存键只用白名单集合即可。
_TOOLS_BLOCK_CACHE: dict[frozenset, str] = {}


def _format_tools_for_prompt(whitelist: set[str] | None = None) -> str:
    """把注册表工具渲染进模型 prompt —— 输出完整中文描述，不截断。

    工具描述是模型选择工具的主要依据：五要素描述（用途/输入/输出/
    适用/「不要用来 → 替代工具」边界）必须完整保留。只渲染首行会
    把描述退化成简略版，导致相近工具之间误选率显著升高。

    whitelist：要渲染的工具名集合，缺省用意图阶段的分析工具白名单。
    注册表不可用时降级返回空字符串。
    """
    try:
        from app.tools import register_all_tools
        register_all_tools()
    except Exception as exc:  # Detail D: 不再静默——注册失败会让工具块缺失
        logging.getLogger(__name__).warning("register_all_tools failed: %s", exc)

    allowed = whitelist if whitelist is not None else _INTENT_TOOL_WHITELIST
    cache_key = frozenset(allowed)
    cached = _TOOLS_BLOCK_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        from app.tools.registry import registry
        blocks = []
        # all_tools() 按注册顺序返回 dict[name, ToolMetadata]
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
    except Exception as exc:  # 极早期导入时注册表尚未初始化
        logger = logging.getLogger(__name__)
        logger.warning("registry unavailable for _format_tools_for_prompt: %s", exc)
        return ""


def get_chat_llm(**kwargs) -> ChatOpenAI:
    """DEPRECATED：保留为 thin wrapper 转 `app.llm.get_chat_llm`（fail-closed 收口）。

    历史上这是 `ChatOpenAI` 直构造的旁路；plan 2026-08-31-p12-review-prep.md Fix 1 后
    主入口 `app.llm.get_chat_llm` 已在 `LLM_PROVIDER=mock` 时抛 NotImplementedError，
    本函数必须同样 fail-closed。新代码不应再 import 此函数。
    """
    import warnings

    from app.llm import get_chat_llm as _main

    warnings.warn(
        "app.llm_legacy.get_chat_llm is deprecated, use app.llm.get_chat_llm（mock 模式抛 NotImplementedError）",
        DeprecationWarning,
        stacklevel=2,
    )
    return _main(**kwargs)


def call_llm(prompt: str | list, **kwargs) -> str:
    import warnings

    warnings.warn("app.llm.call_llm is deprecated, use app.llm.get_llm_adapter().generate", DeprecationWarning, stacklevel=2)
    from app.llm import get_llm_adapter

    return get_llm_adapter().generate(prompt, **kwargs)
