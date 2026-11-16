"""真数据 · 真流程验证对话压缩（DSH 对标 + 数据豁免）。

**为什么单独一个文件**：`tests/contracts/test_compaction_engine.py` 用合成消息钉算法边界（快、必跑）；
这里回答的是另一个问题——**拿真实业务数据跑真实链路，压缩到底丢没丢东西**。所以本文件：

- 数字**来自真实数仓**（`public.fact_orders` + `dim_date` + `dim_store` 现查：2024 华东/华南净销售额、
  月度 Top2、真实订单号与金额、真实同比），不是手编的；
- 走**生产入口** `ContextRuntime().build(agent="confirmed_execution_sql_agent")`
  → 真实 `LLMConfig`（窗口 65536）→ 真实 LLM 摘要 → 真实 `agent.session` digest 落库；
- 下游用**真实** `sql_graph._plan`（真 LLM）做差分：压缩上下文 vs 全量原文，结论必须一致；
- 失败路径用**真实** `LLMAdapter`（坏 base_url）打，不用 mock。

跑法（缺 env 自动跳过，不进 CI 门禁）：

    RUN_REAL_COMPACTION=1 pytest tests/persistence/test_compaction_real_flow.py -q -s

成本：4 次成功 LLM 调用 + 1 次失败调用（≈1~2 分钟），共用一次编排（module 级 fixture）。
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

# 真 LLM 需要仓库根 .env 的 key/窗口：在 import 业务模块之前加载（与 app.main 一致）。
try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=str(Path(__file__).resolve().parents[3] / ".env"))
except ImportError:  # pragma: no cover - dotenv 随 requirements 安装
    pass

from app.infra.checkpoint.session import session_manager
from app.infra.conversation.repository import get_messages, save_message
from app.memory.compaction import (
    classify_units,
    estimate_tokens,
    numbers_not_in_facts,
    protected_fragments,
    resolve_spec,
    select_fold_span,
)

pytestmark = pytest.mark.persistence

_DSN = os.getenv("DATABASE_URL", "postgresql://ragent:ragent@localhost:5432/ragent")

# 真实追问：必须命中 SelectiveRecallPolicy 的「历史引用」词表（刚才/那个/再按/按月），
# 否则咨询策略会判「不需要历史」，压缩路径根本不会被调用（这是链路的真实行为）。
FOLLOW_UP = "刚才那个再按月标注一下最高和最低的"

# 真 LLM 调用+真数仓，不进 CI 门禁：显式开关。
if not os.getenv("RUN_REAL_COMPACTION"):
    pytest.skip(
        "RUN_REAL_COMPACTION not set（本文件真 LLM + 真数仓，≈1~2 分钟）",
        allow_module_level=True,
    )


# --- 真数据：从数仓现查 ------------------------------------------------------


def _real_business_facts() -> dict[str, Any]:
    """现查真实业务数据（2024 华东/华南、月度 Top2、真实订单、真实同比）。"""
    import psycopg2

    conn = psycopg2.connect(_DSN)
    conn.autocommit = True
    try:
        cur = conn.cursor()

        def _region_total(year: int, region: str) -> float:
            cur.execute(
                """SELECT COALESCE(SUM(o.order_amount), 0)
                     FROM public.fact_orders o
                     JOIN public.dim_date d ON d.full_date = o.order_date
                     JOIN public.dim_store s ON s.store_id = o.store_id
                    WHERE d.year = %s AND s.region = %s""",
                (year, region),
            )
            return float(cur.fetchone()[0])

        cur.execute(
            """SELECT d.month, SUM(o.order_amount) AS amt
                 FROM public.fact_orders o
                 JOIN public.dim_date d ON d.full_date = o.order_date
                 JOIN public.dim_store s ON s.store_id = o.store_id
                WHERE d.year = 2024 AND s.region = '华东'
                GROUP BY d.month ORDER BY amt DESC LIMIT 2"""
        )
        top_months = [(int(m), float(a)) for m, a in cur.fetchall()]

        cur.execute(
            """SELECT o.order_id, o.order_amount
                 FROM public.fact_orders o
                 JOIN public.dim_date d ON d.full_date = o.order_date
                 JOIN public.dim_store s ON s.store_id = o.store_id
                WHERE d.year = 2024 AND s.region = '华东'
                ORDER BY o.order_amount DESC LIMIT 1"""
        )
        order_id, order_amount = cur.fetchone()

        east_2024 = _region_total(2024, "华东")
        east_2023 = _region_total(2023, "华东")
        cur.execute(
            """SELECT d.month, SUM(o.order_amount)
                 FROM public.fact_orders o
                 JOIN public.dim_date d ON d.full_date = o.order_date
                 JOIN public.dim_store s ON s.store_id = o.store_id
                WHERE d.year = 2024 AND s.region = '华东'
                GROUP BY d.month"""
        )
        monthly_2024 = {int(m): float(a) / 10000 for m, a in cur.fetchall()}
        return {
            "east_2024_wan": east_2024 / 10000,
            "south_2024_wan": _region_total(2024, "华南") / 10000,
            "yoy_pct": (east_2024 - east_2023) / east_2023 * 100 if east_2023 else 0.0,
            "top_month": top_months[0][0],
            "top_month_wan": top_months[0][1] / 10000,
            "second_month": top_months[1][0],
            "second_month_wan": top_months[1][1] / 10000,
            "order_id": str(order_id),
            "order_amount_wan": float(order_amount) / 10000,
            "monthly_2024": monthly_2024,
        }
    finally:
        conn.close()


def _turns(facts: dict[str, Any]) -> list[tuple[str, str]]:
    """40 轮真实数据分析会话：数字全部来自上面现查的真值。

    构成刻意贴近真实长会话：**少量取数轮 + 大量口径/维度/格式讨论**。
    后者正是压缩的收益来源（叙述长、数字少），前者是数据通道必须逐字保住的样本。
    """
    numeric = [
        ("先看 2024 年华东的净销售额",
         f"2024年华东净销售额 {facts['east_2024_wan']:.2f} 万元。"),
        ("按月拆开，最高的那两个月标注一下",
         f"已按月生成，最高是 {facts['top_month']}月（{facts['top_month_wan']:.2f} 万元）"
         f"和 {facts['second_month']}月（{facts['second_month_wan']:.2f} 万元）。"),
        ("那 2024 年华南呢",
         f"2024年华南净销售额 {facts['south_2024_wan']:.2f} 万元。"),
        (f"订单 {facts['order_id']} 这单多少钱",
         f"订单 {facts['order_id']} 金额 {facts['order_amount_wan']:.2f} 万元。"),
        (f"同比按 {facts['yoy_pct']:.1f}% 那版算",
         f"好的，同比口径 {facts['yoy_pct']:.1f}%。"),
        ("只要金额 5000 元以上的",
         "好的，过滤 5000 元以上。"),
    ]
    narrative = [
        ("华南和华东分开展示，不要合并",
         "好的，两个区域分行展示：华东和华南各自一行，不合并成一个合计，这样区域间对比不会被总量掩盖。"
         "后续你问「那个区域的月度」我就按当前选中的区域继续，不会跳回全国。"),
        ("不要环比，要同比",
         "好的，改成同比口径：同比是与去年同期比，环比是与上一个月比。这里用同比，基期是去年同月，"
         "不再计算环比列；如果你后面要环比，我再把那一列加回来。"),
        ("再加一列去年同期",
         "好的，加去年同期列：每个月份右侧并排放去年同期值，并给出同比变化率，方便一眼看出增长来自哪几个月。"),
        ("口径按订单金额还是实收金额",
         "按订单金额（order_amount）口径。实收金额要扣退款，两套口径结果会差一截；"
         "这里先用订单金额，需要切换我再改，改动只影响指标表达式，不影响维度。"),
        ("明细列一下上个月的前 20 行",
         "好的，上个月明细 20 行：日期、区域、门店、品类、数量、金额都会带上，按时间倒序，末尾截断到 20 行。"),
        ("排序改成按金额倒序", "好的，改成按金额倒序。"),
        ("门店名太长了，显示简称就行",
         "好的，门店名做简称处理，保留区域前缀，去掉冗余门店后缀，表格更容易一屏看完。"),
        ("按周看，不要按月", "好的，时间粒度改成周，分组键从月换成周序号。"),
        ("周别从周一开始算", "好的，周别按周一切分，跨年那一周按自然周归属处理。"),
        ("去掉合计行", "好的，去掉合计行，只保留分组明细。"),
        ("图表用柱状图", "好的，图表用柱状图，横轴时间、纵轴金额，区域按颜色区分。"),
        ("别用折线，我要看对比", "好的，换成柱状图做对比，折线只适合看趋势。"),
        ("颜色区分区域", "好的，华东与华南用不同颜色，图例放右上角。"),
        ("把金额单位换成万元", "好的，金额统一按万元展示，小数保留两位。"),
        ("小数点保留一位就够", "好的，统一保留一位小数。"),
        ("加一列占比", "好的，加占比列：各月占全年合计的比例，分母用当前筛选范围的总计。"),
        ("占比按百分比显示", "好的，占比按百分比显示，保留一位小数。"),
        ("维度再加品类", "好的，维度加上品类，行数会变多，先按金额倒序取前若干条避免表格过长。"),
        ("品类只留饮料和生鲜", "好的，品类只保留饮料与生鲜，其他品类过滤掉。"),
        ("时间范围改成整年", "好的，时间范围改成整年，从年初到年末按当前粒度铺满。"),
        ("去年同期的口径要和今年一致", "好的，去年同期用同一套口径与同一套筛选条件，只把年份整体前移一年，避免口径不一致导致同比失真。"),
        ("门店维度换成城市", "好的，门店维度换成城市，聚合层级上移一级。"),
        ("城市按拼音排序", "好的，城市按拼音排序，便于查找。"),
        ("去掉没有数据的月份", "好的，没有数据的月份不展示，避免图表出现断点。"),
        ("月份显示成中文", "好的，月份显示成中文，例如一月、二月。"),
        ("表头加上单位", "好的，表头加上单位，金额列标注万元。"),
        ("再把趋势写一句话", "好的，趋势用一句话概括：全年逐月波动上行，第四季度为高点，年初与年中各有小高峰。"),
        ("这句写进报告开头", "好的，这句话放到报告开头作为结论摘要。"),
        ("报告标题带上时间范围", "好的，报告标题带上时间范围，格式统一为「区域 + 指标 + 粒度 + 时间范围」。"),
        ("导出成表格", "好的，导出为表格，列顺序与当前展示一致。"),
        ("保留两位小数再导出", "好的，导出时统一保留两位小数。"),
        ("下次默认用这套设置", "好的，这套展示设置作为默认：区域分行、按周、金额万元、保留一位小数、柱状图、按拼音排序。"),
        ("以后都按这个口径", "好的，订单金额口径作为默认口径，实收金额需要时再单独切换。"),
        ("最后把华东月度趋势总结一下",
         "好的，华东月度趋势总结：全年按月上中下三段，第四季度是主峰，年初与年中各有小高点，整体同比为正。"),
    ]
    return numeric[:3] + narrative[:8] + numeric[3:] + narrative[8:]


# --- 编排：一次跑完，多断言共享 ---------------------------------------------


def _run(coro):
    from app.infra.db.postgres import close_pool, init_pool

    async def _body():
        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


def _seed(facts: dict[str, Any], turns: list[tuple[str, str]] | None = None) -> tuple[str, int]:
    import psycopg2

    conn = psycopg2.connect(_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app.users (username, password_hash) VALUES (%s, 'x') RETURNING id",
        (f"cmp-{uuid.uuid4().hex[:12]}",),
    )
    user_id = cur.fetchone()[0]
    conn.close()

    session_id = f"cmp-real-{uuid.uuid4().hex[:12]}"
    turns = turns if turns is not None else _turns(facts)

    async def body():
        await session_manager.create_session(session_id, user_id=user_id)
        for user_text, assistant_text in turns:
            await save_message(session_id, user_id, "user", user_text, "text")
            await save_message(session_id, user_id, "assistant", assistant_text, "assistant_text")
            # 生产链路每轮都会落一条过程行（只有 metadata）：压缩必须跳过它、且不破坏对齐
            await save_message(session_id, user_id, "assistant", None, "turn_trace", {"entries": []})

    _run(body())
    return session_id, user_id


def _cleanup(session_id: str, user_id: int) -> None:
    import psycopg2

    conn = psycopg2.connect(_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    for table in ("app.conversations",):
        cur.execute(f"DELETE FROM {table} WHERE session_id=%s", (session_id,))
    cur.execute("DELETE FROM agent.session WHERE thread_id=%s", (session_id,))
    # 压缩会经 remember_conversation_facts 写 L3（best-effort）——临时用户的行一并清掉
    for table in ("memory.long_term_memory", "memory.semantic_entry", "memory.merge_candidate"):
        try:
            cur.execute(f"DELETE FROM {table} WHERE user_id=%s", (user_id,))
        except Exception:  # noqa: BLE001 - 表不存在/列名不同都不影响清理结论
            conn.rollback()
    cur.execute("DELETE FROM app.users WHERE id=%s", (user_id,))
    conn.close()


def _build_via_production(session_id: str, user_id: int, query: str):
    """走生产入口：ContextRuntime.build → 真 recall + 真压缩 + digest 落库。"""
    from app.context.runtime import ContextRuntime

    async def body():
        return await ContextRuntime().build(
            session_id=session_id,
            user_id=user_id,
            query=query,
            agent="confirmed_execution_sql_agent",   # EXECUTION 档 → 走 L2 压缩路径
            state_dict={},
        )

    return _run(body())


@pytest.fixture(scope="module")
def flow() -> dict[str, Any]:
    """一次真流程，产出全部观测值（LLM 调用集中在这里，约 1~2 分钟）。"""
    facts = _real_business_facts()
    session_id, user_id = _seed(facts)
    observations: dict[str, Any] = {"facts": facts, "session_id": session_id}
    try:
        messages = _run(get_messages(session_id, user_id))
        observations["messages"] = messages

        # ① 第一轮：触发压缩（12 轮 → 24 条消息 + 12 条过程行）
        bundle = _build_via_production(session_id, user_id, FOLLOW_UP)
        observations["bundle"] = bundle
        observations["context"] = bundle["conversation_context"]
        observations["assembled"] = bundle["assembled_context"]
        observations["state_after_first"] = _run(session_manager.get_context_state(session_id))

        # ② 第二轮：无新消息 → 不应再调 LLM、数据块仍在
        bundle2 = _build_via_production(session_id, user_id, "继续看看")
        observations["context_second"] = bundle2["conversation_context"]
        observations["state_after_second"] = _run(session_manager.get_context_state(session_id))

        # ③ 滚动压缩：追加 2 轮 → digest 必须前进且老数字不丢
        async def _append():
            for user_text, assistant_text in [
                ("把 2023 年华东也拉出来对比", "好的，2023 年华东对比。"),
                ("同比就到今天为止", "好的，同比截至今天。"),
            ]:
                await save_message(session_id, user_id, "user", user_text, "text")
                await save_message(
                    session_id, user_id, "assistant", assistant_text, "assistant_text",
                )

        _run(_append())
        messages_after = _run(get_messages(session_id, user_id))
        observations["messages_after_roll"] = messages_after
        bundle3 = _build_via_production(session_id, user_id, "继续")
        observations["context_roll"] = bundle3["conversation_context"]
        observations["state_after_roll"] = _run(session_manager.get_context_state(session_id))

        # ④ 下游差分：真实 sql_graph._plan，压缩上下文 vs 全量原文
        from app.agent import sql_graph
        from app.memory.conversation import format_messages

        follow_up = FOLLOW_UP
        common = {"schema_context": None, "query_plan": None, "granularity": "month"}
        plan_compressed = sql_graph._plan(
            {**common, "user_query": follow_up, "assembled_context": observations["assembled"]}
        )["query_plan"]
        plan_raw = sql_graph._plan(
            {**common, "user_query": follow_up,
             "conversation_context": format_messages(messages)}
        )["query_plan"]
        observations["plan_compressed"] = plan_compressed
        observations["plan_raw"] = plan_raw

        # ⑤ 降级路径：真 adapter + 坏 base_url（不是 mock）
        observations["degraded"] = _degrade_probe(session_id, user_id)
        return observations
    finally:
        _cleanup(session_id, user_id)


def _degrade_probe(session_id: str, user_id: int) -> dict[str, Any]:
    """把 adapter 换成「真 LLMAdapter + 打不通的 base_url」，验证压缩失败不空。"""
    import app.llm as llm_mod

    original_adapter = getattr(llm_mod, "_adapter", None)
    original_base = os.environ.get("LLM_BASE_URL")
    original_retries = os.environ.get("LLM_MAX_RETRIES")
    try:
        os.environ["LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
        os.environ["LLM_MAX_RETRIES"] = "0"
        llm_mod._adapter = llm_mod.LLMAdapter()

        async def _reset_then_build():
            await session_manager.save_context_state(session_id, {
                "digest": None, "digest_msg_count": 0, "digest_version": 0, "mid_digest": None,
            })
            messages = await get_messages(session_id, user_id)
            from app.memory.conversation import build_context

            return build_context(messages=messages)

        return _run(_reset_then_build())
    finally:
        llm_mod._adapter = original_adapter
        if original_base is None:
            os.environ.pop("LLM_BASE_URL", None)
        else:
            os.environ["LLM_BASE_URL"] = original_base
        if original_retries is None:
            os.environ.pop("LLM_MAX_RETRIES", None)
        else:
            os.environ["LLM_MAX_RETRIES"] = original_retries


# --- 断言 -------------------------------------------------------------------


class TestTriggerAndTail:
    def test_真实会话触发压缩且digest落库(self, flow: dict[str, Any]) -> None:
        state = flow["state_after_first"]
        print(f"\n[触发] digest={'有' if state['digest'] else '无'} "
              f"msg_count={state['digest_msg_count']} version={state['digest_version']}")
        assert state["digest"], "12 轮真会话没触发压缩"
        assert state["digest_msg_count"] > 0
        assert state["digest_version"] == 1

    def test_最新五轮原样留在尾部(self, flow: dict[str, Any]) -> None:
        context = flow["context"]
        assert "<最新对话>" in context
        tail = context.split("<最新对话>")[1]
        turns = _turns(flow["facts"])
        # 尾部 = 最近 5 轮（RECENT_WINDOW=10 条消息），逐字保留
        for user_text, assistant_text in turns[-5:]:
            assert f"user: {user_text}" in tail
            assert f"assistant: {assistant_text}" in tail
        # 被折叠的老轮次不许再出现在尾部
        assert f"user: {turns[0][0]}" not in tail

    def test_过程行被跳过且不计入尾部(self, flow: dict[str, Any]) -> None:
        assert "turn_trace" not in flow["context"]


class TestDataSurvival:
    def test_被折叠区间的每个数字都逐字进了数据事实块(self, flow: dict[str, Any]) -> None:
        context = flow["context"]
        assert "<数据事实>" in context, "数据通道没生效"
        facts_block = context.split("<数据事实>")[1].split("</数据事实>")[0]

        # 折叠区间 = 保留尾部之前的所有消息（按算法自己算，不手猜）
        units = classify_units(flow["messages"])
        from app.memory.conversation import _compaction_spec

        spec = _compaction_spec()
        fold_units, keep_units = select_fold_span(
            units, retain_tokens=spec.retain_tokens, ceiling_messages=10,
        )
        assert keep_units, "保尾区间为空，用例前提不成立"
        expected: list[str] = []
        for unit in fold_units:
            for piece in protected_fragments(unit.text):
                if piece not in expected:
                    expected.append(piece)

        print(f"\n[数据事实] 期望 {len(expected)} 个片段：{expected}")
        print(f"[实际注入] {facts_block.strip()}")
        assert expected, "折叠区间里一个数字都没有，用例前提不成立"
        missing = [piece for piece in expected if piece not in facts_block]
        assert missing == [], f"这些数据被压缩吃掉了：{missing}"

    def test_真实业务数值逐字可核对(self, flow: dict[str, Any]) -> None:
        facts_block = flow["context"].split("<数据事实>")[1].split("</数据事实>")[0]
        biz = flow["facts"]
        for label, value in (
            ("2024华东净销售额", f"{biz['east_2024_wan']:.2f} 万元"),
            ("2024华南净销售额", f"{biz['south_2024_wan']:.2f} 万元"),
            ("真实订单号", biz["order_id"]),
            ("同比", f"{biz['yoy_pct']:.1f}%"),
            ("Top1 月份", f"{biz['top_month']}月"),
        ):
            assert value in facts_block, f"{label} 的 {value} 不在数据事实块里"

    def test_checkpoint的数字都能在数据事实里逐字对上(self, flow: dict[str, Any]) -> None:
        """模型可以引用数值，但不许改写/编造：每个数字都得在数据事实里逐字存在。"""
        digest = flow["state_after_first"]["digest"]
        facts_block = flow["context"].split("<数据事实>")[1].split("</数据事实>")[0]
        degraded = not digest.lstrip().startswith("## ")
        print(f"\n[checkpoint] {'降级折法' if degraded else 'LLM 结构化'}\n{digest}")
        invented = numbers_not_in_facts(digest, facts_block)
        print(f"[数字对账] 越界数字={invented}")
        assert invented == [], f"checkpoint 里这些数字在数据事实中不存在：{invented}"
        if not degraded:
            for section in ("## 用户目标", "## 已确认口径", "## 用户纠正"):
                assert section in digest, f"checkpoint 缺段：{section}"

    def test_压缩确实变小了(self, flow: dict[str, Any]) -> None:
        from app.memory.conversation import format_messages

        raw = format_messages(flow["messages"])
        context = flow["context"]
        compiled = "<对话摘要>" in context
        print(f"\n[体积] 原文 {len(raw)} chars ≈ {estimate_tokens(raw)} tokens → "
              f"压缩后 {len(context)} chars ≈ {estimate_tokens(context)} tokens "
              f"({'已压缩' if compiled else '净收益守门拒绝，原样返回'})")
        # 硬契约：压缩后的上下文永远不许比原文更大（宁可放弃压缩）
        assert estimate_tokens(context) <= estimate_tokens(raw)
        if compiled:
            assert estimate_tokens(context) < estimate_tokens(raw), "压缩没变小就不该落库"


class TestRollingAndIdempotence:
    def test_无新消息不重复调LLM(self, flow: dict[str, Any]) -> None:
        assert flow["state_after_second"] == flow["state_after_first"], "没新消息却重写了 digest"
        assert "<数据事实>" in flow["context_second"], "第二轮丢了数据事实块"

    def test_追加两轮后digest前进且老数字不丢(self, flow: dict[str, Any]) -> None:
        before = flow["state_after_second"]["digest_msg_count"]
        after = flow["state_after_roll"]["digest_msg_count"]
        print(f"\n[滚动] digest_msg_count {before} → {after}")
        assert after > before, "追加消息后没有继续压缩"
        facts_block = flow["context_roll"].split("<数据事实>")[1].split("</数据事实>")[0]
        biz = flow["facts"]
        assert f"{biz['east_2024_wan']:.2f} 万元" in facts_block, "滚动压缩把第一轮的数值丢了"
        assert "2023 年华东" in flow["context_roll"], "新折叠的轮次没有进入叙述/尾部"


class TestDownstreamEquivalence:
    def test_压缩上下文与全量原文给出同样的执行计划(self, flow: dict[str, Any]) -> None:
        compressed = flow["plan_compressed"]
        raw = flow["plan_raw"]
        print(f"\n[plan/压缩] time_range={compressed.time_range!r} "
              f"metric={compressed.target_metric!r} dims={compressed.dimensions}")
        print(f"[plan/原文] time_range={raw.time_range!r} "
              f"metric={raw.target_metric!r} dims={raw.dimensions}")

        for plan, label in ((compressed, "压缩"), (raw, "原文")):
            assert "2024" in str(plan.time_range), f"{label}上下文丢了年份"
            assert "销" in plan.target_metric, f"{label}上下文丢了指标"
            assert any("月" in dim for dim in plan.dimensions), f"{label}上下文丢了月度粒度"


class TestShortSession:
    def test_短会话不压缩原样返回(self, flow: dict[str, Any]) -> None:
        """4 轮/8 条真实消息：远低于双闸阈值 → 一个字都不该动、digest 不落库。"""
        facts = flow["facts"]
        session_id, user_id = _seed(facts, turns=_turns(facts)[-4:])
        try:
            bundle = _build_via_production(session_id, user_id, FOLLOW_UP)
            state = _run(session_manager.get_context_state(session_id))
            print(f"\n[短会话] 4 轮/8 条：digest={'有' if state['digest'] else '无'} "
                  f"chars={len(bundle['conversation_context'])}")
            assert state["digest"] is None and state["digest_msg_count"] == 0
            assert "<对话摘要>" not in bundle["conversation_context"]
            assert "<数据事实>" not in bundle["conversation_context"]
            assert "user: " in bundle["conversation_context"]   # 原文直给
        finally:
            _cleanup(session_id, user_id)


class TestDegrade:
    def test_真实LLM打不通时降级不空(self, flow: dict[str, Any]) -> None:
        context, updates, _ = flow["degraded"]
        print(f"\n[降级] chars={len(context)} digest_len={len(updates.get('digest') or '')}")
        assert context.strip(), "压缩失败把上下文整块丢空了"
        # 失败路径的硬契约只有两条：不空、不丢最新内容（要么原文，要么尾部原样）
        assert "2023 年华东" in context, "降级后最新一轮被丢了"
        if updates.get("digest"):
            assert updates["digest"].startswith("user: "), "降级时应退成确定性骨架"
        else:
            assert "<对话摘要>" not in context, "没落 digest 却渲染了摘要块"
        print(f"[降级] 模式={'骨架折法' if updates.get('digest') else '原样返回'}")
