"""Text processing utilities for LLM output parsing."""

from __future__ import annotations

import json
import re


def strip_think(text: str) -> str:
    """Remove <think>...</think> blocks from LLM output.

    Handles closed blocks (<think>...</think>) — removes everything
    between the tags. Unclosed blocks (no </think>) are left in place
    so extract_sql can find any trailing SQL content.
    """
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def extract_sql(text: str) -> str:
    """Extract SQL from LLM output.

    Strips <think> blocks, markdown fences, and leading non-SQL text.
    Returns empty string if no SELECT statement is found.
    """
    if not text:
        return ""
    text = strip_think(text)
    text = strip_markdown_fence(text)
    if "select" not in text.lower():
        return ""
    idx = text.lower().find("select")
    if idx > 0:
        text = text[idx:]
    return text.strip()


def strip_markdown_fence(text: str) -> str:
    """Remove markdown code fence markers (```) from LLM output.

    LLMs often wrap structured output in markdown code fences.
    This function strips the opening ```language and closing ``` markers.

    Args:
        text: Raw text that may contain markdown code fences.

    Returns:
        Text with markdown code fences removed.
    """
    if text.startswith("```"):
        # Strip opening fence (```language\n ...)
        text = text.split("\n", 1)[-1]
        # Strip closing fence
        text = text.rsplit("```", 1)[0]
    return text.strip()


def extract_json_from_llm(text: str) -> str:
    """Extract JSON from LLM output by stripping markdown fences.

    Convenience wrapper around strip_markdown_fence for JSON extraction.

    Args:
        text: Raw LLM output that may contain markdown-fenced JSON.

    Returns:
        Cleaned text ready for json.loads().
    """
    return strip_markdown_fence(text)


def safe_json_parse(text: str) -> dict | list | None:
    """Parse JSON from LLM output with fallback extraction.

    Handles common LLM quirks:
    - Markdown code fences (```json ... ```)
    - Leading/trailing text before/after JSON
    - Partial JSON truncation

    Args:
        text: Raw LLM output that may contain JSON.

    Returns:
        Parsed JSON object, or None if parsing fails.
    """
    if not text:
        return None

    cleaned = strip_markdown_fence(text)

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Reasoning models (DeepSeek, MiniMax-M2.7) often produce BOTH a
    # JSON inside their <think> block AND a final JSON after </think>.
    # cleaned.find("{") + cleaned.rfind("}") may match the wrong
    # pair. Prefer raw_decode() which auto-detects the first complete
    # JSON value and advances past it. Iterate because the response may
    # contain several adjacent objects.
    decoder = json.JSONDecoder()
    idx = 0
    last_dict: dict | None = None
    last_any: dict | list | None = None
    while idx < len(cleaned):
        # Skip whitespace and non-JSON-start chars
        while idx < len(cleaned) and cleaned[idx] not in ("{", "["):
            idx += 1
        if idx >= len(cleaned):
            break
        try:
            obj, end = decoder.raw_decode(cleaned, idx)
            last_any = obj
            if isinstance(obj, dict):
                last_dict = obj
            idx = end
        except json.JSONDecodeError:
            idx += 1
    # Prefer dict (RequirementCard shape). Fall back to whatever was found.
    return last_dict if last_dict is not None else last_any