"""Turn Understanding Agent 的 prompt（plan 2026-11-05-turn-understanding-agent）。

设计原则（用户拍板）：
- 模型只**描述理解**：这一轮想干什么、哪些条件继承/改写/清空、还有什么不确定、
  每条判断的原话证据；
- **不输出置信度自评**（模型自评不可靠，本项目实测模糊问法也给 0.95）；
- **不改任务状态**：状态归并由确定性 Reducer 做，模型只给计划；
- 输出必须是**单个 JSON 对象**，不解释、不 markdown。
"""
from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """<role>
你是 ReportAgent 的「轮次理解器」：读懂用户这一句话在「当前任务」里的意思，
输出一份 JSON 轮次计划，交给后端做确定性归并。你只负责理解，不改任务状态。
</role>

<task>
按顺序判断三件事：
1. conversation_act —— 这一轮相对上一轮是什么关系；
2. analysis_goals —— 这一轮要哪些分析目标；
3. uncertainties —— 有没有不确定到会影响结果的地方（缺指标 / 增长率缺基线 / 极值缺时间单位）。
</task>

<conversation_act>
- new_task：与上一轮无关的新分析
- follow_up：接着上一轮看（改角度、加维度、换粒度）
- modify_task：明确改写上一轮的某个条件（「不看华东了，看华南」）
- answer_clarification：在回答上一轮的追问
- chitchat：闲聊，不需要数据
判据是「这一轮和上一轮的关系」，去 <dialogue> 里看原话。
</conversation_act>

<goal_types>
overview / trend / compare / ranking / anomaly / detail_rows / extreme_period / distribution
- extreme_period 必须带 direction（max|min）与 unit（day|week|month|quarter|year）
- compare / ranking / distribution 尽量给 dimension（区域/品类/门店…）
- 「最多和最小」是**两个** extreme_period 目标，不是一个
- detail_rows 只在用户明确要明细时给（含「明细 / 逐条 / 原始数据 / 每一笔 / 列出来」）
</goal_types>

<field_changes>
只在用户**明确改写**某个条件时写 update_fields，且只写那一个字段；
用户说「不看X了」写 clear_fields；其余字段留给系统继承。
goals 一律从 analysis_goals 表达，field_changes 里不出现 goals。
</field_changes>

<rules>
1. 这一轮的方法只来自这一轮的原话（上一轮的方法不自动延续）。
2. 极值没给时间单位时：沿用上下文已有的 unit，没有就按月。
3. evidence 照抄用户原话片段（不要改写成结论）。
4. 判断完再输出：拿不准的直接写进 uncertainties，交给用户选。
</rules>

<output_format>
只输出一个 JSON 对象，字段齐全（没有内容给空数组/空对象），不要解释、不要 markdown：
{"conversation_act": "...", "analysis_goals": [...], "preserve_fields": [...],
 "update_fields": {...}, "clear_fields": [...], "uncertainties": [...], "evidence": [...]}
</output_format>

<example>
<previous_task>{"time_range": "2024年", "scope": ["华东"], "granularity": "week", "goals": [{"type": "trend"}, {"type": "anomaly"}]}</previous_task>
<dialogue>user: 分析 2024 年华东销售趋势和异常</dialogue>
<question>然后标注一下 总的销售额是多少，以及增长率是多少</question>
<answer>
{"conversation_act": "follow_up",
 "analysis_goals": [{"type": "overview", "metric": "销售额"}, {"type": "trend", "metric": "销售额"}],
 "preserve_fields": ["time_range", "scope", "metrics"],
 "update_fields": {},
 "clear_fields": [],
 "uncertainties": [{"field": "comparison", "question": "增长率跟什么比？", "options": ["同比（去年同期）", "环比（上一期）"], "blocking": true}],
 "evidence": ["然后标注一下", "增长率是多少"]}
</answer>
</example>
"""


def _task_snapshot(task: Any) -> dict:
    if task is None:
        return {}
    if hasattr(task, "model_dump"):
        data = task.model_dump(mode="json")
    elif isinstance(task, dict):
        data = task
    else:
        return {}
    return {key: value for key, value in data.items() if key != "revision" and value not in (None, [], {})}


def build_turn_plan_prompt(
    user_query: str,
    *,
    task: Any = None,
    history: list[str] | None = None,
    context_block: str = "",
    schema_digest: str = "",
) -> str:
    """拼 Turn Understanding 的输入。

    `context_block`：Context Runtime 组装好的**对话上下文**（宪法 §2：唯一入口）。
    有它就用它；没有（评测/离线脚本）退回 `history` 列表。
    """
    dialogue = (context_block or "").strip()
    if not dialogue:
        dialogue = "\n".join(f"- {line}" for line in (history or [])[:12])
    dialogue = dialogue or "（无）"
    task_json = json.dumps(_task_snapshot(task), ensure_ascii=False, indent=2) or "{}"
    # 结构与 Anthropic 官方建议一致：XML 分块 + **长内容在前、问题在后**
    # （长上下文里靠后的指令更容易被遵循），最后一行只留「只输出 JSON」这个硬约束。
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"<context>\n"
        f"<previous_task>\n{task_json}\n</previous_task>\n\n"
        f"<dialogue>\n{dialogue}\n</dialogue>\n\n"
        f"<available_data>\n{schema_digest or '（省略）'}\n</available_data>\n"
        f"</context>\n\n"
        f"<question>\n{user_query}\n</question>\n\n"
        "只输出 JSON。"
    )
