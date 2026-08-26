from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain_core.tools import tool

from app.tools.mcp_client import _fallback_allowed, get_rag_mcp_client
from app.tools.mcp_errors import MCPBoundaryError

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


def _search_faq_rows(query: str, top_k: int = 3) -> list[dict]:
    """纯检索：按中文业务词语返回 top-K 条 {question, sql, note, tables, score}。

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


def _mcp_search_faq(query: str, top_k: int) -> list[dict]:
    """正路：经统一 MCP client 调 ragent-py `search_faq`，返回规范化 rows。

    P2：使用 get_rag_mcp_client().call_tool（统一单例，替代旧
    mcp_faq_client.search_faq method 桥接）；mcp_client 已做响应分类与字段透传。

    空命中（matches=[]）是合法返回，不抛错。
    非 MCPBoundaryError 异常（如 parse bug）必须上抛——收紧 catch 在 search_faq 调用方。
    """
    result = get_rag_mcp_client().call_tool(
        "search_faq", {"query": query, "top_k": top_k}
    )
    items = result.get("matches") or []
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        # ragent-py 检索返回 chunk 文本（含问题+SQL+要点），归一到 {text} 供注入
        rows.append({
            "question": (it.get("title") or it.get("question") or "")[:80],
            "text": (it.get("text") or "")[:2000],
            "score": float(it.get("score") or 0.0),
        })
    return rows


@tool
def search_faq(query: str, top_k: int = 3) -> str:
    """在 Schema FAQ 知识库中检索最常见分析问题的 SQL 模板与业务口径要点。

    输入：query（中文自然语言，如 '区域退货率'、'毛利率'），top_k 返回条数（默认 3）。
    输出：JSON，matches 为命中案例 [{question, sql, note, tables, score}]；无匹配时 matches=[]。
    用于：写 SQL 前查「这类问题以前怎么算」——业务口径（毛利率/退货率/出勤率/库存周转等）
    和常见分组/排序模板都在这里。
    不要用来找数据表——用 search_tables；不要用来查业务数据行——此工具只读 FAQ 知识库。

    P2：MCP-first + catch 收紧（Q6 决议）。
      - MCP 成功 → 走 MCP 路径
      - MCP_INVALID_RESPONSE → 返回 {"error": "MCP_MCP_INVALID_RESPONSE: ..."}
        （不 fallback；决策 4：协议错重试同结果，flag 状态无关）
      - MCP_UNAVAILABLE + flag 未锁 → 降级本地 seed（既有契约）
      - MCP_UNAVAILABLE + flag 锁定 → 返回 {"error": "MCP_MCP_UNAVAILABLE: ..."}
      - 其它 Exception（非 MCPBoundaryError，如 parse bug）→ 向上抛，让上游记录 + 降级
    """
    try:
        rows = _mcp_search_faq(query, top_k)
    except MCPBoundaryError as exc:
        # INVALID_RESPONSE：决策 4 明令「不 fallback（重试同结果）」
        if exc.code.value == "MCP_INVALID_RESPONSE":
            logger.warning(
                "FAQ search failed (MCP_INVALID_RESPONSE, no fallback): %s",
                exc.detail,
            )
            return json.dumps(
                {"error": f"MCP_{exc.code.value}: {exc.detail}"},
                ensure_ascii=False,
            )
        # UNAVAILABLE：flag-gated fallback
        if not _fallback_allowed():
            logger.warning(
                "FAQ search failed (MCP_UNAVAILABLE, no fallback): %s",
                exc.detail,
            )
            return json.dumps(
                {"error": f"MCP_{exc.code.value}: {exc.detail}"},
                ensure_ascii=False,
            )
        logger.warning(
            "MCP FAQ unavailable, falling back to local seed: %s [%s]",
            exc.detail, exc.code.value,
        )
        rows = _search_faq_rows(query, top_k)
    return json.dumps({"matches": rows}, ensure_ascii=False)
