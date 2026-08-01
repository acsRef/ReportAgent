"""mem0 L3 事实抽取引擎。

角色（见 docs/plans/2026-08-01-memory-mechanism.md）：mem0 **只做 L3 长期事实抽取**，
不做召回主路径（召回仍是 UserMemory/QueryMemory 的 pgvector 语义排序——这是
memory-ranking-plan 的既定决策）。mem0 擅长「从对话里自动抽取事实 + 自带去重/更新」，
正好补 LLM JSON 抽取之外的结构化能力。

- ``MEM0_ENABLED=true`` 时启用；否则 `extract_facts` 直接返回 []，由调用方降级到
  纯 LLM 抽取（compress_and_extract 的 extracted_schemas/preferences）。
- 任何失败都 graceful 降级——记忆抽取绝不能拖垮主查询链路。
- mem0 的 ``add`` 是同步阻塞（内含 LLM 调用），故经 ``asyncio.to_thread`` 跑，
  不阻塞事件循环。
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_client = None


def mem0_enabled() -> bool:
    return os.getenv("MEM0_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _get_client():
    """懒加载 mem0 客户端。配置复用与主 LLM/embedder 同源的 OpenAI 兼容端点，
    向量库用本地 chroma（与原 app/memory.py 一致）。"""
    global _client
    if _client is None:
        from mem0 import Memory

        api_key = os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY")
        base_url = os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1")
        config = {
            "llm": {
                "provider": "openai",
                "config": {
                    "model": os.getenv("LLM_MODEL", "MiniMax-M2.7-highspeed"),
                    "api_key": api_key,
                    "base_url": base_url,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"),
                    "api_key": os.getenv("SILICONFLOW_API_KEY") or api_key,
                    "base_url": os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
                },
            },
            "vector_store": {
                "provider": "chroma",
                "config": {
                    "collection_name": "report_memories",
                    "path": str(Path(__file__).resolve().parents[3] / ".mem0"),
                },
            },
        }
        _client = Memory.from_config(config)
    return _client


def _extract_sync(text: str, user_id: str) -> list[str]:
    """同步抽取：mem0.add 返回其抽取/更新的记忆条目，只保留 ADD/UPDATE 的事实文本。"""
    client = _get_client()
    res = client.add(text, user_id=user_id)
    results = res.get("results", []) if isinstance(res, dict) else (res or [])
    facts: list[str] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        mem = r.get("memory")
        event = r.get("event")
        # DELETE/NOOP 不算新增事实；event 缺省（旧版本）时按抽取到即采纳
        if mem and event in (None, "ADD", "UPDATE"):
            facts.append(str(mem))
    return facts


async def extract_facts(text: str, user_id: str | int) -> list[str]:
    """用 mem0 从一段对话文本抽取长期事实。未启用/失败 → 返回 []（降级）。"""
    if not mem0_enabled() or not (text or "").strip():
        return []
    try:
        return await asyncio.to_thread(_extract_sync, text, str(user_id))
    except Exception as exc:
        logger.warning("mem0 extract_facts failed, degrading to LLM-only: %s", exc)
        return []
