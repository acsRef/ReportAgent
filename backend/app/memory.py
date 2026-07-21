from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_MEM0_ENABLED = os.getenv("MEM0_ENABLED", "false").lower() == "true"
_memory_client = None


def get_memory():
    global _memory_client
    if not _MEM0_ENABLED:
        return None
    if _memory_client is None:
        try:
            from mem0 import Memory

            config = {
                "llm": {
                    "provider": "openai",
                    "config": {
                        "model": os.getenv("LLM_MODEL", "MiniMax-M2.7-highspeed"),
                        "api_key": os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY"),
                        "base_url": os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1"),
                    },
                },
                "embedder": {
                    "provider": "openai",
                    "config": {
                        "model": os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
                        "api_key": os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY"),
                        "base_url": os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1"),
                    },
                },
                "vector_store": {
                    "provider": "chroma",
                    "config": {
                        "collection_name": "report_memories",
                        "path": str(Path(__file__).parent.parent / ".mem0"),
                    },
                },
            }
            _memory_client = Memory.from_config(config)
        except ImportError:
            logger.warning("mem0 not installed, memory disabled")
            return None
    return _memory_client


def search_memories(query: str, user_id: str, limit: int = 5) -> list[str]:
    mem = get_memory()
    if mem is None:
        return []
    try:
        memories = mem.search(query, user_id=user_id, limit=limit)
        return [m.get("text", "") for m in memories if m.get("text")]
    except Exception:
        return []


def add_memory(message: str, user_id: str) -> None:
    mem = get_memory()
    if mem is None:
        return
    try:
        mem.add(message, user_id=user_id)
    except Exception:
        pass


def format_memory_context(memories: list[str]) -> str:
    if not memories:
        return ""
    lines = [f"- {m}" for m in memories]
    return "用户历史信息:\n" + "\n".join(lines)
