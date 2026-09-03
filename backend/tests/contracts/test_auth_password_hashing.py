"""④ Argon2id 密码哈希契约（Final Hardening ④，纯函数离线层）。

背景：2026-09-03 前密码用裸 SHA-256（fast hash + 无盐）；现统一 Argon2id
（argon2-cffi：memory-hard + 盐 + verify 常量时间）。存量 sha256 hex 行
仍可校验、登录成功后透明升级（见 persistence/test_auth_password_upgrade.py）。
"""
from __future__ import annotations

import hashlib
import re

import pytest

from app.infra.auth.repository import (
    hash_password,
    password_matches,
)

pytestmark = pytest.mark.contracts


def test_hash_is_argon2id_with_salt():
    h = hash_password("secret123")
    assert h.startswith("$argon2id$"), h
    # 同一密码两次哈希必须不同（随机盐）——防彩虹表/相等性推断
    assert h != hash_password("secret123")


def test_password_matches_roundtrip():
    h = hash_password("correct horse battery staple")
    assert password_matches(h, "correct horse battery staple") is True
    assert password_matches(h, "wrong") is False


def test_legacy_sha256_hex_still_verifiable():
    """旧存量行（64 位 sha256 hex）在校验期仍然有效，不等同于新哈希安全级别。"""
    legacy = hashlib.sha256(b"admin123").hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", legacy)
    assert password_matches(legacy, "admin123") is True
    assert password_matches(legacy, "nope") is False


def test_garbage_stored_hash_never_matches():
    assert password_matches("", "admin123") is False
    assert password_matches("not-a-hash", "admin123") is False
    assert password_matches("x" * 64, "admin123") is False  # 非 hex 的 64 位
    assert password_matches("$argon2id$truncated", "admin123") is False


def test_default_password_literal_detectable_under_both_schemes():
    """非开发环境启动闸要认出「默认口令」，不管以哪种算法存储。"""
    legacy = hashlib.sha256(b"admin123").hexdigest()
    argon = hash_password("admin123")
    assert password_matches(legacy, "admin123") is True
    assert password_matches(argon, "admin123") is True
