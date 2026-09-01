from __future__ import annotations

import json
import re
import time
import logging
from typing import Any, Callable

from langchain_openai import ChatOpenAI

from app.llm.config import LLMConfig
from app.reliability.retry import invoke_with_retry

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


class StructuredParseError(ValueError):
    """LLM 输出无法解析为 JSON 对象。

    继承 ValueError 保持旧 caller 的 except 兼容；但语义上独立于 schema
    validation 失败,便于 generate_structured_safe 区分兜底范围。
    """


class SchemaValidationError(ValueError):
    """LLM 输出 JSON 解析成功但不满足 schema。

    重复请求 LLM 不会改变 schema 错误结论,所以 generate_structured_safe
    必须直接向上抛,不能误入 parse fallback 再发起一次 LLM 调用。
    """


def _validate_against_schema(result: Any, schema: Any) -> Any:
    """单一来源的 schema 校验入口,成功返回 model_dump,失败抛 SchemaValidationError。"""
    from pydantic import BaseModel

    try:
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return schema.model_validate(result).model_dump(mode="json")
        if isinstance(schema, BaseModel):
            return schema.model_validate(result).model_dump(mode="json")
    except Exception as exc:
        raise SchemaValidationError(f"schema validation failed: {exc}") from exc
    # 非 Pydantic schema:不做语义校验(仅返回原 dict)。由 caller 自负其责。
    return result


class LLMAdapter:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig()

    def _chat(self, **kwargs: Any) -> ChatOpenAI:
        cfg = {**self.config.to_chat_kwargs(), **kwargs}
        return ChatOpenAI(**cfg)

    def generate(self, prompt: str | list, **kwargs: Any) -> str:
        llm = self._chat(**kwargs)
        start = time.monotonic()
        resp = invoke_with_retry(
            lambda: llm.invoke(prompt),
            max_retries=self.config.max_retries,
            max_total_time=self.config.max_total_time,
        )
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
            from app.observability.redaction import redact

            tracer = current_tracer()
            if tracer is not None:
                tracer.add_llm_call(
                    model, pt, ct, elapsed_ms,
                    input=redact(prompt),
                    output=redact(text),
                )
        except Exception as exc:
            logger.warning("llm trace failed: %s", exc)
        return text

    def generate_structured(
        self,
        prompt: str | list,
        schema: Any | None = None,
        **kwargs: Any,
    ) -> dict:
        text = self.generate(prompt, **kwargs)
        try:
            data = _extract_json(text)
        except Exception:
            from app.utils.text import safe_json_parse

            fallback = safe_json_parse(text)
            if isinstance(fallback, dict):
                data = fallback
            else:
                logger.warning("generate_structured json parse failed text=%.500s", text)
                raise StructuredParseError("structured parse failed: no JSON object found") from None
        if schema is None:
            return data
        # schema 校验失败 → SchemaValidationError (不由 catch-all 兜底再发一次 LLM)
        try:
            return _validate_against_schema(data, schema)
        except SchemaValidationError:
            logger.warning("generate_structured schema validation failed data=%.500s", str(data))
            raise

    def generate_structured_safe(
        self,
        prompt: str | list,
        parser: Callable[[str], Any] | None = None,
        schema: Any | None = None,
        **kwargs: Any,
    ) -> dict:
        """Adapter 内统一 fallback:caller 一次调用即可。

        顺序:
          1) generate_structured(prompt, schema=schema) —— 主路径
          2) 仅 StructuredParseError 时 → generate(prompt) + parser 兜底
          3) parser 兜底成功后仍按需走 schema 校验
          4) SchemaValidationError 直接向上抛 —— schema 错不会因为再发一次 LLM 而改变结论

        区分语义:
          - StructuredParseError(ValueError)  → 可重试/兜底
          - SchemaValidationError(ValueError) → 不可重试,直接 fail

        网络/retry 错在 generate() 内部已处理,不会进 except。
        """
        try:
            return self.generate_structured(prompt, schema=schema, **kwargs)
        except StructuredParseError:
            text = self.generate(prompt, **kwargs)
            if parser is None:
                raise
            result = parser(text)
            if not isinstance(result, dict):
                raise StructuredParseError(
                    f"structured parse failed: parser returned {type(result).__name__}"
                ) from None
            if schema is None:
                return result
            # 同样走 schema 校验 —— SchemaValidationError 会自然向上抛
            try:
                return _validate_against_schema(result, schema)
            except SchemaValidationError:
                logger.warning(
                    "generate_structured_safe schema validation failed data=%.500s",
                    str(result),
                )
                raise
