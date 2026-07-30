from __future__ import annotations

import os

# 开发期默认字面值。这些值在仓库中公开已知（.env.example / jwt.py / repository.py），
# 任何把它们带进非开发环境的部署都等于远程认证绕过。启动闸的唯一职责就是让这些值
# 「只能」出现在显式声明的开发环境里。
DEV_SECRET_LITERAL = "reportagent-dev-secret-key-change-in-production"
DEV_PASSWORD_LITERAL = "admin123"

# 非开发环境强制的 JWT_SECRET 最小长度（HS256 推荐 ≥32 字节熵）。
MIN_SECRET_CHARS = 32

_TRUTHY = {"1", "true", "yes", "on"}


def app_env() -> str:
    """运行环境标记。fail-closed：未设置时按 ``production`` 处理。

    这是整个安全闸的基石——「忘记配置 APP_ENV」被视为最危险的「忘记配置」，
    因此默认走最严格的 production 规则，而不是宽松放行。
    """
    return (os.getenv("APP_ENV") or "production").strip().lower()


def is_development() -> bool:
    return app_env() == "development"


def allow_insecure_default() -> bool:
    """开发逃生门开关。仅在 ``APP_ENV=development`` 下有意义。"""
    return (os.getenv("ALLOW_INSECURE_DEFAULT_AUTH") or "").strip().lower() in _TRUTHY


def dev_escape_allowed() -> bool:
    """是否允许使用不安全默认值。

    必须「开发环境」且「显式允许」两个条件同时满足。在非开发环境下，
    ``ALLOW_INSECURE_DEFAULT_AUTH`` 被完全忽略——生产无法用逃生门绕过。
    """
    return is_development() and allow_insecure_default()


def validate_auth_security_config() -> None:
    """启动期 auth 安全校验。不通过直接 ``raise RuntimeError``。

    规则（fail-closed）：
      * JWT_SECRET 未设置 → 拒绝（除非开发逃生门）。
      * 非开发环境：JWT_SECRET 不能等于开发默认字面值，且长度 ≥ ``MIN_SECRET_CHARS``。
      * 非开发环境：DEFAULT_PASSWORD 不能等于开发默认字面值 ``admin123``。

    本函数只读环境变量、不触碰数据库，因此必须在 lifespan 最早期、
    初始化 PG pool 与创建默认用户之前调用。
    """
    if dev_escape_allowed():
        return

    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "JWT_SECRET is not configured. Set a long random secret "
            "(APP_ENV=production requires >= %d chars). Local development may set "
            "APP_ENV=development and ALLOW_INSECURE_DEFAULT_AUTH=1 to bypass."
            % MIN_SECRET_CHARS
        )
    if secret == DEV_SECRET_LITERAL:
        raise RuntimeError(
            "JWT_SECRET equals the public development default and cannot be used "
            "outside development. Generate a long random secret for APP_ENV=%s."
            % app_env()
        )
    if len(secret) < MIN_SECRET_CHARS:
        raise RuntimeError(
            "JWT_SECRET is too weak for APP_ENV=%s: got %d chars, need >= %d."
            % (app_env(), len(secret), MIN_SECRET_CHARS)
        )

    password = os.getenv("DEFAULT_PASSWORD") or DEV_PASSWORD_LITERAL
    if password == DEV_PASSWORD_LITERAL:
        raise RuntimeError(
            "DEFAULT_PASSWORD is the insecure default 'admin123' and cannot be used "
            "outside development (APP_ENV=%s). Set a strong DEFAULT_PASSWORD."
            % app_env()
        )
