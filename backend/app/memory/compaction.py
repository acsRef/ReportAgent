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

# 与 `app/context/assembler.py` 同口径：1 token ≈ 3 chars（粗估，够用于阈值判断）。
CHARS_PER_TOKEN = 3

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
    """字符数估算 token（1 token ≈ 3 chars）。空串为 0。"""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


# --- 数据通道：受保护片段 ---------------------------------------------------
# 绝不能压缩的片段——用户明确要求「数字那些数据要保留原样」。
# 只认三类完整语义单元（顺序即优先级）：
#   1) 年月/日期，含「月/日」后缀——否则 `2024年11月` 会被截成 `2024年11`；
#   2) 金额/百分比（带单位）；
#   3) 长编号（订单号）。
# 刻意**不**单独匹配裸数字：它会和上面几条互相切碎（`2024年11月` 里抠出个 `11`），
# 而裸数字对「指代消解」没有价值。
_PROTECTED_PATTERNS = (
    re.compile(r"\d{4}\s*[-/年]\s*\d{1,2}\s*月?(?:\s*\d{1,2}\s*[日号])?"),
    re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|万元|亿元|万|亿|元)"),
    re.compile(r"(?<!\d)\d{5,}(?!\d)"),
)


def protected_fragments(text: str) -> list[str]:
    """抽出一条文本里**必须原样保留**的片段（去重保序）。

    只认「数值类」：日期 / 金额 / 百分比 / 编号。业务实体名（区域、品类）由结构化字段
    承载（上一轮卡），不在这里重复抽——文本里出现几次都一样，压掉不影响正确性。
    """
    seen: list[str] = []
    for pattern in _PROTECTED_PATTERNS:
        for match in pattern.finditer(text or ""):
            piece = match.group(0).strip()
            if piece and piece not in seen:
                seen.append(piece)
    return seen


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
    max_facts: int = 40,
) -> list[str]:
    """抽取折叠区间里的**数据事实**（逐字、去重保序、不经模型）。

    上限按**条数**而不是字符数：宁可少收几条事实，也不把一个金额截成两半。
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


def fold_deterministic(
    units: Sequence[ConversationUnit],
    *,
    keep_head_user: int = 24,
    keep_head_assistant: int = 12,
) -> str:
    """无 LLM 的确定性折法：逐行截骨架，数据片段原样附在行尾。

    这是 LLM 摘要失败时的兜底——**不允许**因为一次超时就把对话上下文整块丢空。
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
            lines.append(f"{who}: {compact_utterance(content, keep_head=head)}")
    return "\n".join(lines)


def ensure_shrunk(*, candidate_tokens: int, shadowed_tokens: int) -> bool:
    """shrink guard：候选必须**严格小于**被折叠内容，否则本次压缩不许落库。"""
    return candidate_tokens < shadowed_tokens


# 半角 + 全角阿拉伯数字：checkpoint 里出现任何一个都算越界。
_NUMERIC_LITERAL = re.compile(r"[0-9０-９]")


def has_numeric_literals(text: str) -> bool:
    """checkpoint 是否越界写了数值。

    摘要模型**必须读得到**被折叠的原文（否则总结不了），所以「不让模型看到数字」做不到；
    能做的是**出口闸**：只要它把数字写进 checkpoint，本轮就判定越界、退回确定性折法
    （确定性折法的数字是逐字复制的，不存在改写）。这样 checkpoint 永远只承载叙述，
    数值只走 `<数据事实>` 通道。
    """
    return bool(_NUMERIC_LITERAL.search(text or ""))
