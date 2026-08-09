"""接口/表字段字典检索工具：httpx 直连 ragent-py 的 /retrieve。

数据字典（表结构语义 + 接口字段字典）存放在 ragent-py 的专用知识库，
灌入由 ragent-py/mcp_server 负责；本模块是 ReportAgent 侧的读取面。
RAGENT_URL 未配置或字典库无匹配时静默降级——绝不阻塞 SQL 生成。
"""
from __future__ import annotations

import json
import logging
import os
import threading

import httpx  # 关键：模块级 `httpx.post`/`httpx.request` 调用，单测以 `monkeypatch.setattr(mod.httpx, ...)` 替换；不要改成 `from httpx import post, request`，会破坏 monkeypatch
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_TOKEN_LOCK = threading.Lock()
_token_cache: dict[str, str] = {}
_kb_id_cache: dict[str, str] = {}

_MAX_MATCH_TEXT = 400
_MAX_MATCHES = 8

# data_source_type 推断：字典块文本里若显式说「长连接/流/推送/长轮询」之类，
# 就是实时流/外部通道，不在 fact_sales / fact_returns 等事实表里。
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
        resp = httpx.post(
            f"{base}/api/v1/auth/login",
            json={"username": os.getenv("RAGENT_USER", ""), "password": os.getenv("RAGENT_PASSWORD", "")},
            timeout=10,
        )
        resp.raise_for_status()
        _token_cache[base] = resp.json()["access_token"]
        return _token_cache[base]


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


@tool
def search_interface_dictionary(query: str, top_k: int = 5) -> str:
    """在数据字典知识库中检索字段/接口/表的含义释义。
    输入：query（中文自然语言，如 'total_amount 是什么'），top_k 返回条数（默认 5）。
    输出：JSON，matches 为命中片段 [{text, source, score}]；无匹配时 matches=[] 且 note 说明；
    字典服务未配置/不可达时返回 error 字段（调用方按无字典处理，不阻塞主流程）。
    用于：用户问题涉及接口字段或不明确字段含义时查释义；写 SQL 前确认业务口径。
    不要用来找数据表——用 search_tables；不要用来执行查询——此工具只读字典文档。"""
    base = _base()
    if not base:
        return json.dumps({"error": "字典服务未配置（RAGENT_URL 为空）"}, ensure_ascii=False)
    try:
        token = _login_token(base)
        kb_id = _dict_kb_id(base, token)
        resp = httpx.request(
            "POST", f"{base}/api/v1/retrieve",
            headers={"Authorization": f"Bearer {token}"},
            json={"query": query, "kb_ids": [kb_id], "top_k": top_k},
            timeout=30,
        )
        if resp.status_code == 401:  # token 过期 → 清缓存重登一次
            original_detail = resp.text[:200]
            with _TOKEN_LOCK:
                _token_cache.pop(base, None)
            token = _login_token(base)
            resp = httpx.request(
                "POST", f"{base}/api/v1/retrieve",
                headers={"Authorization": f"Bearer {token}"},
                json={"query": query, "kb_ids": [kb_id], "top_k": top_k},
                timeout=30,
            )
            # 重登后仍失败（账号被锁、无权等）→ 按 status 翻译，别退回通用 HTTP 文案。
            # 与 ragent-py 侧 6d31a80 的 original_detail 保留模式对齐。
            if resp.status_code == 401:
                return json.dumps(
                    {"error": f"登录失败：请检查 RAGENT_USER / RAGENT_PASSWORD；原始响应：{original_detail}"},
                    ensure_ascii=False,
                )
            if resp.status_code == 403:
                return json.dumps(
                    {"error": f"无权读取字典知识库：{resp.text[:200]}"}, ensure_ascii=False,
                )
            if resp.status_code != 200:
                return json.dumps(
                    {"error": f"字典检索失败：HTTP {resp.status_code} {resp.text[:200]}"},
                    ensure_ascii=False,
                )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return json.dumps({"matches": [], "note": f"字典库无匹配：{query}"}, ensure_ascii=False)
        matches = [
            {"text": (it.get("text") or "")[:_MAX_MATCH_TEXT],
             "source": it.get("title") or it.get("document_id", ""),
             "section_path": it.get("section_path", ""),
             "score": it.get("score", 0.0),
             "data_source_type": _infer_data_source(it.get("title", ""), it.get("text", ""))}
            for it in items[:_MAX_MATCHES]
        ]
        return json.dumps({"matches": matches}, ensure_ascii=False)
    except LookupError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    except httpx.HTTPError as exc:
        logger.warning("dictionary lookup failed: %s", exc)
        return json.dumps({"error": f"字典服务不可达：{exc}"}, ensure_ascii=False)
    except Exception as exc:  # 最后防线：字典链路任何异常都不阻塞主流程
        logger.warning("dictionary lookup unexpected error: %s", exc)
        return json.dumps({"error": f"字典检索异常：{exc}"}, ensure_ascii=False)
