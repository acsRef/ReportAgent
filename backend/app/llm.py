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
    config = {**_LLM_CONFIG, **kwargs}
    return ChatOpenAI(**config)


def call_llm(prompt: str | list, **kwargs) -> str:
    llm = get_chat_llm(**kwargs)
    start = time.monotonic()
    resp = llm.invoke(prompt)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    # Reasoning models (DeepSeek, MiniMax-M2.7-highspeed) often put
    # the final JSON answer inside a <think> block. We keep the FULL
    # response (think + answer) and let  extract the
    # JSON object via its first-{""}-last-}"} scan. Stripping <think>
    # would throw away the only JSON the model produced.
    text = (resp.content or "").strip()
    if not text:
        import logging
        logging.getLogger(__name__).warning(
            "call_llm: empty content from model. raw response type=%s repr=%r",
            type(resp).__name__, str(resp)[:2000],
        )

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
        # C-4: 只记到「当前」请求的 tracer。此前遍历 _local.values() 把一次
        # 调用记到所有在途 tracer，造成 span attribution 跨请求错位。
        from app.infra.trace.sdk import current_tracer
        tracer = current_tracer()
        if tracer is not None:
            tracer.add_llm_call(model, prompt_tokens, completion_tokens, elapsed_ms)
    except Exception:
        pass

    return text.strip()
