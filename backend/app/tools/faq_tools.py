from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# FAQ 知识库单一数据源（与 mcp_schema_server/registry.py 读取同一份 JSON）。
# 基于本文件位置定位仓库根，再定位 backend/scripts/schema_faq.json——与 cwd 无关。
_FAQ_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "schema_faq.json"

_FAQ_ENTRIES: list[dict] | None = None


def _load_faq() -> list[dict]:
    """惰性加载 FAQ 知识库。文件缺失/损坏降级为空列表——SQL 生成主流程不受影响。"""
    global _FAQ_ENTRIES
    if _FAQ_ENTRIES is not None:
        return _FAQ_ENTRIES
    try:
        if _FAQ_PATH.exists():
            with open(_FAQ_PATH, encoding="utf-8") as f:
                _FAQ_ENTRIES = json.load(f)
    except Exception as exc:
        logger.warning("Failed to load schema FAQ %s: %s", _FAQ_PATH, exc)
    if _FAQ_ENTRIES is None:
        _FAQ_ENTRIES = []
    return _FAQ_ENTRIES


def search_faq(query: str, top_k: int = 3) -> list[dict]:
    """按中文业务词语检索 FAQ 知识库，返回 top-K 条 {question, sql, note, tables, score}。

    scoring：每条目的 keywords 中凡是查询里出现的子串均 +3（中国话按子串匹配，
    不做分词）；question 里含查询核心词轻 +1。空/无命中返回 []（调用方应安全降级）。
    """
    entries = _load_faq()
    if not query or not query.strip() or not entries:
        return []

    qlower = query.lower()
    scored: list[tuple[float, dict]] = []
    for e in entries:
        score = 0.0
        for kw in (e.get("keywords", []) or []):
            if isinstance(kw, str) and kw and kw.lower() in qlower:
                score += 3.0
        q_terms = set(str(e.get("question", "")).lower().replace(",", " ").split())
        for term in q_terms:
            if term and len(term) > 1 and term in qlower:
                score += 1.0
        if score > 0:
            scored.append((score, e))

    scored.sort(key=lambda x: -x[0])
    return [
        {
            "question": e.get("question", ""),
            "sql": e.get("sql", ""),
            "note": e.get("note", ""),
            "tables": e.get("tables", []),
            "score": round(score, 2),
        }
        for score, e in scored[:top_k]
    ]