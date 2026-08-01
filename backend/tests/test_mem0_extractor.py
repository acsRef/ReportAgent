"""第 2 轮·mem0 L3 事实抽取引擎测试。

mem0 客户端用 fake 隔离（不依赖真实 chroma/LLM）。验证：未启用降级、
只采纳 ADD/UPDATE 事实、异常 graceful 降级。
"""
from __future__ import annotations

import pytest

from app.infra.memory import mem0_extractor

pytestmark = pytest.mark.smoke


async def test_disabled_returns_empty_without_client(monkeypatch):
    monkeypatch.setattr(mem0_extractor, "mem0_enabled", lambda: False)

    def _boom():
        raise AssertionError("未启用时不应创建 mem0 客户端")

    monkeypatch.setattr(mem0_extractor, "_get_client", _boom)
    assert await mem0_extractor.extract_facts("任意对话", user_id="1") == []


async def test_empty_text_returns_empty(monkeypatch):
    monkeypatch.setattr(mem0_extractor, "mem0_enabled", lambda: True)
    assert await mem0_extractor.extract_facts("   ", user_id="1") == []


async def test_enabled_extracts_add_update_only(monkeypatch):
    class FakeMem0:
        def add(self, text, user_id=None):
            return {"results": [
                {"memory": "用户偏好柱状图", "event": "ADD"},
                {"memory": "销售额映射 total_amount", "event": "UPDATE"},
                {"memory": "过时事实", "event": "DELETE"},
                {"memory": "无事件字段的事实"},  # event 缺省 → 采纳
            ]}

    monkeypatch.setattr(mem0_extractor, "mem0_enabled", lambda: True)
    monkeypatch.setattr(mem0_extractor, "_get_client", lambda: FakeMem0())
    facts = await mem0_extractor.extract_facts("一段对话", user_id=1)
    assert facts == ["用户偏好柱状图", "销售额映射 total_amount", "无事件字段的事实"]


async def test_client_failure_degrades_gracefully(monkeypatch):
    class BoomMem0:
        def add(self, text, user_id=None):
            raise RuntimeError("mem0 down")

    monkeypatch.setattr(mem0_extractor, "mem0_enabled", lambda: True)
    monkeypatch.setattr(mem0_extractor, "_get_client", lambda: BoomMem0())
    assert await mem0_extractor.extract_facts("一段对话", user_id="1") == []
