from __future__ import annotations

"""Intent Agent prompt（Stage 3 LLM 分类）。

源：原 `app/agent/intent.py:67` 裸 f-string。P7 重构为 6 段 + META + build 函数，
文案等价不动（plan NOT doing：删现有 prompt 文案 / 改温度）。
"""

from typing import Any

# 6 段：system_contract / role / task_contract / tool_policy / output_schema / safety_policy
INTENT_CLASSIFY_V1: dict[str, str] = {
    "system_contract": (
        "你是 ReportAgent 的意图分类器。职责：判断用户查询属于哪一类，"
        "不做 SQL 生成、不调工具、不重写查询。"
    ),
    "role": "你是意图分类器。",
    "task_contract": (
        "判断用户查询属于哪类，只输出 JSON，禁止解释。"
        "\n\n用户查询: {user_query}"
        "\n\n类别:"
        "\n- report: 针对数据库星型模型做报表/数据分析（销售额、趋势、排名、退货、库存、考勤等业务指标）"
        "\n- interface: 关于外部接口/实时推送/数据源接入的查询（不是数据库报表），"
        "如「订单接口字段」「实时库存推送」"
        "\n- chitchat: 闲聊或与数据无关的请求"
        "\n- other: 其他"
    ),
    "tool_policy": (
        "本 Agent 不调用任何外部工具；判定仅依据查询语义。"
        "如对类别无把握，confidence 应 ≤ 0.5，由上层决定 fallback。"
    ),
    "output_schema": (
        '输出: {{"kind": "report|interface|chitchat|other", '
        '"confidence": 0.0-1.0, "reason": "简短理由"}}'
    ),
    "safety_policy": (
        "Do NOT invent tables/columns。"
        "Do NOT fabricate query results。"
        "Do NOT assume unavailable schema。"
        "Do NOT call search_schema（意图分类不需要表结构）。"
        "Do NOT generate SQL。"
    ),
}

INTENT_CLASSIFY_META: dict[str, Any] = {
    "name": "intent_classify",
    "version": 1,
    "purpose": "判断用户查询属于 report / interface / chitchat / other 四类之一",
    "input": ["user_query"],
    "output": '{"kind": str, "confidence": float, "reason": str}',
}


def build_intent_classify_prompt(user_query: str) -> str:
    """组装 6 段 + 注入 user_query。返回完整 prompt string。"""
    from app.infra.trace.sdk import record_prompt_version

    record_prompt_version(INTENT_CLASSIFY_META["name"], INTENT_CLASSIFY_META["version"])
    sections = [
        INTENT_CLASSIFY_V1["system_contract"],
        INTENT_CLASSIFY_V1["role"],
        INTENT_CLASSIFY_V1["task_contract"].format(user_query=user_query),
        INTENT_CLASSIFY_V1["tool_policy"],
        INTENT_CLASSIFY_V1["output_schema"],
        INTENT_CLASSIFY_V1["safety_policy"],
    ]
    return "\n\n".join(s for s in sections if s)