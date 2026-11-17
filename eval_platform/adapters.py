"""被评测服务的 HTTP 适配层。

原则（plan `2026-11-17-eval-platform.md`）：

- **只读调接口**，不 import 两个项目的代码（它们各自有独立的 venv / 依赖）；
- **优雅降级**：服务没起、认证失败、超时，都归一成 `ServiceUnavailable`，
  由上层把页面标成「未启动」，而不是把整页打挂；
- 每次调用都带 `timeout` 与实测 `latency_ms`——延迟本身也是评测指标。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

RA_BASE = os.getenv("RA_BASE", "http://127.0.0.1:8100")
RAG_BASE = os.getenv("RAG_BASE", "http://127.0.0.1:8000")
# 手动兜底：登录被限流（ragent 10 次/5 分钟/IP）时，可用 env 直接注入 token
RA_TOKEN_ENV = os.getenv("RA_TOKEN", "").strip()
RAG_TOKEN_ENV = os.getenv("RAG_TOKEN", "").strip()
RA_USERNAME = os.getenv("RA_USERNAME", "admin")
RA_PASSWORD = os.getenv("RA_PASSWORD", "admin123")
RAG_USERNAME = os.getenv("RAG_USERNAME", "admin")
RAG_PASSWORD = os.getenv("RAG_PASSWORD", "admin123")
RAG_KB_IDS = [x.strip() for x in os.getenv("RAG_KB_IDS", "").split(",") if x.strip()]
RAG_KB_NAME_HINT = os.getenv("RAG_KB_NAME_HINT", "三一")
HTTP_TIMEOUT = float(os.getenv("EVAL_HTTP_TIMEOUT", "90"))


# 进程内 token 缓存：ragent 有「登录尝试过于频繁」限流（实测 429），
# 每次调用都登录会被限；缓存 9 分钟，过期再登录。
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_TTL_S = 540.0


def _cached_token(key: str) -> str | None:
    item = _TOKEN_CACHE.get(key)
    if item and time.monotonic() - item[1] < _TOKEN_TTL_S:
        return item[0]
    return None


def _remember_token(key: str, token: str) -> None:
    _TOKEN_CACHE[key] = (token, time.monotonic())


class ServiceUnavailable(RuntimeError):
    """被评测服务不可用（未启动 / 认证失败 / 超时）。"""


@dataclass
class CallResult:
    """一次实时调用的观测值。"""

    data: dict[str, Any]
    latency_ms: float
    raw: dict[str, Any] = field(default_factory=dict)


# --- ReportAgent ------------------------------------------------------------


def _parse_sse_events(text: str) -> list[dict[str, Any]]:
    """把 `event: x\\ndata: {...}` 帧解析成 dict 列表（ReportAgent 的线格式）。"""
    events: list[dict[str, Any]] = []
    # sse-starlette 用 CRLF 分隔帧；不归一就会把整条流当成一帧，只解析出最后一个事件
    for frame in text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n"):
        name = ""
        data_lines: list[str] = []
        for line in frame.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not name:
            continue
        payload: Any = "\n".join(data_lines)
        if payload:
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                pass
        events.append({"event": name, "data": payload})
    return events


async def reportagent_health() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{RA_BASE}/health")
        return {"up": resp.status_code == 200, "base_url": RA_BASE, "detail": resp.text[:120]}
    except Exception as exc:  # noqa: BLE001 - 任何连接问题都算不可用
        return {"up": False, "base_url": RA_BASE, "detail": str(exc)[:160]}


async def reportagent_login() -> str:
    """ReportAgent 的 /chat 也要 JWT（默认 admin/admin123）；token 进程内缓存。"""
    if RA_TOKEN_ENV:
        return RA_TOKEN_ENV
    cached = _cached_token("reportagent")
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{RA_BASE}/api/v1/auth/login",
                json={"username": RA_USERNAME, "password": RA_PASSWORD},
            )
        if resp.status_code != 200:
            raise ServiceUnavailable(f"ra login {resp.status_code}: {resp.text[:160]}")
        body = resp.json()
        token = body.get("access_token") or body.get("token")
        if not token:
            raise ServiceUnavailable(f"ra login 返回里没有 token：{list(body)}")
        _remember_token("reportagent", str(token))
        return str(token)
    except ServiceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ServiceUnavailable(f"ReportAgent 登录失败：{exc}") from exc


async def reportagent_requirement(query: str, *, session_id: str) -> CallResult:
    """跑一轮需求理解（到 `requirement` 事件为止，不 confirm）。

    为什么只跑到这里：confirm 会真跑 SQL + 报告（20–40s/例），不适合"实时点一下"；
    执行/报告两层的期望在平台里显式标 **deferred**，不给假 pass。
    """
    body = {"user_query": query, "session_id": session_id, "mode": "new"}
    token = await reportagent_login()
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            async with client.stream(
                "POST", f"{RA_BASE}/api/v1/chat", json=body,
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status_code != 200:
                    raise ServiceUnavailable(f"chat {resp.status_code}: {(await resp.aread())[:200]!r}")
                # 整条流读完（它自己会在 done 后结束）：中途按行 break 会切在
                # `event: requirement` 与 `data:` 之间，需求卡就丢了。
                chunks: list[str] = []
                async for chunk in resp.aiter_text():
                    chunks.append(chunk)
        latency = (time.monotonic() - started) * 1000
        events = _parse_sse_events("".join(chunks))
        requirement = next((e["data"] for e in events if e["event"] == "requirement"), None)
        error = next((e["data"] for e in events if e["event"] == "error"), None)
        phase = next((e["data"] for e in events if e["event"] == "phase"), None)
        if requirement is None and error is None:
            raise ServiceUnavailable("SSE 里既没有 requirement 也没有 error（超时或服务异常）")
        return CallResult(
            data={"requirement": requirement, "error": error, "phase": phase},
            latency_ms=round(latency, 1),
            raw={"event_names": [e["event"] for e in events]},
        )
    except ServiceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ServiceUnavailable(f"ReportAgent 调用失败：{exc}") from exc


# --- ragent-py --------------------------------------------------------------


async def ragent_health() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{RAG_BASE}/health")
        return {"up": resp.status_code == 200, "base_url": RAG_BASE, "detail": resp.text[:120]}
    except Exception as exc:  # noqa: BLE001
        return {"up": False, "base_url": RAG_BASE, "detail": str(exc)[:160]}


async def ragent_login(force: bool = False) -> str:
    """登录拿 JWT（默认 admin/admin123，可用 env 覆盖）；token 进程内缓存。

    ragent 对登录有频率限制（实测连续调用会 429「登录尝试过于频繁」），所以缓存 + 429 重试一次。
    """
    if RAG_TOKEN_ENV and not force:
        return RAG_TOKEN_ENV
    if not force:
        cached = _cached_token("ragent")
        if cached:
            return cached
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{RAG_BASE}/api/v1/auth/login",
                json={"username": RAG_USERNAME, "password": RAG_PASSWORD},
            )
        if resp.status_code != 200:
            raise ServiceUnavailable(f"login {resp.status_code}: {resp.text[:160]}")
        body = resp.json()
        token = body.get("access_token") or body.get("token")
        if not token:
            raise ServiceUnavailable(f"login 返回里没有 token：{list(body)}")
        _remember_token("ragent", str(token))
        return str(token)
    except ServiceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ServiceUnavailable(f"ragent 登录失败：{exc}") from exc


async def ragent_kb_ids(token: str) -> list[str]:
    """定位「三一重工年报」知识库（env 指定优先，否则按名字包含提示词筛）。"""
    if RAG_KB_IDS:
        return RAG_KB_IDS
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{RAG_BASE}/api/v1/kb", headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code != 200:
            raise ServiceUnavailable(f"kb list {resp.status_code}: {resp.text[:160]}")
        kbs = resp.json()
        matched = [k for k in kbs if RAG_KB_NAME_HINT in str(k.get("name", ""))]
        if not matched:
            raise ServiceUnavailable(
                f"没找到名字含「{RAG_KB_NAME_HINT}」的知识库（现有：{[k.get('name') for k in kbs][:5]}）"
            )
        return [str(matched[0]["id"])]
    except ServiceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ServiceUnavailable(f"ragent 知识库列表失败：{exc}") from exc


async def ragent_retrieve(query: str, *, top_k: int = 5, kb_ids: list[str] | None = None) -> CallResult:
    """向量检索（不生成）：返回 top-k chunk + 分数 + degraded 标记。

    `kb_ids` 显式给定时不再自动发现——场景测试要 pin 住临时知识库，或反过来验证隔离。
    """
    token = await ragent_login()
    kb_ids = kb_ids or await ragent_kb_ids(token)
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{RAG_BASE}/api/v1/retrieve",
                headers={"Authorization": f"Bearer {token}"},
                json={"query": query, "kb_ids": kb_ids, "top_k": top_k},
            )
        latency = (time.monotonic() - started) * 1000
        if resp.status_code != 200:
            raise ServiceUnavailable(f"retrieve {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        return CallResult(
            data={"items": body.get("items") or [], "degraded": bool(body.get("degraded")), "kb_ids": kb_ids},
            latency_ms=round(latency, 1),
        )
    except ServiceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ServiceUnavailable(f"ragent 检索失败：{exc}") from exc


# --- ragent：知识库 / 文档生命周期（增量更新类场景测试用） -------------------


async def _rag_client(token: str, timeout: float = HTTP_TIMEOUT) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout, headers={"Authorization": f"Bearer {token}"},
    )


async def ragent_create_kb(name: str, *, visibility: str = "public") -> dict[str, Any]:
    token = await ragent_login()
    try:
        async with await _rag_client(token, 20) as client:
            resp = await client.post(
                f"{RAG_BASE}/api/v1/kb", json={"name": name, "visibility": visibility},
            )
        if resp.status_code not in (200, 201):
            raise ServiceUnavailable(f"create_kb {resp.status_code}: {resp.text[:200]}")
        return resp.json()
    except ServiceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ServiceUnavailable(f"建知识库失败：{exc}") from exc


async def ragent_delete_kb(kb_id: str) -> bool:
    token = await ragent_login()
    try:
        async with await _rag_client(token, 20) as client:
            resp = await client.delete(f"{RAG_BASE}/api/v1/kb/{kb_id}")
        return resp.status_code in (200, 204)
    except Exception:  # noqa: BLE001 - 清理失败不影响结论
        return False


async def ragent_upload(
    kb_id: str, filename: str, content: bytes, *, document_id: str | None = None,
) -> dict[str, Any]:
    """上传一份文档（`document_id` 给了就是**覆盖/增量更新**语义）。"""
    token = await ragent_login()
    data = {"kb_id": kb_id}
    if document_id:
        data["document_id"] = document_id
    try:
        async with await _rag_client(token, 120) as client:
            resp = await client.post(
                f"{RAG_BASE}/api/v1/documents/upload",
                files={"file": (filename, content, "text/markdown")},
                data=data,
            )
        if resp.status_code != 200:
            raise ServiceUnavailable(f"upload {resp.status_code}: {resp.text[:200]}")
        return resp.json()
    except ServiceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ServiceUnavailable(f"上传文档失败：{exc}") from exc


async def ragent_docs(kb_id: str) -> list[dict[str, Any]]:
    """列**该知识库**的文档。

    ⚠️ `GET /api/v1/documents` **不支持 kb_id 过滤**（实测：签名只有 limit/offset，admin 会拿到全部文档）。
    2026-11-17 的真事故：清理逻辑按 kb 列文档 → 拿到全库 → 把 14 份文档 1391 个 chunk 全删了。
    所以这里必须**客户端过滤**，且删除动作只许作用于本库的 document_id。
    """
    token = await ragent_login()
    try:
        async with await _rag_client(token, 20) as client:
            resp = await client.get(f"{RAG_BASE}/api/v1/documents", params={"limit": 200})
        if resp.status_code != 200:
            raise ServiceUnavailable(f"list docs {resp.status_code}: {resp.text[:200]}")
        items = resp.json()
        return [d for d in items if str(d.get("kb_id")) == str(kb_id)]
    except ServiceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ServiceUnavailable(f"列文档失败：{exc}") from exc


async def ragent_delete_doc(document_id: str) -> bool:
    token = await ragent_login()
    try:
        async with await _rag_client(token, 20) as client:
            resp = await client.delete(f"{RAG_BASE}/api/v1/documents/{document_id}")
        return resp.status_code in (200, 204)
    except Exception:  # noqa: BLE001
        return False
