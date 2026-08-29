from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.agent.prompts import build_intent_classify_prompt
from app.llm import call_llm
from app.utils.text import safe_json_parse


class IntentKind(str, Enum):
    CHITCHAT = "chitchat"
    REPORT = "report"
    INTERFACE = "interface"
    DASHBOARD = "dashboard"
    UNKNOWN = "unknown"


@dataclass
class IntentResult:
    kind: IntentKind
    reason: str
    confidence: float = 0.5


# ── Stage 1 关键词快路径（无 LLM / 无 HTTP） ──

_CHITCHAT_KEYWORDS = (
    "你好", "谢谢", "感谢", "再见", "你是谁", "你能做什么", "在吗",
    "hi", "hello", "哈喽", "帮忙吗", "会什么",
)
_DASHBOARD_KEYWORDS = ("看板", "驾驶舱", "大屏", "dashboard", "概览")
# Stage 2 外部接口——只保留强信号词（弱词如"推送/实时"在报表语境常见，易误判，
# 交给字典命中/LLM 判定）。
_INTERFACE_KEYWORDS = (
    "接口", "websocket", "长连接", "长轮询", "外部服务", "消息流", "事件流", "sse",
)


def classify_intent(user_query: str, dict_hit: bool = False) -> IntentResult:
    """工作流式意图分类：廉价关键词 → 字典命中 → LLM 兜底。

    三段式（由廉到贵）：
      Stage 1  关键词快路径：闲聊 / 看板。
      Stage 2  强接口关键词 → 外部接口。
      Stage 3  字典有命中 → 报表（数据库/字段查询，需求分析处理，含字段澄清与
               stream 数据标注；不会因字段来自推送源就误判成外部接口意图）。
      其余     LLM 分类：报表 / 外部接口 / 闲聊 / 其他。
    """
    q = (user_query or "").strip().lower()
    if not q:
        return IntentResult(IntentKind.UNKNOWN, "空查询", 0.0)

    if any(k in q for k in _CHITCHAT_KEYWORDS):
        return IntentResult(IntentKind.CHITCHAT, "闲聊关键词命中", 0.9)
    if any(k in q for k in _DASHBOARD_KEYWORDS):
        return IntentResult(IntentKind.DASHBOARD, "看板关键词命中", 0.7)
    if any(k in q for k in _INTERFACE_KEYWORDS):
        return IntentResult(IntentKind.INTERFACE, "外部接口关键词命中", 0.8)
    if dict_hit:
        return IntentResult(IntentKind.REPORT, "字典命中数据库/字段查询", 0.6)

    return _llm_classify(q)


def _llm_classify(q: str) -> IntentResult:
    """Stage 3：LLM 语义分类。失败降级为 REPORT（保主流程）。"""
    prompt = build_intent_classify_prompt(q)
    raw = call_llm(prompt, max_tokens=200)
    parsed = safe_json_parse(raw) if isinstance(raw, str) else raw
    if not isinstance(parsed, dict):
        parsed = {}
    kind = str((parsed or {}).get("kind", "")).lower()
    try:
        conf = float((parsed or {}).get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    reason = str((parsed or {}).get("reason", ""))[:100]
    mapping = {
        "report": IntentKind.REPORT,
        "interface": IntentKind.INTERFACE,
        "chitchat": IntentKind.CHITCHAT,
        "other": IntentKind.UNKNOWN,
    }
    return IntentResult(mapping.get(kind, IntentKind.REPORT), reason or "LLM 判定", conf)