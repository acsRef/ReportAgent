from __future__ import annotations

import hashlib
import os

from app.infra.db.postgres import get_pool
from app.infra.auth.startup_guard import DEV_PASSWORD_LITERAL, dev_escape_allowed

DEFAULT_USERNAME = os.getenv("DEFAULT_USERNAME", "admin")
DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "admin123")


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


async def ensure_default_user():
    pool = get_pool()
    weak_hash = _hash_password(DEV_PASSWORD_LITERAL)
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, password_hash FROM app.users WHERE username = $1",
            DEFAULT_USERNAME,
        )
        if existing:
            # 关闭「开发期建的 admin123 账户被直接升上生产」的后门：非开发环境下，
            # 已存在的默认用户若仍持弱密码哈希，拒绝启动（而不是静默放行）。
            if not dev_escape_allowed() and existing["password_hash"] == weak_hash:
                raise RuntimeError(
                    "Default user '%s' already exists with the insecure default "
                    "password. Rotate its password before running APP_ENV != "
                    "development." % DEFAULT_USERNAME
                )
            return
        pw_hash = _hash_password(DEFAULT_PASSWORD)
        await conn.execute(
            "INSERT INTO app.users (username, password_hash) VALUES ($1, $2)",
            DEFAULT_USERNAME, pw_hash,
        )


async def verify_user(username: str, password: str) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, password_hash FROM app.users WHERE username = $1",
            username,
        )
        if not row:
            return None
        if row["password_hash"] != _hash_password(password):
            return None
        return {"id": row["id"], "username": row["username"]}


async def get_user_by_id(user_id: int) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username FROM app.users WHERE id = $1", user_id,
        )
        if row:
            return {"id": row["id"], "username": row["username"]}
        return None
