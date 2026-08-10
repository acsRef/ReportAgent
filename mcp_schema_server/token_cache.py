"""跨进程共享的 ragent-py 登录 token 缓存（文件持久化）。

与 ragent-py/mcp_server/token_cache.py、backend/app/tools/ragent_token_cache.py
同格式，共享同一缓存文件——根治登录 429（每 IP 5 分钟 10 次限流）。

文件格式：{ "<RAGENT_URL>": { "token": "...", "expires_at": <unix> } }
"""
from __future__ import annotations

import json
import os
import threading
import time

_PATH = os.getenv("RAGENT_TOKEN_CACHE", os.path.join(os.path.expanduser("~"), ".ragent_token_cache.json"))
_TTL = float(os.getenv("RAGENT_TOKEN_TTL", str(20 * 3600)))

_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def get_token(base_url: str) -> str | None:
    with _lock:
        entry = _load().get(base_url)
        if entry and entry.get("token") and entry.get("expires_at", 0) > time.time():
            return entry["token"]
        return None


def set_token(base_url: str, token: str) -> None:
    with _lock:
        data = _load()
        data[base_url] = {"token": token, "expires_at": time.time() + _TTL}
        _save(data)


def invalidate(base_url: str) -> None:
    with _lock:
        data = _load()
        if base_url in data:
            data.pop(base_url, None)
            _save(data)