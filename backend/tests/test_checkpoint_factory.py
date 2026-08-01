"""checkpointer 工厂单测：环境路由 + 兜底。

非开发环境的 AsyncPostgresSaver 路径由 persistence/test_postgres_checkpoint.py
直接构造覆盖；这里只测不需要 PG 的开发路由与兜底逻辑。
"""
from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.infra.checkpoint import factory

pytestmark = pytest.mark.smoke


def test_get_checkpointer_fallback_is_memorysaver():
    """未走 lifespan（单例为 None）时兜底 MemorySaver，保证图随时可编译。"""
    saved = factory._checkpointer
    try:
        factory._checkpointer = None
        assert isinstance(factory.get_checkpointer(), MemorySaver)
    finally:
        factory._checkpointer = saved


async def test_init_checkpointer_development_uses_memorysaver(monkeypatch):
    """APP_ENV=development → MemorySaver（便于本地/notebook 单步）。"""
    monkeypatch.setattr(factory, "app_env", lambda: "development")
    saved = factory._checkpointer
    try:
        await factory.init_checkpointer()
        assert isinstance(factory.get_checkpointer(), MemorySaver)
    finally:
        factory._checkpointer = saved
