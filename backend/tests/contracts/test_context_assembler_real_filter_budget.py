"""P4c Task 4: assembler 真实装 Filter (dedup + §七 序) + Token Budget 截断.

参考 P4a/p3 当前 assembler (assembler.py:39-65): 仅 drop empty + 简单拼接, 无 dedup,
无 sort, 无 Budget.  本 plan 加 3 个 pipeline 步骤:
1) dedup by (source, ref_id) 保留 score 最高
2) §七 排序: query > semantic > preference
3) Token Budget 截断 (P4C_ASSEMBLER_TOKEN_BUDGET env, default 4000 tokens)

不动 ContextBundle 公共签名; 既有 contract test 必须不破.
"""
from __future__ import annotations

import pytest

from app.context.assembler import ContextAssembler, RecallItem
from app.context.policy import AgentContextPolicy


def _item(source, kind, ref_id, text="raw", score=0.5):
    return RecallItem(raw_text=text, source=source, kind=kind, score=score, ref_id=ref_id)


class TestFilterEmpty:
    """Filter: drop empty raw_text (P4a 已落; 钉现有行为不破)."""

    def test_drops_empty_raw_text(self):
        asm = ContextAssembler()
        items = [
            _item("memory_query", "query", 1, ""),
            _item("memory_semantic", "semantic", 2, "kept"),
        ]
        bundle = asm.assemble(
            conversation_context="conv",
            recall_items=items,
            agent_policy=AgentContextPolicy.REQUIREMENT,
        )
        raw_texts = [it["raw_text"] for it in bundle["recall_items"]]
        assert "" not in raw_texts
        assert "kept" in raw_texts


class TestDedupBySourceRefId:
    """Filter: dedup by (source, ref_id) 保留 score 最高."""

    def test_dedup_keeps_highest_score(self):
        asm = ContextAssembler()
        items = [
            _item("memory_query", "query", 1, "A_low", 0.3),
            _item("memory_query", "query", 1, "A_high", 0.9),  # dup, ref_id 相同
            _item("memory_query", "query", 2, "B", 0.7),
        ]
        bundle = asm.assemble(
            conversation_context="",
            recall_items=items,
            agent_policy=AgentContextPolicy.EXECUTION,
        )
        raw_texts = [it["raw_text"] for it in bundle["recall_items"]]
        assert "A_low" not in raw_texts, f"dedup 未保留高分: {raw_texts!r}"
        assert raw_texts.count("A_high") == 1
        assert raw_texts.count("B") == 1
        assert len(bundle["recall_items"]) == 2


class TestKindSortQuerySemanticPreference:
    """§七 Conflict Resolution: query > semantic > preference 排序."""

    def test_kind_order_query_semantic_preference(self):
        asm = ContextAssembler()
        items = [
            _item("memory_semantic", "preference", 1, "PREF"),
            _item("memory_query", "query", 2, "QRY"),
            _item("memory_semantic", "semantic", 3, "SEM"),
        ]
        bundle = asm.assemble(
            conversation_context="",
            recall_items=items,
            agent_policy=AgentContextPolicy.EXECUTION,
        )
        assert [it["kind"] for it in bundle["recall_items"]] == ["query", "semantic", "preference"], (
            f"sort order 不符 §七: {bundle['recall_items']!r}"
        )


class TestTokenBudget:
    """Budget: P4C_ASSEMBLER_TOKEN_BUDGET env 控制; char count 估算 (1 token ≈ 3 chars)."""

    def test_token_budget_truncates_recall_block(self, monkeypatch):
        monkeypatch.setenv("P4C_ASSEMBLER_TOKEN_BUDGET", "100")
        asm = ContextAssembler()
        items = [_item("memory_query", "query", i, "x" * 100, 0.5) for i in range(20)]
        bundle = asm.assemble(
            conversation_context="conv",
            recall_items=items,
            agent_policy=AgentContextPolicy.EXECUTION,
        )
        # budget=100 tokens ≈ 300 chars; 实际给点 padding 容差
        assert len(bundle["assembled_context"]) <= 600, (
            f"Budget 未截断: len={len(bundle['assembled_context'])}"
        )

    def test_token_budget_keeps_short(self, monkeypatch):
        """Budget 大于实际 → 不截断."""
        monkeypatch.setenv("P4C_ASSEMBLER_TOKEN_BUDGET", "100000")
        asm = ContextAssembler()
        items = [_item("memory_query", "query", 1, "short", 0.5)]
        bundle = asm.assemble(
            conversation_context="conv",
            recall_items=items,
            agent_policy=AgentContextPolicy.EXECUTION,
        )
        assert "short" in bundle["assembled_context"]
        assert "conv" in bundle["assembled_context"]

    def test_no_budget_env_uses_default(self, monkeypatch):
        """未设 P4C_ASSEMBLER_TOKEN_BUDGET 时, 用默认值 (P3 不裁剪计划被此选项覆盖)."""
        monkeypatch.delenv("P4C_ASSEMBLER_TOKEN_BUDGET", raising=False)
        asm = ContextAssembler()
        items = [_item("memory_query", "query", 1, "x" * 100, 0.5)]
        bundle = asm.assemble(
            conversation_context="conv",
            recall_items=items,
            agent_policy=AgentContextPolicy.EXECUTION,
        )
        # default 4000 tokens × 3 chars = 12000 chars 上限; items + conv 远低于此
        # → 不截断
        assert "x" * 100 in bundle["assembled_context"]
        assert "conv" in bundle["assembled_context"]
