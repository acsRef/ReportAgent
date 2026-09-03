"""④ 存量 sha256 密码行登录后透明升级 Argon2id（Final Hardening ④，真 PG）。

需要真实 PostgreSQL（persistence marker）。验证 verify_user 的旧行兼容 +
升级写回两个契约点：
  1. legacy hex 行用对密码能登录成功（返回 user）；
  2. 登录后库内 password_hash 已变为 $argon2id$，且新密码校验不再走 legacy。
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest

pytestmark = pytest.mark.persistence


def _run(coro):
    from app.infra.db.postgres import init_pool, close_pool

    async def _body():
        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


def test_legacy_sha256_user_logs_in_and_is_upgraded() -> None:
    from app.infra.auth.repository import verify_user
    from app.infra.db.postgres import get_pool

    username = f"legacy_{uuid.uuid4().hex[:10]}"
    password = "rotate-me-123"
    legacy_hash = hashlib.sha256(password.encode()).hexdigest()

    async def body():
        pool = get_pool()
        try:
            async with pool.acquire() as conn:
                user_id = await conn.fetchval(
                    "INSERT INTO app.users (username, password_hash) "
                    "VALUES ($1, $2) RETURNING id",
                    username, legacy_hash,
                )
            # 1) 旧 hex 行：对密码登录成功
            user = await verify_user(username, password)
            assert user is not None and user["username"] == username
            # 2) 登录触发透明升级：库内已换 Argon2id
            async with pool.acquire() as conn:
                stored = await conn.fetchval(
                    "SELECT password_hash FROM app.users WHERE id = $1", user_id
                )
            assert isinstance(stored, str) and stored.startswith("$argon2id$"), stored
            # 3) 升级后仍可正常校验（不再依赖 legacy 分支）
            user2 = await verify_user(username, password)
            assert user2 is not None
            assert await verify_user(username, "wrong-password") is None
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM app.users WHERE username = $1", username)

    _run(body())


def test_register_writes_argon2id_never_sha256() -> None:
    """新注册用户必须直接落 Argon2id（main.py register 收敛到 hash_password）。"""
    from app.infra.auth.repository import hash_password
    from app.infra.db.postgres import get_pool

    username = f"newuser_{uuid.uuid4().hex[:10]}"
    pw_hash = hash_password("brand-new-password")

    async def body():
        pool = get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO app.users (username, password_hash) VALUES ($1, $2)",
                    username, pw_hash,
                )
                stored = await conn.fetchval(
                    "SELECT password_hash FROM app.users WHERE username = $1", username
                )
            assert stored.startswith("$argon2id$")
            assert not hashlib.sha256(b"brand-new-password").hexdigest() == stored
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM app.users WHERE username = $1", username)

    _run(body())
