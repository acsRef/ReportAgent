"""Text processing utilities for LLM output parsing."""

from __future__ import annotations

import json


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

    # Fallback: find first { or [ and last } or ]
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = cleaned.find(start_char)
        if start == -1:
            continue
        end = cleaned.rfind(end_char)
        if end > start:
            candidate = cleaned[start:end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

    return None