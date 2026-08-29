from __future__ import annotations

import json
import re
import time
import logging
from typing import Any

from langchain_openai import ChatOpenAI

from app.llm.config import LLMConfig
from app.llm_resilience import invoke_with_retry

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_REASONING_RE = re.compile(r"<reasoning>.*?</reasoning>", re.DOTALL | re.IGNORECASE)


def strip_think_tags(text: str) -> str:
    if not text:
        return text
    out = _THINK_RE.sub("", text)
    out = _REASONING_RE.sub("", out)
    return out.strip()


def _extract_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found")
    return json.loads(text[start : end + 1])


class LLMAdapter:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig()

    def _chat(self, **kwargs: Any) -> ChatOpenAI:
        cfg = {**self.config.to_chat_kwargs(), **kwargs}
        return ChatOpenAI(**cfg)

    def generate(self, prompt: str | list, **kwargs: Any) -> str:
        llm = self._chat(**kwargs)
        start = time.monotonic()
        resp = invoke_with_retry(lambda: llm.invoke(prompt))
        elapsed_ms = int((time.monotonic() - start) * 1000)
        raw = (getattr(resp, "content", "") or "").strip()
        text = strip_think_tags(raw)
        if not text:
            logger.warning("llm generate empty after strip raw=%r", raw[:2000])
            text = raw.strip()
        try:
            model = kwargs.get("model", self.config.model)
            usage = getattr(resp, "usage_metadata", None) or {}
            if isinstance(usage, dict):
                pt = usage.get("input_tokens", 0) or 0
                ct = usage.get("output_tokens", 0) or 0
            else:
                pt = getattr(usage, "input_tokens", 0) or 0
                ct = getattr(usage, "output_tokens", 0) or 0
            from app.infra.trace.sdk import current_tracer

            tracer = current_tracer()
            if tracer is not None:
                tracer.add_llm_call(model, pt, ct, elapsed_ms)
        except Exception:
            pass
        return text

    def generate_structured(self, prompt: str | list, **kwargs: Any) -> dict:
        text = self.generate(prompt, **kwargs)
        try:
            return _extract_json(text)
        except Exception as exc:
            logger.warning("generate_structured json parse failed: %s text=%.500s", exc, text)
            raise ValueError(f"structured parse failed: {exc}") from exc
