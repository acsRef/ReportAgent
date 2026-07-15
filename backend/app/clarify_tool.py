from __future__ import annotations

import json

from langchain_core.tools import tool


@tool
async def ask_clarification_tool(question: str) -> str:
    """当用户问题缺少关键信息时调用此工具向用户追问。

    例：用户没指定区域 → ask_clarification_tool("请问您想看哪个区域的数据？")
    调用后 Agent 暂停等待用户回复，回复后继续执行。
    """
    from langgraph.types import interrupt
    return interrupt({
        "type": "clarify",
        "question": question,
    })
