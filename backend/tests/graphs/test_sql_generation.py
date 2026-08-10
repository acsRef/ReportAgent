"""Unit tests for SQL generation sanitisation.

Tests the `extract_sql` / `strip_think` helpers and the `_generate_sql`
node's handling of think-block variants (closed, unclosed, absent).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.graphs

from app.utils.text import extract_sql, strip_think
from app.models.contracts import ColumnSchema, SchemaContext, TableSchema


# ── strip_think ─────────────────────────────────────────────────────────

def test_strip_think_removes_closed_block() -> None:
    result = strip_think("<think>some reasoning</think>\nSELECT * FROM t")
    assert result == "SELECT * FROM t"


def test_strip_think_unclosed_block_unchanged() -> None:
    """No </think> — the tag is left in place; extract_sql handles it later."""
    text = "<think>some reasoning\nSELECT * FROM t"
    assert strip_think(text) == text


def test_strip_think_no_block_unchanged() -> None:
    text = "SELECT * FROM t"
    assert strip_think(text) == text


def test_strip_think_multiple_blocks() -> None:
    result = strip_think(
        "<think>first</think>garbage<think>second</think>\nSELECT * FROM t"
    )
    assert result == "garbage\nSELECT * FROM t"


# ── extract_sql ─────────────────────────────────────────────────────────

def test_extract_sql_closed_think() -> None:
    result = extract_sql("<think>reasoning</think>\nSELECT * FROM t")
    assert result == "SELECT * FROM t"


def test_extract_sql_unclosed_think_finds_select() -> None:
    """No </think> — strips to first SELECT."""
    result = extract_sql("<think>some reasoning\nSELECT * FROM t")
    assert result == "SELECT * FROM t"


def test_extract_sql_pure_sql() -> None:
    result = extract_sql("SELECT * FROM t")
    assert result == "SELECT * FROM t"


def test_extract_sql_truncated_no_sql() -> None:
    assert extract_sql("<think>truncated reasoning") == ""


def test_extract_sql_empty() -> None:
    assert extract_sql("") == ""


def test_extract_sql_markdown_fence() -> None:
    result = extract_sql("```sql\nSELECT * FROM t\n```")
    assert result == "SELECT * FROM t"


def test_extract_sql_garbage_before_select() -> None:
    result = extract_sql("Here is your SQL:\nSELECT * FROM t")
    assert result == "SELECT * FROM t"


def test_extract_sql_leading_comment_safe() -> None:
    """v2 鲁棒性：「附注释」规则已删，但万一模型仍输出 `-- 注释\\nSELECT…`，
    extract_sql 也要能安全剥出纯 SELECT，不产生残缺 SQL。"""
    result = extract_sql(
        "-- 主表 fact_sales，经 date_id 关联 dim_date\nSELECT * FROM fact_sales"
    )
    assert result == "SELECT * FROM fact_sales"


# ── _generate_sql integration ───────────────────────────────────────────

def _patch_llm(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """Force both the consumer module and app.llm to return fixed text."""
    import app.agent.sql_graph as graph_mod
    import app.llm as llm_mod
    monkeypatch.setattr(graph_mod, "call_llm", lambda *a, **k: text)
    monkeypatch.setattr(llm_mod, "call_llm", lambda *a, **k: text)


def _minimal_state() -> dict:
    return {
        "query_plan": None,
        "schema_context": None,
        "generated_sql": "",
        "retry_counters": {"sql_generation": 0, "plan": 0},
    }


def test_generate_sql_closed_think(monkeypatch) -> None:
    _patch_llm(monkeypatch, "<think>use fact_sales</think>\nSELECT * FROM t")
    from app.agent.sql_graph import _generate_sql
    result = _generate_sql(_minimal_state())
    assert result["generated_sql"] == "SELECT * FROM t"
    assert result["retry_counters"]["sql_generation"] == 1


def test_generate_sql_unclosed_think(monkeypatch) -> None:
    _patch_llm(monkeypatch, "<think>some reasoning\nSELECT * FROM t")
    from app.agent.sql_graph import _generate_sql
    result = _generate_sql(_minimal_state())
    assert result["generated_sql"] == "SELECT * FROM t"


def test_generate_sql_pure_sql(monkeypatch) -> None:
    _patch_llm(monkeypatch, "SELECT * FROM t")
    from app.agent.sql_graph import _generate_sql
    result = _generate_sql(_minimal_state())
    assert result["generated_sql"] == "SELECT * FROM t"


def test_generate_sql_truncated_returns_empty(monkeypatch) -> None:
    _patch_llm(monkeypatch, "<think>truncated")
    from app.agent.sql_graph import _generate_sql
    result = _generate_sql(_minimal_state())
    assert result["generated_sql"] == ""


def test_generate_sql_empty_response(monkeypatch) -> None:
    _patch_llm(monkeypatch, "")
    from app.agent.sql_graph import _generate_sql
    result = _generate_sql(_minimal_state())
    assert result["generated_sql"] == ""


# ── _generate_sql prompt 内容断言（JOIN / 时间 / 数组 规则）──────────────

def _capture_prompt(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Monkeypatch call_llm to capture the exact prompt handed to the LLM.

    _generate_sql 调用 call_llm([{"role": "user", "content": prompt}], ...)
    —— 单消息列表形态。返回 {"captured": ..., "calls": int}。
    """
    captured: dict = {"text": "", "calls": 0}

    def fake_call_llm(messages, **kwargs):
        captured["calls"] += 1
        if isinstance(messages, list) and messages:
            captured["text"] = messages[0].get("content", "")
        else:
            captured["text"] = str(messages)
        return "SELECT 1"

    import app.agent.sql_graph as graph_mod
    monkeypatch.setattr(graph_mod, "call_llm", fake_call_llm)
    return captured


def _multi_table_schema() -> SchemaContext:
    """5 张表 schema：fact_sales + 4 张维度表，制造多 JOIN 场景。"""
    cols = lambda names: [ColumnSchema(name=n, type="integer") for n in names]
    return SchemaContext(
        source="test",
        tables=[
            TableSchema(
                name="fact_sales",
                description="销售事实",
                columns=cols(["sale_id", "date_id", "product_id", "region_id",
                              "customer_id", "quantity", "total_amount"]),
                relationships=[
                    {"foreign_table": "dim_date", "foreign_key": "date_id"},
                    {"foreign_table": "dim_region", "foreign_key": "region_id"},
                    {"foreign_table": "dim_product", "foreign_key": "product_id"},
                    {"foreign_table": "dim_customer", "foreign_key": "customer_id"},
                ],
            ),
            TableSchema(
                name="dim_region",
                description="区域维度",
                columns=cols(["region_id", "region_name", "tier"]),
            ),
            TableSchema(
                name="dim_product",
                description="产品维度",
                columns=cols(["product_id", "product_name", "category"]),
            ),
            TableSchema(
                name="dim_customer",
                description="客户维度",
                columns=cols(["customer_id", "customer_name", "customer_tier"]),
            ),
            TableSchema(
                name="dim_date",
                description="日期维度",
                columns=cols(["date_id", "full_date", "year", "quarter_num",
                              "quarter", "week_of_year", "day_name"]),
            ),
        ],
    )


def test_generate_sql_prompt_contains_join_rules(monkeypatch) -> None:
    """JOIN 8 条规则必须原样出现在 prompt 里（LEFT JOIN / GROUP BY / LIMIT 200 /
    子查询分层 / 不臆造列）。v2：移除「附注释」断言——extract_sql 会剥掉开头注释，
    该规则已删（见 plan 修订 v2 问题 3）。"""
    from app.agent.sql_graph import _generate_sql
    captured = _capture_prompt(monkeypatch)
    state = {
        **_minimal_state(),
        "user_query": "华东一线城市 2024 年各品类销售额排名",
        "schema_context": _multi_table_schema(),
    }
    _generate_sql(state)

    prompt = captured["text"]
    # LEFT JOIN 优先 + 禁 RIGHT JOIN
    assert "LEFT JOIN" in prompt and "禁止使用 RIGHT JOIN" in prompt
    # 主表 = FROM 首表
    assert "FROM 后面的第一张表就是主表" in prompt
    # JOIN 条件写 ON、维度表过滤写 ON / 主表过滤写 WHERE
    assert "JOIN 关联条件必须写在 ON 子句里" in prompt
    assert "维度表的过滤条件写在 ON 里" in prompt
    # GROUP BY 完整性
    assert "GROUP BY 必须包含所有未聚合的查询列" in prompt
    # 超过 3 张表拆两层子查询
    assert "拆成两层子查询" in prompt
    # 明细默认 LIMIT 200
    assert "LIMIT 200" in prompt
    # 不臆造列
    assert "禁止臆造列" in prompt
    # v2：确认「附注释」规则已移除（不再要求 SQL 顶部注释）
    assert "说明关联逻辑" not in prompt


def test_generate_sql_prompt_injects_faq(monkeypatch) -> None:
    """Schema RAG：命中 FAQ 时把示例 SQL + 业务口径注入 prompt，并带「仅作参考」防御。"""
    from app.agent.sql_graph import _generate_sql
    captured = _capture_prompt(monkeypatch)
    monkeypatch.setattr(
        "app.agent.sql_graph.search_faq",
        lambda query, top_k=3: [{
            "question": "区域退货率",
            "sql": "SELECT rc.region_name, ROUND(SUM(rt.return_amount)/NULLIF(SUM(f.total_amount),0)*100,2) AS 退货率 FROM fact_returns rt JOIN fact_sales f ON rt.sale_id=f.sale_id JOIN dim_region rc ON f.region_id=rc.region_id GROUP BY rc.region_name",
            "note": "退货率 = 退货金额/销售额，经 sale_id 关联",
            "tables": ["fact_returns", "fact_sales"],
            "score": 6.0,
        }],
    )
    state = {
        **_minimal_state(),
        "user_query": "各区域退货率排名",
        "schema_context": _multi_table_schema(),
    }
    _generate_sql(state)

    prompt = captured["text"]
    assert "参考案例 1" in prompt
    assert "退货率 = 退货金额/销售额" in prompt
    assert "仅作参考" in prompt


def test_generate_sql_no_faq_when_no_match(monkeypatch) -> None:
    """Schema RAG：无命中时 prompt 不含 FAQ 块，主流程正常。"""
    from app.agent.sql_graph import _generate_sql
    captured = _capture_prompt(monkeypatch)
    monkeypatch.setattr("app.agent.sql_graph.search_faq", lambda query, top_k=3: [])
    state = {
        **_minimal_state(),
        "user_query": "完全无关的乱码查询",
        "schema_context": _multi_table_schema(),
    }
    _generate_sql(state)

    prompt = captured["text"]
    assert "参考案例" not in prompt


def test_generate_sql_faq_error_degrades(monkeypatch) -> None:
    """Schema RAG：search_faq 抛错时降级为无 FAQ，不影响 SQL 生成。"""
    from app.agent.sql_graph import _generate_sql
    captured = _capture_prompt(monkeypatch)

    def _boom(query, top_k=3):
        raise RuntimeError("faq unavailable")

    monkeypatch.setattr("app.agent.sql_graph.search_faq", _boom)
    state = {
        **_minimal_state(),
        "user_query": "各区域销售额排名",
        "schema_context": _multi_table_schema(),
    }
    result = _generate_sql(state)
    assert captured["calls"] == 1
    assert "参考案例" not in captured["text"]


def test_generate_sql_prompt_contains_time_split_rules(monkeypatch) -> None:
    """时间维度规则：date_id 关联 dim_date.full_date 区间过滤 + 左闭右开 +
    相对/绝对时间混用时子查询分算禁混写。"""
    from app.agent.sql_graph import _generate_sql
    captured = _capture_prompt(monkeypatch)
    state = {
        **_minimal_state(),
        "user_query": "对比 2024-01 与上月的华东销售",
        "schema_context": _multi_table_schema(),
    }
    _generate_sql(state)

    prompt = captured["text"]
    assert "date_id 外键关联 dim_date" in prompt
    assert "full_date" in prompt
    assert "左闭右开区间" in prompt
    assert "[start, end)" in prompt
    assert "两个带别名的子查询" in prompt
    assert "禁止在同一个 WHERE 里混写两种时间逻辑" in prompt
    # 明确提示 dim_date 无 month 列
    assert "dim_date 没有 month" in prompt


def test_generate_sql_prompt_contains_array_rule(monkeypatch) -> None:
    """数组类型规则：@> ARRAY['标签'] 而非 LIKE '%标签%'。"""
    from app.agent.sql_graph import _generate_sql
    captured = _capture_prompt(monkeypatch)
    state = {
        **_minimal_state(),
        "user_query": "按标签筛选",
        "schema_context": _multi_table_schema(),
    }
    _generate_sql(state)

    prompt = captured["text"]
    assert "@> ARRAY['标签']" in prompt
    assert "LIKE '%标签%'" in prompt
    assert "数组列恒为空" in prompt


def test_generate_sql_prompt_contains_fk_chain(monkeypatch) -> None:
    """v2 问题1：外键链路必须出现在真正写 JOIN 的 `_generate_sql` prompt 里
    （不只是 _plan / 常量）。通用断言：循环覆盖全部事实表的主外键映射。"""
    from app.agent.sql_graph import _generate_sql
    captured = _capture_prompt(monkeypatch)
    state = {
        **_minimal_state(),
        "user_query": "各区域各品类销售额",
        "schema_context": _multi_table_schema(),
    }
    _generate_sql(state)

    prompt = captured["text"]
    # 全部事实表 → 维度表外键映射都要在 _generate_sql 的 prompt 中
    expected_fk_pairs = [
        "fact_sales: date_id→dim_date",
        "region_id→dim_region",
        "product_id→dim_product",
        "customer_id→dim_customer",
        "fact_returns: return_date_id→dim_date",
        "sale_id→fact_sales",
        "warehouse_id→dim_warehouse",
        "employee_id→dim_employee",
    ]
    for pair in expected_fk_pairs:
        assert pair in prompt, f"外键映射缺失于 _generate_sql prompt: {pair}"


def test_generate_sql_prompt_includes_current_date(monkeypatch) -> None:
    """v2 问题2：相对时间换算需要基准——prompt 必须含「运行当天」的 ISO 日期。
    动态断言（date.today()），任何一天运行都成立，不硬编码具体日期。"""
    from datetime import date as _date
    from app.agent.sql_graph import _generate_sql
    captured = _capture_prompt(monkeypatch)
    state = {
        **_minimal_state(),
        "user_query": "今年的销售趋势",
        "schema_context": _multi_table_schema(),
    }
    _generate_sql(state)

    prompt = captured["text"]
    assert "当前日期:" in prompt
    assert _date.today().isoformat() in prompt


def test_plan_prompt_includes_current_date_and_fk(monkeypatch) -> None:
    """v2：`_plan` 也要有当前日期（判断 time_range 可推断性）+ 外键链路。"""
    from datetime import date as _date
    from app.agent.sql_graph import _plan
    captured = _capture_prompt(monkeypatch)  # _plan 以 str 形态调 call_llm，helper 兼容
    state = {
        "user_query": "今年华东销售趋势",
        "schema_context": _multi_table_schema(),
        "chosen_tool": None,
        "confirmed_requirement": None,
    }
    _plan(state)

    prompt = captured["text"]
    assert "当前日期:" in prompt
    assert _date.today().isoformat() in prompt
    assert "fact_sales: date_id→dim_date" in prompt  # _PLAN_TABLE_HINTS 含外键链路


def test_generate_sql_multi_join_schema_listed_in_prompt(monkeypatch) -> None:
    """多 JOIN 场景：5 张表全部进入 prompt 的「可用表结构」，不丢失任何表。"""
    from app.agent.sql_graph import _generate_sql
    captured = _capture_prompt(monkeypatch)
    state = {
        **_minimal_state(),
        "user_query": "各区域各品类销售额",
        "schema_context": _multi_table_schema(),
    }
    _generate_sql(state)

    prompt = captured["text"]
    for table in ("fact_sales", "dim_region", "dim_product",
                  "dim_customer", "dim_date"):
        assert f"表 {table}" in prompt
