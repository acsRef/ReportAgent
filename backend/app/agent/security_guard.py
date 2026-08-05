from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


@dataclass
class SecurityResult:
    score: int = 0
    level: str = "LOW"
    blocked: bool = False
    matched_rules: list[str] = field(default_factory=list)
    reason: str = ""


# A-5 后半段：零宽/不可见字符——注入用它们在字母间插字规避连续字符匹配
# （ignore 中间插 U+200B）。归一化阶段一次剥净。
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def _normalize(text: str) -> str:
    """匹配前归一化（A-5 后半段绕过加固）：全角→半角 + 剥零宽字符。

    NFKC 把全角字符（ｉｇｎｏｒｅ → ignore）与兼容字符折回本位；零宽字符剥净后
    无法再拆断连续字符模式。只影响匹配副本，不改任何返回值以外的语义。
    """
    text = unicodedata.normalize("NFKC", text)
    return _ZERO_WIDTH_RE.sub("", text)


# 注入规则。英文 ignore/forget/disregard 允许中间夹 0–3 个词（此前只允许夹 1 个词，
# 导致最经典的 "ignore all previous instructions" 都漏掉——all 与 previous 两个词
# 夹在 ignore 与 instructions 之间）。中文补「指令覆盖」与「作废/失效」两类自然说法。
# 新规则都要求「指令类词 + 覆盖/失效类词」同现，纯业务查询（"之前的销售额"「对比上月」
# 「忽略空值」）不含指令类词，不会误伤——见 tests/test_security_hardening.py。
# A-5 后半段：匹配一律在 _normalize 之后；首字母字符类容忍 1gnore/f0rget 类 leet 变形。
_RULES: list[tuple[str, str, int]] = [
    # 英文「忽略/忘记/无视之前的指令」——允许中间夹 0–3 个词
    ("ignore_previous", r"[i1]gnore\s+(?:\w+\s+){0,3}(?:instructions?|rules?|system|prompts?|commands?|context)", 3),
    ("forget_instructions", r"f[o0]rget\s+(?:\w+\s+){0,3}(?:instructions?|rules?|prompts?|context)", 3),
    ("disregard_instructions", r"d[i1]sregard\s+(?:\w+\s+){0,3}(?:instructions?|rules?|prompts?|previous|prior)", 3),
    # 中文「忽略/无视…之前的…指令/规则/设定」
    ("override_instruction_cn", r"(?:忽略|无视|别管|不用管|不要管|跳过).{0,12}(?:之前|以前|上面|以上|从前|先前|原来|前面|所有|全部).{0,12}(?:prompt|提示词|指令|规则|要求|设定|约束|限制|对话|上下文)", 3),
    # 中文「之前的 prompt/指令/设定…失效/作废/无效」（本 review 的直接场景）
    ("invalidate_previous_cn", r"(?:之前|以前|上面|以上|从前|先前|原来|前面|过往).{0,12}(?:prompt|提示词|指令|规则|要求|设定|约束|对话|上下文).{0,12}(?:失效|作废|无效|不算|都不用|不用管|清空|删除|重置|忽略)", 3),
    # A-5 后半段：英文同义动词 bypass/override/circumvent（仍要求与指令类词同现）
    ("bypass_instructions_en", r"(?:bypass|by-pass|[o0]verride|circumvent)\s+(?:\w+\s+){0,3}(?:instructions?|rules?|system|prompts?|commands?|context)", 3),
    # A-5 后半段：中文「绕过/解除…指令/规则/要求」。目标词刻意不含「限制/约束/
    # 对话/上下文」——业务语境高频（「解除之前的合同限制」），防误伤优先；「设定」
    # 仅在与「你」同现时计入（「解除你之前的设定」是注入话术，而「突破之前设定的
    # 目标」是正常业务表达）。
    ("bypass_instruction_cn", r"(?:绕过|解除|摆脱|挣脱|突破).{0,12}(?:(?:之前|以前|上面|以上|所有|全部).{0,12}(?:prompt|提示词|指令|规则|要求)|你.{0,12}(?:prompt|提示词|指令|规则|要求|设定))", 3),
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

        # A-5 后半段：先归一化（全角→半角 + 剥零宽字符）再匹配，
        # 编码混淆类绕过在匹配前还原为规范形态。
        normalized = _normalize(query)
        for name, pattern, weight in _RULES:
            if re.search(pattern, normalized, re.I):
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
