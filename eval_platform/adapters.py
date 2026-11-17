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
RA_USERNAME = os.getenv("RA_USERNAME", "admin")
RA_PASSWORD = os.getenv("RA_PASSWORD", "admin123")
RAG_USERNAME = os.getenv("RAG_USERNAME", "admin")
RAG_PASSWORD = os.getenv("RAG_PASSWORD", "admin123")
RAG_KB_IDS = [x.strip() for x in os.getenv("RAG_KB_IDS", "").split(",") if x.strip()]
RAG_KB_NAME_HINT = os.getenv("RAG_KB_NAME_HINT", "三一")
HTTP_TIMEOUT = float(os.getenv("EVAL_HTTP_TIMEOUT", "90"))


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
    """ReportAgent 的 /chat 也要 JWT（默认 admin/admin123）。"""
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


async def ragent_login() -> str:
    """登录拿 JWT（默认 admin/admin123，可用 env 覆盖）。"""
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


async def ragent_retrieve(query: str, *, top_k: int = 5) -> CallResult:
    """向量检索（不生成）：返回 top-k chunk + 分数 + degraded 标记。"""
    token = await ragent_login()
    kb_ids = await ragent_kb_ids(token)
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
