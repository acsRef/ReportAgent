"""Text processing utilities for LLM output parsing."""

from __future__ import annotations


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