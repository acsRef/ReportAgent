"""对话压缩引擎契约（对标 DSH compaction-basic，数据豁免）。

三条硬约束（plan `2026-11-16-compaction-dsh-alignment.md`）：

1. **保尾**：压缩的是最老的前缀，最近的一问一答永不进折叠区间，且**绝不切开单元**；
2. **数据不参与压缩**：日期/金额/百分比/编号由确定性正则逐字抽取进 `<数据事实>`，
   模型写的 checkpoint 里**出现任何数字都判越界**（退回确定性折法）；
3. **失败不空**：LLM 抛错 / 摘要没变短 → 退回确定性折法，上下文不许变空。
"""
from __future__ import annotations

import pytest

from app.context.assembler import ContextAssembler, RecallItem
from app.context.policy import AgentContextPolicy
from app.memory import compaction as C
from app.memory import conversation as conv
from app.memory.prompts import CONVERSATION_CHECKPOINT_V2

pytestmark = pytest.mark.contracts


# --- 计量 / 预算 ------------------------------------------------------------


class TestEstimateTokens:
    @pytest.mark.parametrize(("text", "expected"), [
        ("", 0),
        ("abc", 1),
        ("abcd", 2),
        ("a" * 3, 1),
        ("a" * 3000, 1000),
    ])
    def test_按三字符估一token(self, text: str, expected: int) -> None:
        assert C.estimate_tokens(text) == expected


class TestResolveSpec:
    def test_阈值取窗口比例与压力预算的较小者(self) -> None:
        spec = C.resolve_spec(context_window=10000, reserved_completion_tokens=1000)
        # headroom 缺省 = max(1024, 10000//8=1250) = 1250
        assert spec.headroom_tokens == 1250
        # pressure = 10000-1000-1250 = 7750 < 10000*0.8=8000 → 取 7750
        assert spec.threshold_tokens == 7750
        # retain = (窗口-预留)×0.16 = 9000×0.16 = 1440
        assert spec.retain_tokens == 1440
        assert spec.retain_tokens < spec.threshold_tokens

    def test_显式余量(self) -> None:
        spec = C.resolve_spec(
            context_window=20000, reserved_completion_tokens=0, headroom_tokens=0,
        )
        assert spec.threshold_tokens == 16000   # min(16000, 20000)
        assert spec.retain_tokens == 3200

    @pytest.mark.parametrize(("kwargs", "hint"), [
        ({"context_window": 0}, "context_window"),
        ({"context_window": 1000, "reserved_completion_tokens": 1000}, "消息预算"),
        ({"context_window": 1000, "headroom_tokens": 2000}, "余量"),
        ({"context_window": 1000, "threshold_ratio": 0}, "threshold_ratio"),
        ({"context_window": 10000, "retain_ratio": 0.9}, "retain_tokens"),
    ])
    def test_配置打架报配置错误(self, kwargs: dict, hint: str) -> None:
        with pytest.raises(C.CompactionConfigError) as exc:
            C.resolve_spec(**kwargs)
        assert hint in str(exc.value)


class TestShouldCompact:
    def test_token_闸(self) -> None:
        spec = C.resolve_spec(context_window=1000, headroom_tokens=0)
        assert C.should_compact(
            message_count=2, measured_tokens=spec.threshold_tokens, spec=spec, message_ceiling=20,
        )

    def test_条数兜底闸(self) -> None:
        spec = C.resolve_spec(context_window=100_000, headroom_tokens=0)
        assert C.should_compact(
            message_count=21, measured_tokens=10, spec=spec, message_ceiling=20,
        )
        assert not C.should_compact(
            message_count=20, measured_tokens=10, spec=spec, message_ceiling=20,
        )


# --- 单元切分 ---------------------------------------------------------------


def _row(index: int, role: str, content: str, message_type: str = "text") -> dict:
    return {"id": index + 1, "role": role, "content": content, "message_type": message_type}


class TestClassifyUnits:
    def test_一问一答成对且不切分(self) -> None:
        messages = [
            _row(0, "user", "看 2024年3月 华东销售额"),
            _row(1, "assistant", "好，2024年3月华东销售额", "assistant_text"),
            _row(2, "user", "再看看 2023年"),
            _row(3, "assistant", "好的，2023年", "assistant_text"),
        ]
        units = C.classify_units(messages)
        assert [len(u.messages) for u in units] == [2, 2]
        assert units[0].text.startswith("user: 看 2024年3月")
        assert units[1].start == 2          # 原始下标，不是过滤后的序号

    def test_跳过过程行但保留原始下标(self) -> None:
        messages = [
            _row(0, "user", "问题一"),
            _row(1, "assistant", "", "turn_trace"),
            _row(2, "system", "", "requirement_patch"),
            _row(3, "assistant", "答复一", "assistant_text"),
            _row(4, "user", "问题二"),
        ]
        units = C.classify_units(messages)
        assert [u.start for u in units] == [0, 4]
        assert [len(u.messages) for u in units] == [2, 1]

    def test_空内容不入单元(self) -> None:
        assert C.classify_units([_row(0, "user", "   ")]) == []


# --- 保尾选范围 -------------------------------------------------------------


def _units(sizes: list[int]) -> list[C.ConversationUnit]:
    """构造 `sizes` 个单位，每个单位 `size` 条消息（每条 3 字符 → 1 token）。"""
    units: list[C.ConversationUnit] = []
    cursor = 0
    for index, size in enumerate(sizes):
        rows = tuple(
            {"id": cursor + offset + 1, "role": "user" if offset == 0 else "assistant",
             "content": f"m{cursor + offset}", "message_type": "text" if offset == 0 else "assistant_text"}
            for offset in range(size)
        )
        cursor += size
        text = "\n".join(f"user: {r['content']}" for r in rows)
        units.append(C.ConversationUnit(
            start=index * size, message_ids=(), messages=rows, text=text,
            tokens=C.estimate_tokens(text),
        ))
    return units


class TestSelectFoldSpan:
    def test_保尾且受条数上限约束(self) -> None:
        units = _units([2] * 11)          # 11 组问答 = 22 条
        fold, keep = C.select_fold_span(units, retain_tokens=10_000, ceiling_messages=10)
        assert sum(len(u.messages) for u in keep) == 10
        assert sum(len(u.messages) for u in fold) == 12
        assert keep[0].start == 12        # 折叠区间的右开边界

    def test_token_压力可缩短尾部但守住下限(self) -> None:
        units = _units([2] * 6)
        big = [
            C.ConversationUnit(
                start=u.start, message_ids=(), messages=u.messages, text=u.text, tokens=5000,
            )
            for u in units
        ]
        fold, keep = C.select_fold_span(big, retain_tokens=6000, ceiling_messages=10, floor_messages=3)
        # 5000×2 已超 6000，但下限 3 条 → 继续收到 4 条
        assert sum(len(u.messages) for u in keep) == 4
        assert sum(len(u.messages) for u in fold) == 8

    def test_没有可折叠前缀时返回空折叠(self) -> None:
        units = _units([2])
        fold, keep = C.select_fold_span(units, retain_tokens=10_000, ceiling_messages=10)
        assert fold == [] and keep == units


# --- 数据通道 ---------------------------------------------------------------


class TestDataFacts:
    def test_逐字抽取且去重(self) -> None:
        units = _units([1, 1])
        units = [
            C.ConversationUnit(
                start=u.start, message_ids=(), messages=u.messages, text=u.text, tokens=u.tokens,
            )
            for u in units
        ]
        units[0] = C.ConversationUnit(
            start=0, message_ids=(), messages=(), tokens=1,
            text="user: 看 2024年11月 华东 2630 万元 同比 12.5%",
        )
        units[1] = C.ConversationUnit(
            start=1, message_ids=(), messages=(), tokens=1,
            text="assistant: 还是 2024年11月，2630 万元",
        )
        facts = C.collect_data_facts(units)
        assert facts == ["2024年11月", "2630 万元", "12.5%"]

    def test_上限按条数而不是字符(self) -> None:
        text = "\n".join(f"user: 2024年{i + 1}月 华东 {i + 1}00 万元" for i in range(10))
        unit = C.ConversationUnit(start=0, message_ids=(), messages=(), text=text, tokens=10)
        facts = C.collect_data_facts([unit], max_facts=4)
        assert len(facts) == 4
        assert all(f in text for f in facts)      # 一条都没被截断


class TestNumericConsistency:
    """数值一致性闸：引用合法、改写/编造越界（2026-11-16 替换「一刀切禁数字」）。"""

    FACTS = "2024年11月、9631.81 万元、12.5%"

    def test_逐字引用不越界(self) -> None:
        summary = "## 已确认口径\n- 净销售额，最高是 11 月（9631.81 万元），同比 12.5%"
        assert C.numbers_not_in_facts(summary, self.FACTS) == []

    @pytest.mark.parametrize("summary", [
        "- 净销售额约 9631 万元（把 9631.81 四舍五入了）",
        "- 全年 9999 万元（编造）",
        "- 同比 13%（把 12.5 改了）",
    ])
    def test_改写或编造即越界(self, summary: str) -> None:
        assert C.numbers_not_in_facts(summary, self.FACTS) != []

    def test_全角数字归一后比对(self) -> None:
        assert C.numeric_tokens("１２３ 与 123") == {"123"}

    def test_纯叙述不越界(self) -> None:
        assert C.numbers_not_in_facts("## 用户目标\n- 想看月度趋势", "") == []


class TestFoldDeterministic:
    def test_只留骨架且比原文短(self) -> None:
        unit = C.ConversationUnit(
            start=0, message_ids=(), messages=(
                {"role": "user", "message_type": "text",
                 "content": "帮我分析一下 2024年11月 华东区域 净销售额 2630 万元 的情况，以及同比增长 12.5% 的原因"},
            ), tokens=10,
            text="",
        )
        folded = C.fold_deterministic([unit])
        # 2026-11-16 更正：数据只走 <数据事实> 一处，叙述通道不再追加「（数据：…）」
        # （重复会把折叠体积撑得比原文还大，真流程测试实测 258 > 204 tokens）
        assert "（数据：" not in folded
        assert folded.startswith("user: 帮我分析一下")
        # 骨架不许切出半截日期（`2024年1` 比丢掉它更危险）
        assert "2024年1 " not in folded and not folded.rstrip("…").endswith("2024年11")
        assert len(folded) < len("user: " + unit.messages[0]["content"])


# --- build_context 端到端（DSH 五步 + 数据豁免） ---------------------------


_SPEC_KWARGS = {"context_window": 200, "reserved_completion_tokens": 0, "headroom_tokens": 50}


_FILLER_USER = "，口径按订单金额算，筛选条件沿用上一轮，门店明细一起带上，按金额从高到低排，不要合并成一行"
_FILLER_ASSISTANT = "已按你的要求处理：口径沿用订单金额，未做合并，门店明细按金额倒序排列，时间范围与筛选条件都与上一轮一致"


def _numbered_messages(turns: int = 9) -> list[dict]:
    """真长消息（每条约 100+ 字）：短消息压骨架等于没压，会被净收益守门拒绝。"""
    rows: list[dict] = []
    for index in range(turns):
        month = index % 12 + 1
        rows.append({
            "id": index * 2 + 1, "role": "user", "message_type": "text",
            "content": f"看 2024年{month}月 华东的净销售额，大概 {month}000 万元{_FILLER_USER}",
        })
        rows.append({
            "id": index * 2 + 2, "role": "assistant", "message_type": "assistant_text",
            "content": f"好，2024年{month}月华东净销售额。{_FILLER_ASSISTANT}",
        })
    return rows


def _patch_summary(monkeypatch: pytest.MonkeyPatch, summary: str) -> None:
    monkeypatch.setattr(
        conv, "compress_and_extract",
        lambda old, batch: {
            "summary": summary,
            "extracted_schemas": [],
            "extracted_preferences": [],
        },
    )


class TestBuildContextCompaction:
    def test_数据事实块逐字保留被折叠的数字(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_summary(monkeypatch, "## 用户目标\n- 按月看华东净销售额\n## 已确认口径\n- 销售额/按月")

        ctx, updates, batch = conv.build_context(
            messages=_numbered_messages(), **_SPEC_KWARGS,
        )

        assert "<数据事实>" in ctx
        assert "2024年1月" in ctx and "1000 万元" in ctx   # 被折叠区间里的数字一条没丢
        assert "<对话摘要>\n## 用户目标" in ctx
        facts_block = ctx.split("<数据事实>")[1].split("</数据事实>")[0]
        tail_block = ctx.split("<最新对话>")[1]
        assert "2024年1月" in facts_block
        assert "2024年1月" not in tail_block     # 最新对话只留尾部，老轮次进折叠
        assert updates["digest"].startswith("## 用户目标")
        assert updates["digest_msg_count"] > 0
        assert batch

    def test_checkpoint_编造数字就退回确定性折法(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 9999 万元 在折叠区间的数据事实里不存在 → 越界
        _patch_summary(monkeypatch, "## 用户目标\n- 看 9999 万元的月度分布")

        ctx, updates, _ = conv.build_context(messages=_numbered_messages(), **_SPEC_KWARGS)

        # 越界的 checkpoint 不许落库：digest 变成确定性折法（只留骨架）
        assert "9999 万元" not in updates["digest"]
        assert updates["digest"].startswith("user: ")
        facts_block = ctx.split("<数据事实>")[1]
        assert "1000 万元" in facts_block        # 真实数字仍在数据通道里
        assert "<对话摘要>" in ctx

    def test_LLM_失败降级不空(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(old, batch):
            raise RuntimeError("llm down")

        monkeypatch.setattr(conv, "compress_and_extract", boom)

        ctx, updates, _ = conv.build_context(messages=_numbered_messages(), **_SPEC_KWARGS)

        assert ctx.strip() != ""
        assert "<最新对话>" in ctx
        assert updates["digest"]                     # 兜底折法仍落库
        assert "2024年" in ctx                       # 数字在数据事实块里

    def test_shrink_guard_拒绝更长的摘要(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_summary(monkeypatch, "长" * 2000)

        _, updates, _ = conv.build_context(messages=_numbered_messages(), **_SPEC_KWARGS)

        assert len(updates["digest"]) < 2000
        # 退化成确定性折法（骨架），不是模型那段 2000 字的长文
        assert updates["digest"].startswith("user: ")
        assert "长" * 50 not in updates["digest"]

    def test_最新几轮永远原样(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_summary(monkeypatch, "## 用户目标\n- 按月看")

        ctx, _, _ = conv.build_context(messages=_numbered_messages(), **_SPEC_KWARGS)

        tail = ctx.split("<最新对话>")[1]
        # 最后一轮（9 月）必须逐字在尾部，不被压成骨架
        assert "user: 看 2024年9月 华东的净销售额，大概 9000 万元" in tail
        assert "assistant: 好，2024年9月华东净销售额。" in tail

    def test_配置打架时跳过压缩而不是打挂(self) -> None:
        ctx, updates, _ = conv.build_context(
            messages=_numbered_messages(), context_window=0,
        )
        assert updates == {}
        assert "2024年9月" in ctx          # 原样给出，不压缩


class TestCheckpointPrompt:
    def test_禁止写数值且段位固定(self) -> None:
        prompt = " ".join(CONVERSATION_CHECKPOINT_V2.values())
        for section in ("用户目标", "已确认口径", "用户纠正", "已否决的写法", "未完成事项"):
            assert f"## {section}" in prompt
        assert "Numbers must come from the conversation verbatim" in prompt


# --- assembler 预算：保尾 ---------------------------------------------------


class TestAssemblerKeepsTail:
    def test_超预算丢最老的行而不是最新的(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("P4C_ASSEMBLER_TOKEN_BUDGET", "1000")   # 3000 chars
        conversation = "\n".join(f"第{i}轮：老内容" for i in range(400))
        bundle = ContextAssembler().assemble(
            conversation_context=conversation,
            recall_items=[],
            agent_policy=AgentContextPolicy.EXECUTION,
        )
        assembled = bundle["assembled_context"]
        assert len(assembled) <= 3000
        assert "第399轮" in assembled        # 最新一行必须活着
        assert "第0轮" not in assembled      # 最老的一行被丢

    def test_召回块按优先级从尾部丢(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("P4C_ASSEMBLER_TOKEN_BUDGET", "60")     # 180 chars
        items = [RecallItem(raw_text="Q" * 100, source="memory_query", kind="query", ref_id=1)]
        bundle = ContextAssembler().assemble(
            conversation_context="最新一轮" * 20,
            recall_items=items,
            agent_policy=AgentContextPolicy.EXECUTION,
        )
        assembled = bundle["assembled_context"]
        assert len(assembled) <= 180
        assert "最新" in assembled
