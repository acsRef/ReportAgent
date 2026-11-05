"""推理模型直连客户端：拿 `content` + `reasoning_content`（思维链）。

为什么不用 LangChain 的 ChatOpenAI：实测（2026-11-05，硅基流动）
- 同一句 prompt，直连 `POST /chat/completions` 是 7.4s 并带回 `reasoning_content`；
- 经 ChatOpenAI 走 `extra_body` 传 `enable_thinking` 时，60s 超时、重试两次后整轮失败，
  且 `reasoning_content` 取不到。

Turn Understanding 是「一轮一次」的关键调用，这里直连更可控：超时可单独配置、
思维链能拿到、失败语义明确（返回 None 由调用方回落旧解析）。

2026-11-05（plan streaming-reasoning）：
- `on_delta` 传入时走 `stream=True`，边收边回调**累计**文本——用户要「实时看见模型在想什么」；
- `max_tokens` 是**思考 + 回答共享**的总额，默认按预算推算（`TURN_MAX_TOKENS`），
  避免「只调预算把回答挤掉 → JSON 被截断 → 表现为理解失败」。
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from app.llm.config import LLMConfig

logger = logging.getLogger(__name__)

DeltaCallback = Callable[[str, str], None]


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


def resolve_thinking_budget(override: int | None = None) -> int:
    """思考预算（token）：思考阶段最多花多少。"""
    if override is not None:
        return max(1, int(override))
    try:
        return max(1, int(os.getenv("TURN_THINKING_BUDGET", "512")))
    except (TypeError, ValueError):
        return 512


def resolve_max_tokens(budget: int) -> int:
    """总额（思考 + 回答共享）。默认 = 预算 + 900，保证回答那部分不会被预算吃掉。"""
    try:
        configured = int(os.getenv("TURN_MAX_TOKENS", "") or 0)
    except (TypeError, ValueError):
        configured = 0
    if configured > 0:
        return configured
    return max(1200, budget + 900)


def _parse_usage(usage: dict | None, finish_reason: str, budget: int) -> tuple[int, bool]:
    """→ (reasoning_tokens, truncated)。"""
    details = (usage or {}).get("completion_tokens_details") or {}
    try:
        reasoning_tokens = int((details or {}).get("reasoning_tokens") or 0)
    except (TypeError, ValueError):
        reasoning_tokens = 0
    truncated = finish_reason == "length" or (budget > 0 and reasoning_tokens >= budget - 4)
    return reasoning_tokens, truncated


def _prepare(
    prompt: str,
    *,
    model: str | None,
    max_tokens: int | None,
    enable_thinking: bool | None,
    thinking_budget: int | None,
    streaming: bool,
) -> tuple[dict, str, int, str, dict] | None:
    """组装请求 → (body, model, budget, url, headers)；模型未配置时返回 None。"""
    config = LLMConfig()
    chosen = (model or config.turn_model or config.model or "").strip()
    if not chosen:
        return None
    thinking = (
        enable_thinking
        if enable_thinking is not None
        else (os.getenv("TURN_ENABLE_THINKING", "1").strip() not in ("0", "false", "no"))
    )
    budget = resolve_thinking_budget(thinking_budget) if thinking else 0
    body: dict = {
        "model": chosen,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens if max_tokens is not None else resolve_max_tokens(budget),
        "temperature": config.temperature,
        "stream": streaming,
    }
    if thinking:
        body["enable_thinking"] = True
        body["thinking_budget"] = budget
    if streaming:
        body["stream_options"] = {"include_usage": True}
    url = f"{(config.base_url or '').rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key or ''}",
        "Content-Type": "application/json",
    }
    return body, chosen, budget, url, headers


def chat_with_reasoning(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    enable_thinking: bool | None = None,
    thinking_budget: int | None = None,
    timeout: float | None = None,
    on_delta: DeltaCallback | None = None,
) -> ReasoningReply | None:
    """直连 chat/completions；网络/HTTP 失败返回 None（调用方回落）。

    `on_delta(kind, text)`：`kind ∈ {"reasoning", "content"}`，`text` 是**累计**文本。
    传入时切到流式（用户实时可见）。

    注意：这是**同步**版本（评测脚本/CLI 用）。async 图节点要用
    `chat_with_reasoning_async`——同步调用会把事件循环堵住，流式帧只能在结束后一起吐出。
    """
    prepared = _prepare(
        prompt,
        model=model,
        max_tokens=max_tokens,
        enable_thinking=enable_thinking,
        thinking_budget=thinking_budget,
        streaming=bool(on_delta),
    )
    if prepared is None:
        return None
    body, chosen, budget, url, headers = prepared

    started = time.monotonic()
    if on_delta:
        return _stream_chat(
            url, headers, body, chosen, budget, timeout or turn_timeout(), started, on_delta,
        )

    try:
        response = httpx.post(url, headers=headers, json=body, timeout=timeout or turn_timeout())
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

    reasoning_tokens, truncated = _parse_usage(
        payload.get("usage"), str(choice.get("finish_reason") or ""), budget,
    )
    return ReasoningReply(
        content=str(message.get("content") or ""),
        reasoning=str(message.get("reasoning_content") or ""),
        model=chosen,
        elapsed_ms=elapsed_ms,
        reasoning_tokens=reasoning_tokens,
        truncated=truncated,
    )


def _stream_chat(
    url: str,
    headers: dict,
    body: dict,
    model: str,
    budget: int,
    timeout: float,
    started: float,
    on_delta: DeltaCallback,
) -> ReasoningReply | None:
    """流式收 delta（reasoning_content + content），边收边回调累计文本。

    失败语义：一个字符都没收到 → None（调用方退回非流式/回落）；
    已收到内容但中途断流 → 用已有内容尽力返回（模型至少想了一半）。
    """
    reasoning = ""
    content = ""
    last_usage: dict = {}
    finish_reason = ""
    try:
        with httpx.stream("POST", url, headers=headers, json=body, timeout=timeout) as response:
            if response.status_code != 200:
                logger.warning("turn model stream http %s", response.status_code)
                return None
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload_text = line[5:].strip()
                if payload_text == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload_text)
                except ValueError:
                    continue
                if isinstance(chunk.get("usage"), dict):
                    last_usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices or not isinstance(choices[0], dict):
                    continue
                finish_reason = str(choices[0].get("finish_reason") or finish_reason or "")
                delta = choices[0].get("delta")
                if not isinstance(delta, dict):
                    continue
                piece = delta.get("reasoning_content") or ""
                if piece:
                    reasoning += piece
                    _safe_delta(on_delta, "reasoning", reasoning)
                piece = delta.get("content") or ""
                if piece:
                    content += piece
                    _safe_delta(on_delta, "content", content)
    except Exception as exc:  # noqa: BLE001 - 断流/超时：有内容就尽力返回
        if not reasoning and not content:
            logger.warning("turn model stream failed: %s", exc)
            return None
        logger.warning("turn model stream interrupted after %s chars: %s", len(reasoning), exc)

    reasoning_tokens, truncated = _parse_usage(last_usage, finish_reason, budget)
    return ReasoningReply(
        content=content,
        reasoning=reasoning,
        model=model,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        reasoning_tokens=reasoning_tokens,
        truncated=truncated,
    )


def _safe_delta(on_delta: DeltaCallback, kind: str, text: str) -> None:
    """回调失败不能打断流（渲染是附赠品，不是主链）。"""
    try:
        on_delta(kind, text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("reasoning on_delta failed: %s", exc)

# ── async 版本（图节点用）─────────────────────────────────────────────────
#
# 2026-11-05（plan streaming-reasoning）：图节点是 async 的，同步 httpx 会把事件循环
# 堵住——流式帧只能在调用结束后一起吐出来（实测：8 个 running 帧全挤在 requirement 帧
# 前 35ms，前端看起来仍是「一次蹦出来」）。async 版本让事件循环继续跑，帧才是真的边想边发。


async def chat_with_reasoning_async(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    enable_thinking: bool | None = None,
    thinking_budget: int | None = None,
    timeout: float | None = None,
    on_delta: DeltaCallback | None = None,
) -> ReasoningReply | None:
    """`chat_with_reasoning` 的 async 版（语义一致，只是不阻塞事件循环）。"""
    prepared = _prepare(
        prompt,
        model=model,
        max_tokens=max_tokens,
        enable_thinking=enable_thinking,
        thinking_budget=thinking_budget,
        streaming=bool(on_delta),
    )
    if prepared is None:
        return None
    body, chosen, budget, url, headers = prepared
    started = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=timeout or turn_timeout()) as client:
            if not on_delta:
                response = await client.post(url, headers=headers, json=body)
                if response.status_code != 200:
                    logger.warning(
                        "turn model http %s: %s", response.status_code, response.text[:200],
                    )
                    return None
                payload = response.json()
                choices = payload.get("choices") or []
                choice = choices[0] if choices and isinstance(choices[0], dict) else {}
                message = choice.get("message") if choices else {}
                if not isinstance(message, dict):
                    return None
                reasoning_tokens, truncated = _parse_usage(
                    payload.get("usage"), str(choice.get("finish_reason") or ""), budget,
                )
                return ReasoningReply(
                    content=str(message.get("content") or ""),
                    reasoning=str(message.get("reasoning_content") or ""),
                    model=chosen,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    reasoning_tokens=reasoning_tokens,
                    truncated=truncated,
                )

            reasoning = ""
            content = ""
            last_usage: dict = {}
            finish_reason = ""
            async with client.stream("POST", url, headers=headers, json=body) as response:
                if response.status_code != 200:
                    logger.warning("turn model stream http %s", response.status_code)
                    return None
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload_text = line[5:].strip()
                    if payload_text == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload_text)
                    except ValueError:
                        continue
                    if isinstance(chunk.get("usage"), dict):
                        last_usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices or not isinstance(choices[0], dict):
                        continue
                    finish_reason = str(choices[0].get("finish_reason") or finish_reason or "")
                    delta = choices[0].get("delta")
                    if not isinstance(delta, dict):
                        continue
                    piece = delta.get("reasoning_content") or ""
                    if piece:
                        reasoning += piece
                        _safe_delta(on_delta, "reasoning", reasoning)
                    piece = delta.get("content") or ""
                    if piece:
                        content += piece
                        _safe_delta(on_delta, "content", content)
    except Exception as exc:  # noqa: BLE001 - 断流/超时：有内容就尽力返回
        logger.warning("turn model async stream failed: %s", exc)
        return None

    reasoning_tokens, truncated = _parse_usage(last_usage, finish_reason, budget)
    return ReasoningReply(
        content=content,
        reasoning=reasoning,
        model=chosen,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        reasoning_tokens=reasoning_tokens,
        truncated=truncated,
    )
