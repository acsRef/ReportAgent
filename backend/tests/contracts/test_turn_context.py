"""Turn Understanding 必须拿到「总的上下文」（2026-11-08 用户拍板 + 真机事故）。

事故链：
1. `_turn_understand` 里写死 `history=None` → 模型只看到上一轮归并后的几个字段，
   「然后标注一下…」里的「然后」「标注」全靠它猜；
2. 接上 Context Runtime 后仍然拿不到——因为 `SelectiveRecallPolicy` 用 `_HISTORY_REF`
   词表判断「这一轮是否延续历史」，而「然后」不在词表里；
3. 修法：对 `turn_understanding` **强制**取对话上下文（它的职责就是判断轮次关系，
   不该由词表启发式决定），且走 Context Runtime 而不是自己拼字符串（宪法 §2）。
"""
from __future__ import annotations

import pytest

from app.agent.prompts.turn_plan_prompts import build_turn_plan_prompt
from app.agent.requirement_analysis_graph import _turn_context_block

pytestmark = pytest.mark.contracts


class TestPromptCarriesContext:
    def test_上下文块进_prompt(self) -> None:
        prompt = build_turn_plan_prompt(
            "然后标注一下 总的销售额是多少",
            context_block="user: 分析 2024 年华东销售趋势和异常",
        )

        assert "【对话上下文（用户原话）】" in prompt
        assert "分析 2024 年华东销售趋势和异常" in prompt

    def test_没有块时回落_history(self) -> None:
        prompt = build_turn_plan_prompt("那看华南的呢", history=["用户：看华东", "助手：好"])

        assert "用户：看华东" in prompt

    def test_两者都没有时写明无(self) -> None:
        prompt = build_turn_plan_prompt("随便问一句")

        assert "（无）" in prompt


class TestContextBlock:
    async def test_强制取对话上下文(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`force_conversation=True`：不靠 `_HISTORY_REF` 词表判「是否延续历史」。"""
        captured: dict = {}

        async def fake_build(**kwargs):
            captured.update(kwargs)
            return {"conversation_context": "user: 上一轮原话"}

        from app.context.runtime import context_runtime

        monkeypatch.setattr(context_runtime, "build", fake_build)
        block = await _turn_context_block(
            {"session_id": "s-1", "user_id": 1, "user_query": "然后标注一下"}, has_prior=True,
        )

        assert block == "user: 上一轮原话"
        assert captured["force_conversation"] is True
        assert captured["agent"] == "turn_understanding"   # resolver → REQUIREMENT 档
        assert captured["session_id"] == "s-1"

    @pytest.mark.parametrize("state", [
        {"session_id": "", "user_id": 1},
        {"session_id": "s", "user_id": None},
        {"session_id": "s"},
    ])
    async def test_缺少会话或用户时直接空(self, state: dict) -> None:
        assert await _turn_context_block(state, has_prior=False) == ""

    async def test_取上下文失败不影响这一轮(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def boom(**kwargs):
            raise RuntimeError("db down")

        from app.context.runtime import context_runtime

        monkeypatch.setattr(context_runtime, "build", boom)
        block = await _turn_context_block(
            {"session_id": "s-1", "user_id": 1, "user_query": "q"}, has_prior=False,
        )

        assert block == ""
