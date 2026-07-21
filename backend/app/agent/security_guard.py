from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SecurityResult:
    score: int = 0
    level: str = "LOW"
    blocked: bool = False
    matched_rules: list[str] = field(default_factory=list)
    reason: str = ""


_RULES: list[tuple[str, str, int]] = [
    ("ignore_previous", r"ignore\s+(all|previous|prior)\s+(instructions|rules|system)", 3),
    ("ignore_previous_cn", r"忽略.*(之前|上面|以上).*(规则|指令|要求)", 3),
    ("forget_instructions", r"forget\s+(all|your)\s+(instructions|rules|prompt)", 3),
    ("override_role", r"(you\s+are\s+now|act\s+as\s+|you\s+are\s+not\s+)", 2),
    ("system_prompt_leak", r"(system\s+prompt|原始提示|系统提示词)", 3),
    ("password_request", r"(show|get|give|tell|输出|告诉|获取).{0,10}(password|密码|secret|密钥)", 3),
    ("data_exfil", r"(dump|export|extract|泄露|窃取).{0,10}(data|user|password|客户|订单)", 5),
    ("drop_table", r"drop\s+table", 5),
    ("sql_ddl", r"\b(delete|insert|update|alter|truncate|drop)\s", 4),
    ("role_hijack", r"你现在是|你是管理员|你是老板", 2),
]

class SecurityGuard:

    @staticmethod
    def check(query: str) -> SecurityResult:
        score = 0
        matched: list[str] = []

        for name, pattern, weight in _RULES:
            if re.search(pattern, query, re.I):
                score += weight
                matched.append(name)

        if score >= 3:
            level = "HIGH"
            blocked = True
            reason = f"检测到高风险模式: {', '.join(matched)}"
        else:
            level = "LOW"
            blocked = False
            reason = ""

        return SecurityResult(
            score=score,
            level=level,
            blocked=blocked,
            matched_rules=matched,
            reason=reason,
        )
