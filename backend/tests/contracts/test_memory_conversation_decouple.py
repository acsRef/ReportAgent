"""P4a Conversation Memory 解耦 + L3 write seam 钉子。

P4a plan §Verification 钉住：
1. app.memory.conversation 暴露 build_context / prepare_conversation_context（domain 层存在）
2. app.memory.manager.remember_conversation_facts 委托 MemoryManager.remember_preference
3. review #9 核心：AST 扫 app/context/**/*.py 无 from/import app.infra.memory
4. 反向依赖：AST 扫 app/infra/memory/**/*.py 无 import app.memory / app.context
5. recall_structured 不存在（P4a NOT doing）
6. MemoryManager.recall 仍 -> str（签名不变）
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.contracts

_APP = Path(inspect.getfile(__import__("app"))).parent  # backend/app/


def _module_srcs(pkg_dir: Path) -> list[tuple[str, str]]:
    return [(p.stem, p.read_text(encoding="utf-8")) for p in pkg_dir.rglob("*.py")]


# --- 1. domain 层存在性 -----------------------------------------------------


def test_memory_conversation_exposes_engine_symbols():
    from app.memory import conversation
    for sym in ("build_context", "prepare_conversation_context",
                "compress_and_extract", "format_context_block"):
        assert hasattr(conversation, sym), f"app.memory.conversation 缺 {sym}"


def test_memory_conversation_does_not_import_infra_user_memory_directly():
    # conversation 写 L3 必须经 manager，不直连 UserMemory/mem0。
    # 用 AST 查真实 import（不用 substring，避免命中 docstring 里的说明性文字）。
    src = Path(inspect.getfile(
        __import__("app.memory.conversation", fromlist=["conversation"])
    )).read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for m in mods:
            if m in _RAW_MEMORY_PRIMITIVES or m == "app.infra.memory":
                offenders.append(m)
    assert not offenders, (
        f"conversation.py 不应直连 infra raw 原语（应经 manager）：{offenders}"
    )


# --- 2. write seam 委托 MemoryManager --------------------------------------


@pytest.mark.asyncio
async def test_remember_conversation_facts_delegates_to_manager(monkeypatch):
    from app.memory import manager
    saved: list[dict] = []

    async def fake_remember(self, user_id, content, memory_type="insight",
                            importance=0.3, source=""):
        saved.append({"content": content, "importance": importance,
                      "memory_type": memory_type, "source": source})
        return 1

    monkeypatch.setattr(
        "app.infra.memory.memory_manager.MemoryManager.remember_preference",
        fake_remember,
    )
    updates = {
        "extracted_schemas": [{"db_field": "total_amount"}],
        "extracted_preferences": ["用户偏好柱状图"],
    }
    await manager.remember_conversation_facts(1, updates, compressed_batch=[])

    contents = {s["content"] for s in saved}
    assert any("total_amount" in c for c in contents)
    assert "用户偏好柱状图" in contents
    # importance=0.5 与旧 _save_l3_facts 对齐（P3 test 断言不破）
    assert all(s["importance"] == 0.5 for s in saved)
    assert all(s["source"] == "context_compress" for s in saved)


# --- 3. context 包与 infra.memory raw 原语解耦（review #9 核心） -------------
#
# 边界精确化：宪法 §6「读写一律经 Memory Manager」。context 经
# MemoryManager（网关）合法；被禁止的是 context 绕过网关直连 raw persistence
# 原语（UserMemory / QueryMemory / mem0_extractor）——那才是 review #9 的
# Legacy Glue。故钉子只禁 raw 原语，放行 memory_manager。

_RAW_MEMORY_PRIMITIVES = (
    "app.infra.memory.user_memory",
    "app.infra.memory.query_memory",
    "app.infra.memory.mem0_extractor",
    "app.infra.memory.policy",
)


def test_context_package_has_no_raw_infra_memory_import():
    offenders = []
    for name, src in _module_srcs(_APP / "context"):
        tree = ast.parse(src)
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                if m in _RAW_MEMORY_PRIMITIVES or m == "app.infra.memory":
                    offenders.append(f"app/context/{name}.py → {m}")
    assert not offenders, (
        f"context 包不得绕过 MemoryManager 直连 raw persistence 原语（review #9）：{offenders}"
    )


# --- 4. persistence 不反向依赖 domain --------------------------------------


def test_infra_memory_does_not_import_app_memory_or_context():
    offenders = []
    for name, src in _module_srcs(_APP / "infra" / "memory"):
        tree = ast.parse(src)
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                if m.startswith("app.memory") or m.startswith("app.context"):
                    offenders.append(f"infra/memory/{name}.py → {m}")
    assert not offenders, (
        f"infra.memory（persistence）不得反向 import domain/context：{offenders}"
    )


# --- 6. recall API 边界（P4a 保持 string API） -------------------------------
#
# P4a 的 `test_recall_structured_not_introduced_in_p4a` 钉子已于 P4b T4 移除：
# P4b 正是 recall_structured 的落地处（见 test_structured_recall_contract.py）。
# recall() -> str 兼容面由下方 + test_structured_recall_contract 双重保证。


def test_recall_still_returns_str():
    import asyncio
    from unittest.mock import AsyncMock, patch
    from app.infra.memory import MemoryManager

    async def _go():
        with patch("app.infra.memory.memory_manager.QueryMemory.search_similar",
                   new=AsyncMock(return_value=[])), \
             patch("app.infra.memory.memory_manager.UserMemory.search",
                   new=AsyncMock(return_value=[])):
            return await MemoryManager().recall("q", "1")

    result = asyncio.run(_go())
    assert isinstance(result, str)
