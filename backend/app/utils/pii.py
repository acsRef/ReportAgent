"""PII 脱敏。

把用户查询里常见的 PII（手机号 / 邮箱 / 身份证）打码，避免 PII 原样进入
LLM prompt、trace、conversations、report_version。见
docs/plans/2026-08-03-security-injection-hardening.md。

设计取舍：
- 只 mask 三类**明确模式**（手机/邮箱/身份证），不碰姓名/地址等模糊实体——
  后者易误伤业务词。本库是销售/经营分析，查询里几乎不会出现这三类 PII，
  对其 mask 不影响正常 BI 查询语义。
- 保留首尾少量字符便于辨识，其余打 `*`。
"""
from __future__ import annotations

import re

# 身份证：18 位，末位可为 X。先于手机号 mask（更长，避免手机号 pattern 命中其子串）。
_ID_RE = re.compile(r"(?<!\d)(\d{3})\d{11}(\d{3}[0-9Xx])(?!\d)")
# 手机号：11 位，1[3-9] 开头。前后用 (?<!\d)/(?!\d) 防止命中更长数字串（如身份证）的子串。
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{6}(\d{2})(?!\d)")
# 邮箱：本地名保留首字符 + ***@域名。
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def mask_pii(text: str) -> str:
    """对文本中的手机号/邮箱/身份证打码；无 PII 原样返回。"""
    if not text:
        return text
    text = _ID_RE.sub(r"\1***********\2", text)      # 保留前 3 后 4
    text = _PHONE_RE.sub(r"\1******\2", text)        # 保留前 3 后 2
    text = _EMAIL_RE.sub(lambda m: m.group(1) + "***@" + m.group(2), text)
    return text
