"""对话压缩引擎（对标 DSH `@deepseek-ai/dsh-compaction`，数据豁免）。

**为什么有这个模块**：原实现把「压缩策略」散落在 `memory/conversation.py` 里（按条数触发、
散文摘要、字符串截断），三处缺陷见 plan `2026-11-16-compaction-dsh-alignment.md`。本模块把
策略层抽成**纯函数**，与存储/LLM 分离，便于逐条钉契约：

- `estimate_tokens` / `resolve_spec`：DSH `resolveCompactSpec` 的移植（阈值 = min(窗口×比例,
  窗口 − 预留输出 − 余量)；保留量 = 消息预算 × 比例）；
- `classify_units` / `select_fold_span`：保尾选范围，**绝不切开一问一答**（DSH 的
  tool-call/result 配对边界在对话侧的对应物就是 turn 边界）；
- `protected_fragments` / `collect_data_facts`：**数据通道**——日期/金额/百分比/编号由确定性
  正则逐字抽取，**不经过任何模型**。这是与 DSH 的唯一实质差异：DSH 靠 prompt 要求模型把数字
  抄对，ReportAgent 干脆不让模型看到数字；
- `compact_utterance` / `fold_deterministic`：无 LLM 时的兜底折法（骨架 + 数据原样）；
- `ensure_shrunk`：shrink guard——候选不比被折叠内容短就不许落库（DSH 同款守卫）。

本模块只做纯计算：不读库、不调 LLM、不写日志以外的副作用。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal, Sequence

# --- token 估算：2026-11-16 实测校准（真 provider，读 usage.prompt_tokens） --------
#
# | 样本            | 字符  | 实测 tokens | 字/token |
# |-----------------|-------|-------------|----------|
# | 中文散文        | 5 000 | 2 992       | 1.67     |
# | 中文散文        | 100 k | 59 797      | 1.67     |
# | 中文 + 数据/SQL | 5 000 | 2 011       | 2.49     |
# | 纯英文          | 5 000 | 887         | 5.64     |
#
# 旧实现一律按 3 chars/token 估：中文会被**低估 1.79 倍**（自认为只用了 52k tokens，
# 其实已 ~93k），英文被高估 1.88 倍（保守）。中文是本产品的绝对主力文本，所以拆成
# 两个系数：CJK 用实测 1.67，其余仍按 3.0（保守，且保持既有 ASCII 用例语义不变）。
CJK_CHARS_PER_TOKEN = 1.67
OTHER_CHARS_PER_TOKEN = 3.0
CHARS_PER_TOKEN = OTHER_CHARS_PER_TOKEN   # 兼容旧引用（只对 ASCII 成立）

# CJK 表意文字 + 中文标点 + 全角符号：这些几乎都一字一 token 量级
_CJK_CLASS = "\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
_CJK_RE = re.compile(f"[{_CJK_CLASS}]")

# --- DSH 默认策略参数 -------------------------------------------------------
DEFAULT_THRESHOLD_RATIO = 0.8    # DSH `DEFAULT_THRESHOLD_RATIO`
DEFAULT_RETAIN_RATIO = 0.16      # DSH `DEFAULT_RETAIN_RATIO`
MIN_HEADROOM_TOKENS = 1024       # 窗口过小时别把阈值压到 0


class CompactionConfigError(ValueError):
    """压缩配置不自洽（窗口/预留/余量/保留量互相打架）。

    对应 DSH 的 `TargetPressureConfigError`：属于**配置**错误，不是运行时错误，
    所以调用方应该告警并跳过本轮压缩，而不是把整条链路打挂。
    """


def estimate_tokens(text: str) -> int:
    """按实测比例估算 token（CJK 1.67 字/token，其余 3.0 字/token）。空串为 0。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return max(1, math.ceil(cjk / CJK_CHARS_PER_TOKEN + other / OTHER_CHARS_PER_TOKEN))


# --- 数据通道：受保护片段 ---------------------------------------------------
# 绝不能压缩的片段——用户明确要求「数字那些数据要保留原样」。
# 顺序即优先级（越具体的语义单元越先抽）：
#   1) 年月/日期，含「月/日」后缀——否则 `2024年11月` 会被截成 `2024年11`；
#   2) 裸月/日（`11月` / `3日`）——2026-11-16 真流程测试抓到的缺口：用户在长对话里说
#      「最高是 11 月」，裸月没有年份前缀，旧词表不认 → 折叠后这个月份彻底消失
#      （checkpoint 又禁止写数字）。带「月/日」后缀的才是完整语义单元，不是裸数字；
#   3) 金额/百分比（带单位）；
#   4) 长编号（订单号）。
# 仍然**不**单独匹配裸数字：它会把 `2024年11月` 切出个 `11`，且对指代消解没价值。
_PROTECTED_PATTERNS = (
    re.compile(r"\d{4}\s*[-/年]\s*\d{1,2}\s*月?(?:\s*\d{1,2}\s*[日号])?"),
    # 裸年份：真流程测试抓到的第二个缺口——「先看 2024 年华东」里的 `2024 年` 是**时间范围数据**，
    # 旧词表只认带月的完整日期，于是年份在折叠后消失（checkpoint 又不许凭空造数）。
    re.compile(r"\d{4}\s*[-/年]"),
    re.compile(r"\d{1,2}\s*[月日号]"),
    re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|万元|亿元|万|亿|元)"),
    re.compile(r"(?<!\d)\d{5,}(?!\d)"),
)


def _drop_subsumed(fragments: list[str]) -> list[str]:
    """去掉被更长片段包含的短片段（`2024年11月` 已含 `11月`，不重复记两条）。"""
    return [
        piece for piece in fragments
        if not any(other != piece and piece in other for other in fragments)
    ]


def protected_fragments(text: str) -> list[str]:
    """抽出一条文本里**必须原样保留**的片段（去重保序、去掉被更长片段包含的）。

    只认「数值类」：日期 / 月份 / 金额 / 百分比 / 编号。业务实体名（区域、品类）由结构化
    字段承载（上一轮卡），不在这里重复抽——文本里出现几次都一样，压掉不影响正确性。
    """
    seen: list[str] = []
    for pattern in _PROTECTED_PATTERNS:
        for match in pattern.finditer(text or ""):
            piece = match.group(0).strip()
            if piece and piece not in seen:
                seen.append(piece)
    return _drop_subsumed(seen)


def compact_utterance(text: str, *, keep_head: int) -> str:
    """把一条历史压成「骨架 + 全部数据原样」。

    用户原话（question）比助手确认句信息量大，所以 user 保留更长的头；
    但**两者的数字一律原样**——数字被压掉等于篡改事实。
    """
    flat = " ".join(str(text or "").split())
    if not flat:
        return ""
    head = flat[:keep_head].rstrip()
    fragments = protected_fragments(flat)
    if not fragments:
        return f"{head}…"
    return f"{head}…（数据：{'、'.join(fragments)}）"


# --- 预算规格（DSH resolveCompactSpec 移植） -------------------------------


@dataclass(frozen=True)
class CompactionSpec:
    """一次压缩的token 预算：触发阈值 + 保留量。"""

    context_window: int
    reserved_completion_tokens: int
    headroom_tokens: int
    threshold_ratio: float
    retain_ratio: float
    threshold_tokens: int
    retain_tokens: int


def resolve_spec(
    *,
    context_window: int,
    reserved_completion_tokens: int = 0,
    headroom_tokens: int | None = None,
    threshold_ratio: float = DEFAULT_THRESHOLD_RATIO,
    retain_ratio: float = DEFAULT_RETAIN_RATIO,
) -> CompactionSpec:
    """把「窗口 + 预留 + 比例」算成具体 token 预算。

    与 DSH 同构：`threshold = floor(min(window × ratio, window − 预留输出 − 余量))`。
    余量缺省按窗口的 1/8 取（下限 `MIN_HEADROOM_TOKENS`）——DSH 的 65536 是给 128k 窗口
    的绝对值，直接搬过来会把本项目的 65k 窗口压成「永不触发」，所以改成按比例。
    """
    if not isinstance(context_window, int) or context_window <= 0:
        raise CompactionConfigError(f"context_window ({context_window}) 必须是正整数")
    if reserved_completion_tokens < 0:
        raise CompactionConfigError(
            f"reserved_completion_tokens ({reserved_completion_tokens}) 不能为负"
        )
    if not (0 < threshold_ratio <= 1):
        raise CompactionConfigError(f"threshold_ratio ({threshold_ratio}) 必须在 (0, 1]")
    if not (0 < retain_ratio < 1):
        raise CompactionConfigError(f"retain_ratio ({retain_ratio}) 必须在 (0, 1)")

    headroom = headroom_tokens if headroom_tokens is not None else max(
        MIN_HEADROOM_TOKENS, context_window // 8,
    )
    if headroom < 0:
        raise CompactionConfigError(f"headroom_tokens ({headroom}) 不能为负")

    message_budget = context_window - reserved_completion_tokens
    if message_budget <= 0:
        raise CompactionConfigError(
            f"预留输出 {reserved_completion_tokens} 已吃掉 {context_window} 的窗口，没有消息预算"
        )
    pressure_budget = message_budget - headroom
    if pressure_budget <= 0:
        raise CompactionConfigError(
            f"预留输出 {reserved_completion_tokens} + 余量 {headroom} 已吃掉 {context_window} 的窗口"
        )

    threshold_tokens = math.floor(min(context_window * threshold_ratio, pressure_budget))
    retain_tokens = math.floor(message_budget * retain_ratio)
    if retain_tokens >= threshold_tokens:
        raise CompactionConfigError(
            f"retain_tokens ({retain_tokens}) 必须小于 threshold_tokens ({threshold_tokens})"
        )
    return CompactionSpec(
        context_window=context_window,
        reserved_completion_tokens=reserved_completion_tokens,
        headroom_tokens=headroom,
        threshold_ratio=threshold_ratio,
        retain_ratio=retain_ratio,
        threshold_tokens=threshold_tokens,
        retain_tokens=retain_tokens,
    )


def should_compact(
    *,
    message_count: int,
    measured_tokens: int,
    spec: CompactionSpec,
    message_ceiling: int,
) -> bool:
    """双闸触发：token 压力（DSH）+ 条数兜底（历史行为，防小消息灌水）。

    只有 token 闸在真实长对话里起作用；条数闸保留是因为本项目的消息可能极短
    （实测均值 13~28 字），纯 token 闸在小消息灌水时永远不触发。
    """
    return measured_tokens >= spec.threshold_tokens or message_count > message_ceiling


# --- 单元切分（turn 边界 = DSH 的 tool-pair 边界） -------------------------

# 过程记录 / 指针行：只有 metadata 没有正文，对「叙述」零价值，不参与压缩单元。
_SKIP_MESSAGE_TYPES = frozenset({"turn_trace", "requirement_patch"})


@dataclass(frozen=True)
class ConversationUnit:
    """一个不可再分的对话单元（默认一问一答）。

    `start` 是单元里第一条消息在**原始消息列表**中的下标——折叠边界要回写到
    `digest_msg_count`，必须能和原始行号对齐（跳过过程行后下标会漂移）。
    """

    start: int
    message_ids: tuple[int, ...]
    messages: tuple[dict, ...]
    text: str
    tokens: int


def classify_units(
    messages: Sequence[dict],
    *,
    skip_types: frozenset[str] = _SKIP_MESSAGE_TYPES,
) -> list[ConversationUnit]:
    """把消息按 turn 切成单元：遇到 user 就开新单元，assistant 归到当前单元。

    为什么按 turn 而不是按条：折叠一半的问答（只留问、不留答）比不折叠更有害，
    模型会把「用户问过」当成「已经答过」。
    """
    units: list[ConversationUnit] = []
    current: list[tuple[int, dict, str]] = []

    def flush() -> None:
        if not current:
            return
        lines = [f"{who}: {(row.get('content') or '').strip()}" for _, row, who in current]
        text = "\n".join(lines)
        units.append(ConversationUnit(
            start=current[0][0],
            message_ids=tuple(
                int(row["id"]) for _, row, _ in current if row.get("id") is not None
            ),
            messages=tuple(row for _, row, _ in current),
            text=text,
            tokens=estimate_tokens(text),
        ))
        current.clear()

    for index, message in enumerate(messages or []):
        message_type = str(message.get("message_type") or "")
        if message_type in skip_types:
            continue
        content = (message.get("content") or "").strip()
        if not content:
            continue
        who = "user" if (message.get("role") == "user" or message_type == "text") else "assistant"
        if who == "user" and current:
            flush()
        current.append((index, message, who))
    flush()
    return units


def select_fold_span(
    units: Sequence[ConversationUnit],
    *,
    retain_tokens: int,
    ceiling_messages: int,
    floor_messages: int = 3,
) -> tuple[list[ConversationUnit], list[ConversationUnit]]:
    """保尾选范围：返回 `(要折叠的单元, 原样保留的单元)`。

    从最新往回累加，同时受三道约束（任一先到就停）：

    - `retain_tokens`（DSH 的 retainTokens 预算）：尾部攒够就停；
    - `ceiling_messages`（本项目 L1 窗口）：最多原样保留这么多条消息；
    - `floor_messages`：无论如何至少留这么多条，否则模型没有「当下」的上下文。

    返回的折叠区间是**前缀**，绝不切开单元；没有可折叠内容时返回 `([], units)`。
    """
    keep: list[ConversationUnit] = []
    kept_messages = 0
    kept_tokens = 0
    for unit in reversed(list(units)):
        if kept_messages >= ceiling_messages:
            break
        fits = kept_tokens + unit.tokens <= retain_tokens
        if not fits and kept_messages >= floor_messages:
            break
        keep.insert(0, unit)
        kept_messages += len(unit.messages)
        kept_tokens += unit.tokens
    if not keep or len(keep) == len(units):
        return [], list(units)
    return list(units)[: len(units) - len(keep)], keep


def collect_data_facts(
    units: Sequence[ConversationUnit],
    *,
    max_facts: int = 200,
) -> list[str]:
    """抽取折叠区间里的**数据事实**（逐字、去重保序、不经模型）。

    上限按**条数**而不是字符数：宁可少收几条事实，也不把一个金额截成两半。
    默认值取得很宽（200）——真正的兜底是 `build_context` 的**净收益守门**：
    数据块太大导致压缩不划算时，本轮整段不压缩，而不是悄悄丢数据。
    """
    facts: list[str] = []
    for unit in units:
        for piece in protected_fragments(unit.text):
            if piece not in facts:
                facts.append(piece)
                if len(facts) >= max_facts:
                    return facts
    return facts


def render_units(units: Sequence[ConversationUnit]) -> str:
    """按原顺序渲染单元文本（`user: …` / `assistant: …`）。"""
    return "\n".join(unit.text for unit in units)


def _safe_truncate(text: str, limit: int) -> str:
    """截到 `limit` 字符，但**不切断受保护片段**（宁可少留几个字）。

    否则骨架会产出 `2024年1`（截断的日期）这类半截数据——比丢掉它更危险，因为看起来像另一个日期。
    """
    if len(text) <= limit:
        return text
    cut = limit
    for pattern in _PROTECTED_PATTERNS:
        for match in pattern.finditer(text):
            if match.start() < cut < match.end():
                cut = match.start()
                break
    return text[:cut].rstrip()


def fold_deterministic(
    units: Sequence[ConversationUnit],
    *,
    keep_head_user: int = 24,
    keep_head_assistant: int = 12,
) -> str:
    """无 LLM 的确定性折法：逐行只留骨架，**不再重复数据**。

    这是 LLM 摘要失败时的兜底——**不允许**因为一次超时就把对话上下文整块丢空。

    2026-11-16 更正两点（都是真流程测试抓出来的）：

    1. 原实现在每行尾部追加「（数据：…）」，与 `<数据事实>` 块**重复**，而重复本身会把折叠后
       体积撑得比原文还大（实测 258 > 204 tokens）。数据通道只保留一处：`<数据事实>`。
    2. 骨架按字符硬截会切出 `2024年1` 这种半截日期 → 改走 `_safe_truncate`。
    """
    lines: list[str] = []
    for unit in units:
        for row in unit.messages:
            content = " ".join((row.get("content") or "").split())
            if not content:
                continue
            is_user = row.get("role") == "user" or str(row.get("message_type") or "") == "text"
            head = keep_head_user if is_user else keep_head_assistant
            who = "user" if is_user else "assistant"
            lines.append(f"{who}: {_safe_truncate(content, head)}…")
    return "\n".join(lines)


def ensure_shrunk(*, candidate_tokens: int, shadowed_tokens: int) -> bool:
    """shrink guard：候选必须**严格小于**被折叠内容，否则本次压缩不许落库。"""
    return candidate_tokens < shadowed_tokens


# 半角 + 全角阿拉伯数字：checkpoint 里出现任何一个都算「碰了数字」。
_NUMERIC_LITERAL = re.compile(r"[0-9０-９]")


def has_numeric_literals(text: str) -> bool:
    """文本里是否出现阿拉伯数字（仅用于诊断/日志，不作为放行判据）。"""
    return bool(_NUMERIC_LITERAL.search(text or ""))


# 数值 token：整数或小数（`11` / `9631.81` / `20.0`），全角先归一成半角再抽。
_NUMBER_TOKEN = re.compile(r"\d+(?:\.\d+)?")


def numeric_tokens(text: str) -> set[str]:
    """抽出文本里的数值 token 集合（用于和「数据事实」逐字对账）。"""
    normalized = (text or "").translate(_FULLWIDTH_DIGITS)
    return set(_NUMBER_TOKEN.findall(normalized))


def _same_number(left: str, right: str) -> bool:
    """`20` 与 `20.0` 视为同一个数（同一个值，写法不同不算改写）。"""
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return left == right


def numbers_not_in_facts(summary: str, facts_text: str) -> list[str]:
    """checkpoint 里**不在数据事实中**的数字（= 编造或改写过的数值）。

    这是 2026-11-16 把「一刀切禁止数字」换掉的正确判据：模型引用 `11 月`、`9631.81 万元`
    都是合法的（数据事实里逐字存在），但一旦自己算了/四舍五入了（`9631 万元`、`1000 万元`），
    这个 token 就不在事实集合里 → 判定越界，本轮退回确定性折法。
    """
    allowed = numeric_tokens(facts_text)
    missing = [
        token for token in numeric_tokens(summary)
        if token not in allowed and not any(_same_number(token, other) for other in allowed)
    ]
    return sorted(missing)


_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９．", "0123456789.")
