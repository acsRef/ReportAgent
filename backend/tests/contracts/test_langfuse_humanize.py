"""Langfuse 上报的「人性化」与边界（plan 2026-11-08-trace-inspector）。

真机暴露的两件事：
1. UI 里一屏十几条 `report_agent_run`、节点名是 `confirmed_sql_agent`、一半 observation 是
   `xxx_output` 这种内部摘要——人能看懂才有排查价值；
2. 一轮 trace 几十个 observation 全带完整结果集时，云端 OTLP 上报 5s 超时**整批丢弃**。

这里钉住纯函数部分（不发网络）：命名、节点中文名、payload 截断。
"""
from __future__ import annotations

import pytest

from app.observability import langfuse_flush as f

pytestmark = pytest.mark.contracts


class TestRootName:
    def test_用用户原话当标题(self) -> None:
        tracer = type("T", (), {"user_query": "2023年各区域销售额排名"})()
        assert f._root_name(tracer) == "2023年各区域销售额排名"

    def test_过长截断(self) -> None:
        long_query = "把" * 100
        tracer = type("T", (), {"user_query": long_query})()
        name = f._root_name(tracer)

        assert name.endswith("…")
        assert len(name) == f._ROOT_QUESTION_CHARS + 1

    def test_换行压成空格(self) -> None:
        tracer = type("T", (), {"user_query": "第一行\n第二行"})()
        assert f._root_name(tracer) == "第一行 第二行"

    @pytest.mark.parametrize("query", [None, "", "   "])
    def test_没有原话回落默认名(self, query) -> None:
        tracer = type("T", (), {"user_query": query})()
        assert f._root_name(tracer) == "report_agent_run"


class TestNodeHumanName:
    @pytest.mark.parametrize(("node", "label"), [
        ("turn_understand", "理解意图"),
        ("generate_sql", "生成 SQL"),
        ("validate", "校验 SQL"),
        ("execute", "执行查询"),
        ("report_agent", "生成报告"),
    ])
    def test_已知节点出中文(self, node: str, label: str) -> None:
        human, kind = f._node_human(node)
        assert human == label
        assert kind

    def test_confirmed_前缀也能认(self) -> None:
        human, _kind = f._node_human("confirmed_sql_agent")
        assert human == "执行 SQL 分析"

    def test_未知节点原样返回(self) -> None:
        human, kind = f._node_human("some_internal_thing")
        assert human == "some_internal_thing"
        assert kind == ""


class TestNodeFilter:
    """Langfuse 按 observation 计费：只发人能看懂的阶段 + 失败节点（本地表仍全量）。"""

    @pytest.mark.parametrize("node", [
        "turn_understand", "requirement_parse", "requirement_project",
        "sql_plan", "sql_generate", "sql_validate", "sql_execute", "sql_evaluate",
        "sql_diagnose", "confirmed_sql_agent", "confirmed_report_agent", "report_build_output",
    ])
    def test_关键阶段要发(self, node: str) -> None:
        assert f._should_report(node, "SUCCESS", "essential") is True

    @pytest.mark.parametrize("node", [
        "security_guard", "confirmed_security_guard", "classify", "task_reduce",
        "reportability", "requirement_persist_draft", "sql_gate", "data_agent",
        "confirmed_idempotency_guard", "persist_report", "intent_analyze",
    ])
    def test_确定性内部步骤不发(self, node: str) -> None:
        assert f._should_report(node, "SUCCESS", "essential") is False

    def test_失败节点无条件发(self) -> None:
        """最容易漏、也最该看的是「平时不起眼、这次红了」的节点。"""
        assert f._should_report("security_guard", "FAILED", "essential") is True

    def test_full_档全发(self) -> None:
        assert f._should_report("security_guard", "SUCCESS", "full") is True

    def test_未知档位按_essential_处理(self) -> None:
        assert f._should_report("security_guard", "SUCCESS", "weird") is False


class TestBoundedPayload:
    def test_小对象原样(self) -> None:
        assert f._bounded({"a": 1}) == {"a": 1}

    def test_字符串直接返回(self) -> None:
        assert f._bounded("SELECT 1") == "SELECT 1"

    def test_大对象截断并标注长度(self) -> None:
        value = "x" * (f._OBS_PAYLOAD_CHARS + 500)
        result = f._bounded(value)

        assert isinstance(result, str)
        assert result.startswith("x" * 100)
        assert "截断" in result
        assert str(f._OBS_PAYLOAD_CHARS + 500) in result

    def test_None_透传(self) -> None:
        assert f._bounded(None) is None

    def test_pii_仍被脱敏(self) -> None:
        result = f._bounded({"phone": "13800138000"})
        assert "13800138000" not in str(result)
