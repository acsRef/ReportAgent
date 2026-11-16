from __future__ import annotations

"""Memory Agent prompts（conversation summary fusion）。

源：原 `app/memory/conversation.py:69` 裸 f-string。P7 重构为 6 段 + META +
build 函数，文案等价不动。

注意：build 函数内 late-import `app.memory.conversation` 的常量与 `format_messages`，
避免 `app.memory.prompts → app.memory.conversation` 与反向 import 形成循环。
"""

from typing import Any, Optional

CONVERSATION_SUMMARIZE_V1: dict[str, str] = {
    "system_contract": (
        "你是 ReportAgent 对话摘要助手。职责：维护一段对话的滚动摘要——"
        "把「旧摘要」与「最新对话」融合为新摘要，同时抽取稳定的结构化事实。"
        "不直接生成 SQL、不调外部工具。"
    ),
    "role": "你在维护一段对话的滚动摘要。",
    "task_contract": (
        "把「旧摘要」和「最新对话」融合成一份新摘要，并抽取其中稳定的结构化事实。"
        "\n\n旧摘要（{old_digest_len} 字）："
        "\n{old_digest}"
        "\n\n最新对话（{batch_len} 条）："
        "\n{batch_text}"
    ),
    "tool_policy": (
        "本 Agent 不调用任何外部工具。仅依据提供的「旧摘要」与「最新对话」输出。"
        "不要引入未在对话中出现的字段映射或偏好。"
    ),
    "output_schema": (
        "只输出 JSON，禁止解释、禁止 markdown："
        "\n{{"
        '\n  "summary": "融合新旧信息的叙事摘要，不超过 {max_chars} 字；'
        '只保留话题脉络/用户反馈/决策背景，不含具体字段名和数值",'
        '\n  "extracted_schemas": ['
        '\n    {{"type": "field_mapping", "user_term": "销售额", '
        '"db_field": "order_amount", "table": "fact_orders"}},'
        '\n    {{"type": "calculation", "user_term": "环比", '
        '"sql_expression": "(v-LAG(v))/LAG(v)*100"}}'
        "\n  ],"
        '\n  "extracted_preferences": ["用户要求华东华南分开展示", "用户偏好柱状图"]'
        "\n}}"
        "\n\n要求："
        "\n1. summary 是替换旧摘要，不是追加，严格不超过 {max_chars} 字。"
        "\n2. extracted_schemas 只提取新出现或变更的字段映射/计算口径。"
        "\n3. extracted_preferences 只提取明确、稳定的用户偏好指令。"
    ),
    "safety_policy": (
        "Do NOT invent tables/columns（不要为不在对话中的字段编 db_field）。"
        "Do NOT fabricate query results（不在摘要里写具体数字/趋势）。"
        "Do NOT assume unavailable schema（用户没提到的字段映射不要写）。"
        "Do NOT call search_schema when schema is already known（本步骤不需要表结构）。"
        "Do NOT extract preferences that the user did not state explicitly."
        "Do NOT bypass max_chars limit——超长要主动裁剪。"
    ),
}

CONVERSATION_SUMMARIZE_META: dict[str, Any] = {
    "name": "conversation_summarize",
    "version": 1,
    "purpose": "融合旧摘要+最新对话为新摘要，抽取 schemas/preferences",
    "input": ["old_digest", "batch_messages", "max_chars"],
    "output": "{summary, extracted_schemas, extracted_preferences}",
}

# --- V2：结构化 checkpoint（2026-11-16，plan compaction-dsh-alignment） ------
# 与 V1 的差别只有一处，但很关键：**禁止模型碰数据**。
# V1 要求「只保留话题脉络/用户反馈/决策背景，不含具体字段名和数值」——把数值和口径一起
# 赶走，于是执行/报告侧的历史既没有数字也没有字段；V2 把两件事分开：口径（字段名/维度名）
# 允许写；**数值只能逐字引用**（数值真源是 `app/memory/compaction.collect_data_facts` 逐字抽取的事实，
# 另行注入 `<数据事实>` 块）。模型看不到数字，就没有篡改数字的机会。
# 段位对标 DSH compaction-basic 的 8 段 checkpoint，按数据分析场景裁剪为 5 段。
CONVERSATION_CHECKPOINT_V2: dict[str, str] = {
    "system_contract": (
        "你是 ReportAgent 的对话压缩引擎。职责：把「旧 checkpoint」与「最新对话」合并成"
        "一份结构化 checkpoint，让另一个模型读完就能接着干活，且不需要回看原文。"
        "你不生成 SQL、不调外部工具、不推测数据。"
    ),
    "role": "你正在为一段数据分析会话生成 checkpoint。",
    "task_contract": (
        "旧 checkpoint（{old_digest_len} 字）："
        "\n{old_digest}"
        "\n\n最新对话（{batch_len} 条）："
        "\n{batch_text}"
    ),
    "tool_policy": (
        "本 Agent 不调用任何外部工具。仅依据提供的「旧 checkpoint」与「最新对话」输出。"
        "不要引入未在对话中出现的字段映射或偏好。"
    ),
    "output_schema": (
        "只输出 JSON，禁止解释、禁止 markdown 围栏："
        "\n{{"
        '\n  "summary": "结构化 checkpoint，不超过 {max_chars} 字；严格按下面 5 段、按序，'
        '每段 terse bullet，空段写（无），缺段视为不合格；数值只能逐字引用，禁止自己算",'
        '\n  "extracted_schemas": ['
        '\n    {{"type": "field_mapping", "user_term": "销售额", '
        '"db_field": "order_amount", "table": "fact_orders"}}'
        "\n  ],"
        '\n  "extracted_preferences": ["用户要求华东华南分开展示", "用户偏好柱状图"]'
        "\n}}"
        "\n\nsummary 的段位（缺一不可）："
        "\n## 用户目标"
        "\n## 已确认口径（只写指标名/维度名/时间形态；数值可逐字引用，不得自算）"
        "\n## 用户纠正（照抄用户的否定与改口，如「不是环比，是同比」）"
        "\n## 已否决的写法"
        "\n## 未完成事项（用户提过但还没做的）"
        "\n\n要求："
        "\n1. summary 是**替换**旧 checkpoint，不是追加；仍成立的事实留下，过期的丢掉。"
        "\n2. extracted_schemas 只提取新出现或变更的字段映射/计算口径。"
        "\n3. extracted_preferences 只提取明确、稳定的用户偏好指令。"
    ),
    "safety_policy": (
        "Numbers must come from the conversation verbatim（数值只能逐字引用对话里已有的写法，"
        "例如 `11 月`、`9631.81 万元`）；禁止自己计算、四舍五入、改写单位或编造数值——"
        "系统会拿你写的每个数字和数据事实逐字比对，对不上就判越界并退回确定性折法。"
        "Do NOT invent tables/columns（不要为不在对话中的字段编 db_field）。"
        "Do NOT fabricate query results（不写结论性数字；趋势判断可以写，数据以系统注入的事实为准）。"
        "Do NOT extract preferences that the user did not state explicitly."
        "Do NOT mention this checkpoint request or that the context was compressed."
        "Do NOT bypass max_chars limit——超长要主动裁剪。"
    ),
}

CONVERSATION_CHECKPOINT_META: dict[str, Any] = {
    "name": "conversation_checkpoint",
    "version": 2,
    "purpose": "把旧 checkpoint + 最新对话合并成结构化 5 段 checkpoint（数值只能逐字引用）",
    "input": ["old_checkpoint", "batch_messages", "max_chars"],
    "output": "{summary, extracted_schemas, extracted_preferences}",
}


def build_conversation_checkpoint_prompt(
    old_checkpoint: Optional[str],
    batch_messages: list,
    max_chars: int | None = None,
) -> str:
    """组装 V2 结构化 checkpoint prompt（生产路径）。

    `max_chars` 默认走 `L2_MAX_CHARS`；`batch_messages` 渲染成 `role: content` 文本。
    """
    from app.memory.conversation import L2_MAX_CHARS, format_messages

    if max_chars is None:
        max_chars = L2_MAX_CHARS

    sections = [
        CONVERSATION_CHECKPOINT_V2["system_contract"],
        CONVERSATION_CHECKPOINT_V2["role"],
        CONVERSATION_CHECKPOINT_V2["task_contract"].format(
            old_digest_len=len(old_checkpoint or ""),
            old_digest=old_checkpoint or "（无）",
            batch_len=len(batch_messages),
            batch_text=format_messages(batch_messages),
        ),
        CONVERSATION_CHECKPOINT_V2["tool_policy"],
        CONVERSATION_CHECKPOINT_V2["output_schema"].format(max_chars=max_chars),
        CONVERSATION_CHECKPOINT_V2["safety_policy"],
    ]
    return "\n\n".join(s for s in sections if s)


def build_conversation_summarize_prompt(
    old_digest: Optional[str],
    batch_messages: list,
    max_chars: int | None = None,
) -> str:
    """组装 6 段 + 注入 old_digest + batch_text。max_chars 默认走 L2_MAX_CHARS=800。"""
    # Late import 避免循环: app.memory.prompts ↔ app.memory.conversation
    from app.memory.conversation import L2_MAX_CHARS, format_messages

    if max_chars is None:
        max_chars = L2_MAX_CHARS

    sections = [
        CONVERSATION_SUMMARIZE_V1["system_contract"],
        CONVERSATION_SUMMARIZE_V1["role"],
        CONVERSATION_SUMMARIZE_V1["task_contract"].format(
            old_digest_len=len(old_digest or ""),
            old_digest=old_digest or "（无）",
            batch_len=len(batch_messages),
            batch_text=format_messages(batch_messages),
        ),
        CONVERSATION_SUMMARIZE_V1["tool_policy"],
        CONVERSATION_SUMMARIZE_V1["output_schema"].format(max_chars=max_chars),
        CONVERSATION_SUMMARIZE_V1["safety_policy"],
    ]
    return "\n\n".join(s for s in sections if s)