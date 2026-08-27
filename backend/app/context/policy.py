"""Agent-specific context policy（P3 Task 5）。

P3 plan §2.2 钉住：
- 枚举 REQUIREMENT / EXECUTION / REPORT
- resolver 按 agent_name 前缀映射；未知 → REQUIREMENT（保守 fallback）
"""
from __future__ import annotations

from enum import Enum


class AgentContextPolicy(str, Enum):
    REQUIREMENT = "requirement"
    EXECUTION = "execution"
    REPORT = "report"


class ContextPolicyResolver:
    """根据 agent 名解析为 AgentContextPolicy。

    前缀规则（P3）：
    - `requirement_*` → REQUIREMENT
    - `confirmed_execution_*` / `sql_*` / `_generate_sql*` / `data_*` → EXECUTION
    - `report_*` → REPORT
    - 其他（含未知 / 空串）→ REQUIREMENT（保守 fallback）
    """

    def resolve(self, agent_name: str) -> AgentContextPolicy:
        if not agent_name:
            return AgentContextPolicy.REQUIREMENT
        if agent_name.startswith("requirement_"):
            return AgentContextPolicy.REQUIREMENT
        if (
            agent_name.startswith("confirmed_execution_")
            or agent_name.startswith("sql_")
            or agent_name.startswith("_generate_sql")
            or agent_name.startswith("data_")
        ):
            return AgentContextPolicy.EXECUTION
        if agent_name.startswith("report_"):
            return AgentContextPolicy.REPORT
        return AgentContextPolicy.REQUIREMENT
