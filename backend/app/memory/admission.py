"""记忆准入（写入侧闸门，plan `2026-11-09-memory-admission-and-consolidation`）。

为什么需要这一层：判断「这条话该不该变成长期记忆、记给谁、记多久」原本**没有专门处理**——
`MemoryPolicy.extract_preference` 只答「像不像偏好」，而写入端把结果硬编码成
`stable_preference / scope=user / 无 expires_at`。于是：

- 「以后都用柱状图」与「这次先按周看」得到完全相同的生命周期；
- `temporary_preference` 在生产里**永远不产生**（读取侧白名单里躺着它，写入侧没人写）。

判据必须**确定性**（宪法 §五：Confidence 不让模型拍）。本模块只做两件纯函数的事：

1. `classify_scope(text)` —— 看**作用域标记**（不是时间指示词）；
2. `is_admissible(text)` —— 值不值得记（闸 1：空/寒暄/一次性任务参数/单条数据引用/提问）。

注意与 `policy._TEMPORAL_KEYWORDS` 的区别：那是「昨天/今天/本月」这类**时间指示词**，
用来判「是不是关于某个时间点的陈述」；判**长期 vs 会话**看的是「以后/默认」vs「这次/先/暂时」。
两者混用会得出荒谬结论（「最近销售额怎么样」不该是临时偏好）。
"""
from __future__ import annotations

from dataclasses import dataclass

# 长期标记：说了就是「以后都这样」——必须当轮生效，不等批处理
LONG_TERM_MARKERS: tuple[str, ...] = (
    "以后", "今后", "默认", "每次", "一直", "始终", "都按", "都用", "都看",
    "always", "default",
)

# 会话标记：说了就是「这一次」——当轮生效，但绑定 session 且会过期
SESSION_MARKERS: tuple[str, ...] = (
    "这次", "本次", "这一回", "这一轮", "暂时", "临时", "先按", "先看", "先这么",
    "今天先", "就按这次",
)

# 会话级默认存活时长。24h 的理由：够覆盖「一次分析任务的连续追问」，
# 又不至于让「这次先按周看」在下周还生效。
DEFAULT_SESSION_TTL_HOURS = 24

# 纯寒暄/无信息量的文本不值得进记忆（闸 1，完整版见 _REJECT_RULES）
_GREETING_PREFIXES = ("你好", "您好", "谢谢", "多谢", "在吗", "hi", "hello", "thanks")
_MIN_CONTENT_CHARS = 4

# 闸 1 的拒绝规则（顺序决定 reason，便于排查）。每条都要求**确定性命中**，
# 宁可放过（留成 candidate）也不能误杀——误杀的代价是「该记的口径永远丢了」。
_REJECT_RULES: tuple[tuple[str, str], ...] = (
    # ① 一次性任务参数：带具体年份/月份/区县的任务句，是 Session State 不是记忆
    ("task-parameter", r"(?:看|查|统计|分析|拉|跑).{0,6}(?:19|20)\d{2}\s*年"),
    ("task-parameter", r"(?:看|查|统计|分析|拉|跑).{0,8}(?:\d{1,2}\s*月|本周|上周|昨天|今天)"),
    # ② 单条数据引用：没有跨会话价值的指代
    ("deictic-reference", r"(?:这个|那个|这条|那条|刚才|上面)(?:数字|数|值|结果|数据)"),
    # ③ 提问句（不是陈述）：问题本身不该变成记忆
    ("question", r"^(?:为什么|怎么|多少|哪些|哪个|是否|能不能|可以吗)"),
)


def _reject_reason(text: str) -> str | None:
    import re

    stripped = (text or "").strip()
    if not stripped:
        return "empty"
    lowered = stripped.lower()
    if any(lowered.startswith(prefix) for prefix in _GREETING_PREFIXES) and len(stripped) <= 6:
        return "greeting"
    if len(stripped) < _MIN_CONTENT_CHARS:
        return "too-short"
    for reason, pattern in _REJECT_RULES:
        if re.search(pattern, stripped):
            return reason
    return None


def is_admissible(text: str) -> tuple[bool, str]:
    """闸 1：值不值得记。

    顺序有意：先判寒暄再看长度——「你好」两个字符，如果先判长度会得到
    `too-short`，但真实理由是「这是寒暄」，日志/排查要能读到正确原因。

    完整拒绝表（2026-11-09 用户批准）：
    - `task-parameter`：一次性任务参数（「看下 2024 年」「查一下 3 月」）→ Session State；
    - `deictic-reference`：单条数据引用（「这个数字」）→ 没有跨会话价值；
    - `question`：提问句（「为什么跌了」）→ 问题不是事实；
    - `greeting` / `too-short` / `empty`。
    """
    reason = _reject_reason(text)
    if reason is not None:
        return False, reason
    return True, "ok"


@dataclass(frozen=True)
class Admission:
    """一条候选的准入结论。

    - `status`: 热路径直接写 `active` 还是先落 `candidate`（等批处理）；
    - `scope` / `memory_type` / `ttl_hours`: 生命周期三件套；
    - `reason`: 落日志与 run 记账用，出问题时能回答「为什么它是临时的」。
    """

    scope: str
    memory_type: str
    status: str
    ttl_hours: int | None
    reason: str

    @property
    def is_long_term(self) -> bool:
        return self.reason == "long-term-marker"

    @property
    def writes_active(self) -> bool:
        return self.status == "active"


def _hit(text: str, markers: tuple[str, ...]) -> str | None:
    for marker in markers:
        if marker in text:
            return marker
    return None




def classify_scope(text: str) -> Admission:
    """闸 2：按作用域标记决定「记给谁、记多久、要不要等批处理」。

    三档（顺序敏感：长期标记优先，一句话里同时有「以后」和「先按」时按长期算，
    因为「以后先按周看」的语义主体是「以后」）：

    | 档 | 标记 | 结论 |
    |---|---|---|
    | 长期 | 以后/默认/每次/一直 | `active` + `scope=user` + 无过期 |
    | 会话 | 这次/先按/暂时/本次 | `active` + `scope=session` + 24h 过期 |
    | 无标记 | —— | `candidate`（不写 active，等批处理攒证据） |
    """
    body = text or ""
    long_hit = _hit(body, LONG_TERM_MARKERS)
    if long_hit:
        return Admission(
            scope="user", memory_type="stable_preference", status="active",
            ttl_hours=None, reason="long-term-marker",
        )
    session_hit = _hit(body, SESSION_MARKERS)
    if session_hit:
        return Admission(
            scope="session", memory_type="temporary_preference", status="active",
            ttl_hours=DEFAULT_SESSION_TTL_HOURS, reason="session-marker",
        )
    return Admission(
        scope="user", memory_type="stable_preference", status="candidate",
        ttl_hours=None, reason="no-scope-marker",
    )
