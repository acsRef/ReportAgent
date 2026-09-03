from __future__ import annotations

import hashlib
import hmac
import os
import re

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from app.infra.db.postgres import get_pool
from app.infra.auth.startup_guard import DEV_PASSWORD_LITERAL, dev_escape_allowed

DEFAULT_USERNAME = os.getenv("DEFAULT_USERNAME", "admin")
DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "admin123")

# Argon2id 参数走 argon2-cffi 默认（time=3 / memory=64MiB / parallelism=4），
# 面试/文档口径：memory-hard + salt + 慢哈希，取代裸 SHA-256（fast hash 可被
# 大规模离线爆破，且无盐无法抗彩虹表）。
_hasher = PasswordHasher()

# 旧版裸 SHA-256 hex（2026-09-03 前产物）识别：64 位 hex。登录成功后透明升级
# 为 Argon2id（见 verify_user），存量行无需停机迁移。
_LEGACY_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def hash_password(password: str) -> str:
    """Argon2id 哈希（带随机盐）。"""
    return _hasher.hash(password)


def _legacy_sha256_hex(password: str) -> str:
    """旧版 sha256 摘要——仅用于存量行校验与透明升级，新写入一律走 Argon2id。"""
    return hashlib.sha256(password.encode()).hexdigest()


def password_matches(stored_hash: str, password: str) -> bool:
    """校验密码与存储哈希是否匹配（Argon2id 优先，兼容旧 sha256 hex）。

    常量时间比较：Argon2 verify 内部自带；legacy 分支用 hmac.compare_digest。
    """
    if stored_hash.startswith("$argon2"):
        try:
            return _hasher.verify(stored_hash, password)
        except VerificationError:
            return False
    if _LEGACY_SHA256_HEX.fullmatch(stored_hash or ""):
        return hmac.compare_digest(stored_hash, _legacy_sha256_hex(password))
    return False


def _is_legacy_sha256(stored_hash: str) -> bool:
    return bool(_LEGACY_SHA256_HEX.fullmatch(stored_hash or ""))


async def ensure_default_user():
    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, password_hash FROM app.users WHERE username = $1",
            DEFAULT_USERNAME,
        )
        if existing:
            # 关闭「开发期建的 admin123 账户被直接升上生产」的后门：非开发环境下，
            # 已存在的默认用户若仍持默认口令（无论 Argon2id 还是旧 sha256 存法），
            # 拒绝启动（而不是静默放行）。
            if not dev_escape_allowed() and password_matches(
                existing["password_hash"], DEV_PASSWORD_LITERAL
            ):
                raise RuntimeError(
                    "Default user '%s' already exists with the insecure default "
                    "password. Rotate its password before running APP_ENV != "
                    "development." % DEFAULT_USERNAME
                )
            return
        pw_hash = hash_password(DEFAULT_PASSWORD)
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
        if not password_matches(row["password_hash"], password):
            return None
        # 透明升级：旧 sha256 行登录成功即重写为 Argon2id（幂等，之后不再走 legacy）。
        if _is_legacy_sha256(row["password_hash"]):
            new_hash = hash_password(password)
            await conn.execute(
                "UPDATE app.users SET password_hash = $1 WHERE id = $2",
                new_hash, row["id"],
            )
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
