"""分层超时单一来源（P9，伞形 §七 TimeoutPolicy）。

各层默认值收编自既有实现（P9 不改 llm/mcp/db 行为，只收编数值并钉一致）；
真新行为只有 background_task（MAX_TASK_DURATION，伞形 §200：超时 → Persist FAILED
→ ReportVersion(error) → 前端 error，不允许永远停在 generating）。
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable

LAYER_DEFAULTS: dict[str, float] = {
    "llm_request": float(os.getenv("LLM_TIMEOUT", "60")),
    "llm_total_budget": float(os.getenv("LLM_MAX_TOTAL_TIME", "90")),
    "mcp_request": float(os.getenv("RAGENT_MCP_TIMEOUT", "15")),
    "db_connect": 10.0,  # = sql_tools.CONNECT_TIMEOUT_S
    "db_statement_ms": 30_000.0,  # = sql_tools.STATEMENT_TIMEOUT_MS
    # 600s 依据：P0 基线 P95 180s；LLM 总预算 90s/次 × 最坏 repair 链 + SQL 30s 富余。
    "background_task": float(os.getenv("MAX_TASK_DURATION", "600")),
}

# 背景任务总预算（main.py _run_confirmed_graph 消费）。
MAX_TASK_DURATION = LAYER_DEFAULTS["background_task"]


async def run_with_timeout(awaitable: Awaitable[Any], seconds: float) -> Any:
    """给 awaitable 套总预算；超时抛 asyncio.TimeoutError，正常则透传结果。"""
    return await asyncio.wait_for(awaitable, timeout=seconds)
