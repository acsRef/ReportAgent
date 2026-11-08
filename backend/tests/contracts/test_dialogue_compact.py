"""对话上下文的 compact（2026-11-08 用户拍板）。

两条硬要求：
1. **历史始终给**，不再用关键词命中决定给不给（「然后」不在词表里 → 模型只能瞎猜）；
2. **数字不能压缩**——日期 / 金额 / 百分比 / 编号必须原样出现，超过预算也宁可超。
"""
from __future__ import annotations

import pytest

from app.memory.conversation import (
    _compact_utterance,
    _protected_fragments,
    prepare_user_utterances_context,
)

pytestmark = pytest.mark.contracts


class TestProtectedFragments:
    @pytest.mark.parametrize(("text", "must_keep"), [
        ("2024年11月华东销售额是 2630 万元", ["2024年11月", "2630 万元"]),
        ("比对 2023年11月 和 2024年11月", ["2023年11月", "2024年11月"]),
        ("订单号 39454 金额 70.06 元", ["39454", "70.06 元"]),
        ("同比增长 12.5%", ["12.5%"]),
        ("那七月份呢", []),
    ])
    def test_抽出数字类片段(self, text: str, must_keep: list[str]) -> None:
        fragments = _protected_fragments(text)
        for piece in must_keep:
            assert piece in fragments, f"{piece!r} 没被保护，片段={fragments}"

    def test_去重保序(self) -> None:
        assert _protected_fragments("2024年11月 和 2024年11月") == ["2024年11月"]

    def test_只有年份不算完整日期(self) -> None:
        # 「2024年」这种裸年份由结构化字段（time_range）承载，不进文本保护片段
        assert _protected_fragments("2024年") == []


class TestCompactUtterance:
    def test_骨架截断但数据全保留(self) -> None:
        text = "帮我分析一下 2024年11月 华东区域 净销售额 2630 万元 的情况，以及同比增长 12.5% 的原因"
        compacted = _compact_utterance(text, keep_head=10)

        assert len(compacted) < len(text)
        assert "2024年11月" in compacted
        assert "2630 万元" in compacted
        assert "12.5%" in compacted

    def test_没有数字时只留骨架(self) -> None:
        compacted = _compact_utterance("那七月份呢，帮我再看看", keep_head=6)

        assert compacted.startswith("那七月份呢")
        assert "数据" not in compacted

    def test_空内容返回空(self) -> None:
        assert _compact_utterance("   ", keep_head=10) == ""


def _msgs(*items: tuple[str, str]) -> list[dict]:
    return [
        {"role": "user" if mtype == "text" else "assistant",
         "content": content, "message_type": mtype}
        for mtype, content in items
    ]


class TestPrepareContext:
    async def test_短会话原样返回(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = _msgs(("text", "分析 2024 年华东销售趋势"), ("assistant_text", "好，2024华东的销售额，按周"))

        async def fake_get_messages(session_id, user_id):
            return rows

        monkeypatch.setattr(
            "app.infra.conversation.repository.get_messages", fake_get_messages,
        )
        text = await prepare_user_utterances_context("s", 1)

        assert "user: 分析 2024 年华东销售趋势" in text
        assert "assistant: 好，2024华东的销售额，按周" in text

    async def test_长会话压缩但数字一条不丢(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows: list[dict] = []
        for month in range(1, 9):
            rows.append({"role": "user", "message_type": "text",
                         "content": f"看看 2024年{month}月 华东的净销售额是多少，大概 {month}000 万元"})
            rows.append({"role": "assistant", "message_type": "assistant_text",
                         "content": f"好，2024年{month}月华东净销售额。"})

        async def fake_get_messages(session_id, user_id):
            return rows

        monkeypatch.setattr(
            "app.infra.conversation.repository.get_messages", fake_get_messages,
        )
        text = await prepare_user_utterances_context("s", 1, max_chars=600, keep_recent=2)

        # 每个月份都必须还在（数字不能压缩）
        for month in range(1, 9):
            assert f"2024年{month}月" in text, f"{month} 月的日期丢了：{text}"
            assert f"{month}000 万元" in text, f"{month}000 万元 丢了：{text}"

    async def test_最近几条永远原样(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = _msgs(
            ("text", "第一轮"), ("text", "第二轮"), ("text", "第三轮"),
            ("text", "第四轮：然后标注一下 总的销售额是多少"), ("text", "第五轮：以及增长率"),
        )

        async def fake_get_messages(session_id, user_id):
            return rows

        monkeypatch.setattr(
            "app.infra.conversation.repository.get_messages", fake_get_messages,
        )
        text = await prepare_user_utterances_context("s", 1, keep_recent=2, max_chars=80)

        assert "user: 第五轮：以及增长率" in text
        assert "user: 第四轮：然后标注一下 总的销售额是多少" in text

    async def test_跳过过程记录行(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = _msgs(("text", "问题"), ("turn_trace", ""), ("assistant_text", "答复"))

        async def fake_get_messages(session_id, user_id):
            return rows

        monkeypatch.setattr(
            "app.infra.conversation.repository.get_messages", fake_get_messages,
        )
        text = await prepare_user_utterances_context("s", 1)

        assert "turn_trace" not in text

    async def test_读库失败降级为空(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def boom(session_id, user_id):
            raise RuntimeError("db down")

        monkeypatch.setattr("app.infra.conversation.repository.get_messages", boom)

        assert await prepare_user_utterances_context("s", 1) == ""
