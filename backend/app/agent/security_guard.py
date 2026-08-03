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


# 注入规则。英文 ignore/forget/disregard 允许中间夹 0–3 个词（此前只允许夹 1 个词，
# 导致最经典的 "ignore all previous instructions" 都漏掉——all 与 previous 两个词
# 夹在 ignore 与 instructions 之间）。中文补「指令覆盖」与「作废/失效」两类自然说法。
# 新规则都要求「指令类词 + 覆盖/失效类词」同现，纯业务查询（"之前的销售额"「对比上月」
# 「忽略空值」）不含指令类词，不会误伤——见 tests/graphs/test_security_guard.py。
_RULES: list[tuple[str, str, int]] = [
    # 英文「忽略/忘记/无视之前的指令」——允许中间夹 0–3 个词
    ("ignore_previous", r"ignore\s+(?:\w+\s+){0,3}(?:instructions?|rules?|system|prompts?|commands?|context)", 3),
    ("forget_instructions", r"forget\s+(?:\w+\s+){0,3}(?:instructions?|rules?|prompts?|context)", 3),
    ("disregard_instructions", r"disregard\s+(?:\w+\s+){0,3}(?:instructions?|rules?|prompts?|previous|prior)", 3),
    # 中文「忽略/无视…之前的…指令/规则/设定」
    ("override_instruction_cn", r"(?:忽略|无视|别管|不用管|不要管|跳过).{0,12}(?:之前|以前|上面|以上|从前|先前|原来|前面|所有|全部).{0,12}(?:prompt|提示词|指令|规则|要求|设定|约束|限制|对话|上下文)", 3),
    # 中文「之前的 prompt/指令/设定…失效/作废/无效」（本 review 的直接场景）
    ("invalidate_previous_cn", r"(?:之前|以前|上面|以上|从前|先前|原来|前面|过往).{0,12}(?:prompt|提示词|指令|规则|要求|设定|约束|对话|上下文).{0,12}(?:失效|作废|无效|不算|都不用|不用管|清空|删除|重置|忽略)", 3),
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
