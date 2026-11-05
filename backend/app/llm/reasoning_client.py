"""推理模型直连客户端：拿 `content` + `reasoning_content`（思维链）。

为什么不用 LangChain 的 ChatOpenAI：实测（2026-11-05，硅基流动）
- 同一句 prompt，直连 `POST /chat/completions` 是 7.4s 并带回 `reasoning_content`；
- 经 ChatOpenAI 走 `extra_body` 传 `enable_thinking` 时，60s 超时、重试两次后整轮失败，
  且 `reasoning_content` 取不到。

Turn Understanding 是「一轮一次」的关键调用，这里直连更可控：超时可单独配置、
思维链能拿到、失败语义明确（返回 None 由调用方回落旧解析）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

from app.llm.config import LLMConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReasoningReply:
    content: str
    reasoning: str
    model: str
    elapsed_ms: int = 0
    # 2026-11-05（plan live-session-issues D1）：思维链是否被**思考预算**截断。
    # provider 用 `usage.completion_tokens_details.reasoning_tokens` 报出思考用量，
    # 顶到 budget 时 CoT 会停在一句中间——不标注的话用户会以为界面坏了。
    reasoning_tokens: int = 0
    truncated: bool = False


def turn_timeout() -> float:
    """Turn Understanding 的超时（默认 90s；推理模型开思考时比普通调用慢）。"""
    try:
        return float(os.getenv("LLM_TURN_TIMEOUT", "90"))
    except (TypeError, ValueError):
        return 90.0


def chat_with_reasoning(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 1200,
    enable_thinking: bool | None = None,
    thinking_budget: int | None = None,
    timeout: float | None = None,
) -> ReasoningReply | None:
    """直连 chat/completions；网络/HTTP 失败返回 None（调用方回落）。"""
    config = LLMConfig()
    chosen = (model or config.turn_model or config.model or "").strip()
    if not chosen:
        return None
    body: dict = {
        "model": chosen,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": config.temperature,
        "stream": False,
    }
    thinking = (
        enable_thinking
        if enable_thinking is not None
        else (os.getenv("TURN_ENABLE_THINKING", "1").strip() not in ("0", "false", "no"))
    )
    if thinking:
        body["enable_thinking"] = True
        if thinking_budget is None:
            try:
                thinking_budget = int(os.getenv("TURN_THINKING_BUDGET", "512"))
            except (TypeError, ValueError):
                thinking_budget = 512
        body["thinking_budget"] = thinking_budget
    url = f"{(config.base_url or '').rstrip('/')}/chat/completions"
    import time

    started = time.monotonic()
    try:
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {config.api_key or ''}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=timeout or turn_timeout(),
        )
    except Exception as exc:  # noqa: BLE001 - 失败由调用方回落
        logger.warning("turn model http failed: %s", exc)
        return None
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if response.status_code != 200:
        logger.warning(
            "turn model http %s: %s", response.status_code, response.text[:200],
        )
        return None
    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("turn model bad json: %s", exc)
        return None
    choices = payload.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if choices else {}
    if not isinstance(message, dict):
        return None

    # 2026-11-05（plan live-session-issues D1）：判断思维链是否被思考预算截断。
    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    try:
        reasoning_tokens = int((details or {}).get("reasoning_tokens") or 0)
    except (TypeError, ValueError):
        reasoning_tokens = 0
    finish_reason = str(choice.get("finish_reason") or "")
    budget = int(thinking_budget or 0)
    truncated = finish_reason == "length" or (
        budget > 0 and reasoning_tokens >= budget - 4
    )

    return ReasoningReply(
        content=str(message.get("content") or ""),
        reasoning=str(message.get("reasoning_content") or ""),
        model=chosen,
        elapsed_ms=elapsed_ms,
        reasoning_tokens=reasoning_tokens,
        truncated=truncated,
    )
