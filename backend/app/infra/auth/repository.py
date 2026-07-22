from __future__ import annotations

import hashlib
import os

from app.infra.db.postgres import get_pool

DEFAULT_USERNAME = os.getenv("DEFAULT_USERNAME", "admin")
DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "admin123")


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


async def ensure_default_user():
    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM app.users WHERE username = $1", DEFAULT_USERNAME
        )
        if existing:
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
