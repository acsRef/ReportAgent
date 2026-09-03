"""接口/表字段字典检索工具：MCP-first + flag-gated HTTP fallback。

数据字典（表结构语义 + 接口字段字典）存放在 ragent-py 的专用知识库，
灌入由 ragent-py/mcp_server 负责；本模块是 ReportAgent 侧的读取面。

P2 RAG/MCP Boundary 改造（docs/plans/2026-08-26-p2-rag-mcp-boundary.md）：
  - 正路走 MCP client `search_dictionary`；失败时 flag-gated HTTP fallback
    （PHASE2_MCP_ONLY 未锁 + UNAVAILABLE 时走原 httpx /retrieve 路径）。
  - 失败语义五分类（mcp_errors.MCPErrorCode）：
      - MCP_INVALID_RESPONSE → 显式 error JSON（不 retry 不 fallback）
      - MCP_UNAVAILABLE flag-locked → 显式 error JSON 含 MCP code
      - HTTP fallback 异常 → 既有错误形状（"字典服务不可达…"）
  - 工具契约对 Agent 不变：成功 matches=[{text, source, section_path, score,
    data_source_type}]，失败 {"error": ...}。
"""
from __future__ import annotations

import json
import logging
import os
import threading

import httpx  # 关键：模块级 `httpx.post`/`httpx.request` 调用，单测以 `monkeypatch.setattr(mod.httpx, ...)` 替换；不要改成 `from httpx import post, request`，会破坏 monkeypatch
from langchain_core.tools import tool

from app.tools import ragent_token_cache
from app.tools.mcp_client import (
    _fallback_allowed,
    _validate_matches_contract,
    get_rag_mcp_client,
)
from app.tools.mcp_errors import MCPBoundaryError

logger = logging.getLogger(__name__)

_TOKEN_LOCK = threading.Lock()
_token_cache: dict[str, str] = {}
_kb_id_cache: dict[str, str] = {}

_MAX_MATCH_TEXT = 400
_MAX_MATCHES = 8

# data_source_type 推断：字典块文本里若显式说「长连接/流/推送/长轮询」之类，
# 就是实时流/外部通道，不在 fact_orders / fact_payments 等事实表里。
# LLM 看到这个标记就不该写 SQL，而应在 requirement 里建议接入实时数据。
_STREAM_KEYWORDS = (
    "长连接", "websocket", "sse ", "server-sent", "server sent",
    "长轮询", "long poll", "long-poll", "推送", "实时", "push", "stream",
)


def _infer_data_source(title: str, text: str) -> str:
    blob = f"{title or ''}\n{text or ''}".lower()
    for kw in _STREAM_KEYWORDS:
        if kw in blob:
            return "stream"
    return "table"


def _base() -> str:
    return os.getenv("RAGENT_URL", "").rstrip("/")


def _login_token(base: str) -> str:
    with _TOKEN_LOCK:
        cached = _token_cache.get(base)
        if cached:
            return cached
        # 跨进程共享缓存：命中则直接复用（避免多次进程各自登录撞限流）。
        shared = ragent_token_cache.get_token(base)
        if shared:
            _token_cache[base] = shared
            return shared
        resp = httpx.post(
            f"{base}/api/v1/auth/login",
            json={"username": os.getenv("RAGENT_USER", ""), "password": os.getenv("RAGENT_PASSWORD", "")},
            timeout=10,
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        _token_cache[base] = token
        ragent_token_cache.set_token(base, token)
        return token


def _dict_kb_id(base: str, token: str) -> str:
    """按名解析字典 KB id（GET /api/v1/kb），缓存。"""
    with _TOKEN_LOCK:
        cached = _kb_id_cache.get(base)
        if cached:
            return cached
    kb_name = os.getenv("DICT_KB_NAME", "数据字典")
    resp = httpx.request("GET", f"{base}/api/v1/kb",
                         headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    for kb in resp.json():
        if kb.get("name") == kb_name:
            with _TOKEN_LOCK:
                _kb_id_cache[base] = kb["id"]
            return kb["id"]
    raise LookupError(f"ragent-py 中不存在名为 {kb_name} 的知识库")


def _items_to_matches(items: list[dict]) -> list[dict]:
    """将 MCP/HTTP 检索的 items 列表规范化为工具层 matches。

    字段裁剪（Q8 决策 3 稳定字段集）：
      - text（截 400）、source（title 优先，回退 document_id）、section_path、
        score、data_source_type（表名命中→table，流式关键字→stream）
      - 内部字段（chunk_id / document_id / kb_id 等）不外露
    """
    matches = []
    for it in items[:_MAX_MATCHES]:
        if not isinstance(it, dict):
            continue
        matches.append({
            "text": (it.get("text") or "")[:_MAX_MATCH_TEXT],
            "source": it.get("title") or it.get("document_id", ""),
            "section_path": it.get("section_path", ""),
            "score": it.get("score", 0.0),
            "data_source_type": _infer_data_source(
                it.get("title", ""), it.get("text", "")
            ),
        })
    return matches


def _search_dict_via_mcp(query: str, top_k: int) -> list[dict]:
    """正路：MCP `search_dictionary` → 业务契约校验后的 items。

    业务契约校验（review 第 2 轮 P1 修订）：_validate_matches_contract 检查
    result 形态 + 每个 item 含 text(str) + score(numeric)；失败 → MCPBoundaryError
    (INVALID_RESPONSE)，由 search_interface_dictionary dispatcher 决定 error JSON / fallback。

    Raises:
        MCPBoundaryError: 失败时显式分类。
    """
    result = get_rag_mcp_client().call_tool(
        "search_dictionary", {"query": query, "top_k": top_k}
    )
    return _validate_matches_contract(result)


def _search_dict_http(query: str, top_k: int) -> tuple[list[dict], bool]:
    """HTTP fallback：原 httpx /api/v1/retrieve；返回 (items, has_degraded)。

    失败抛错（httpx.HTTPError / LookupError / 401 重登一次后仍败 → 状态码翻译）。
    """
    base = _base()
    if not base:
        raise RuntimeError("字典服务未配置（RAGENT_URL 为空）")
    token = _login_token(base)
    kb_id = _dict_kb_id(base, token)

    def _retrieve():
        return httpx.request(
            "POST", f"{base}/api/v1/retrieve",
            headers={"Authorization": f"Bearer {token}"},
            json={"query": query, "kb_ids": [kb_id], "top_k": top_k},
            timeout=30,
        )

    resp = _retrieve()
    if resp.status_code == 401:  # token 过期 → 失效共享缓存 + 重登一次
        original_detail = resp.text[:200]
        with _TOKEN_LOCK:
            _token_cache.pop(base, None)
        ragent_token_cache.invalidate(base)
        token = _login_token(base)
        resp = _retrieve()
        # 重登后仍失败（账号被锁、无权等）→ 按 status 翻译，别退回通用 HTTP 文案。
        # 与 ragent-py 侧 6d31a80 的 original_detail 保留模式对齐。
        if resp.status_code == 401:
            raise RuntimeError(
                f"登录失败：请检查 RAGENT_USER / RAGENT_PASSWORD；原始响应：{original_detail}"
            )
        if resp.status_code == 403:
            raise RuntimeError(f"无权读取字典知识库：{resp.text[:200]}")
        if resp.status_code != 200:
            raise RuntimeError(f"字典检索失败：HTTP {resp.status_code} {resp.text[:200]}")
    resp.raise_for_status()
    body = resp.json()
    return body.get("items") or [], bool(body.get("degraded"))


@tool
def search_interface_dictionary(query: str, top_k: int = 5) -> str:
    """在数据字典知识库中检索字段/接口/表的含义释义。
    输入：query（中文自然语言，如 'order_amount 是什么'），top_k 返回条数（默认 5）。
    输出：JSON，matches 为命中片段 [{text, source, score}]；无匹配时 matches=[] 且 note 说明；
    字典服务未配置/不可达时返回 error 字段（调用方按无字典处理，不阻塞主流程）。
    用于：用户问题涉及接口字段或不明确字段含义时查释义；写 SQL 前确认业务口径。
    不要用来找数据表——用 search_tables；不要用来执行查询——此工具只读字典文档。

    P5 仍保留 HTTP fallback 代码但默认不走（PHASE2_MCP_ONLY 默认 ON）；仅测试显式设 flag OFF 时可达。
    失败语义：
      - MCP 成功 → 返回 MCP items 规范化结果
      - MCPBoundaryError(INVALID_RESPONSE) → error JSON（不 fallback）
      - MCPBoundaryError(UNAVAILABLE) + _fallback_allowed → HTTP fallback
      - MCPBoundaryError(UNAVAILABLE) + flag ON → error JSON
      - 其它 Exception → 向上抛
    """
    try:
        items = _search_dict_via_mcp(query, top_k)
    except MCPBoundaryError as exc:
        if exc.code.value == "MCP_INVALID_RESPONSE" or not _fallback_allowed():
            logger.warning(
                "dictionary lookup failed (MCP, no fallback): %s [%s]",
                exc.detail, exc.code.value,
            )
            return json.dumps(
                {"error": f"{exc.code.value}: {exc.detail}"},
                ensure_ascii=False,
            )
        logger.warning(
            "MCP unavailable, falling back to HTTP: %s [%s]",
            exc.detail, exc.code.value,
        )
        try:
            items, _has_degraded = _search_dict_http(query, top_k)
        except LookupError as exc_lookup:
            return json.dumps({"error": str(exc_lookup)}, ensure_ascii=False)
        except httpx.HTTPError as exc_http:
            logger.warning("dictionary lookup failed: %s", exc_http)
            return json.dumps({"error": f"字典服务不可达：{exc_http}"}, ensure_ascii=False)
        except Exception as exc_other:
            logger.warning("dictionary lookup HTTP fallback failed: %s", exc_other)
            return json.dumps({"error": str(exc_other)}, ensure_ascii=False)
    # 非 MCPBoundaryError（_search_dict_via_mcp 抛 RuntimeError/KeyError 等真程序 bug）
    # → 向上抛，由 LangChain tool runtime 暴露给调用方；与 faq_tools catch 收紧一致。

    if not items:
        return json.dumps({"matches": [], "note": f"字典库无匹配：{query}"}, ensure_ascii=False)
    matches = _items_to_matches(items)
    return json.dumps({"matches": matches}, ensure_ascii=False)
