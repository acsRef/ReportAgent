"""真机失败归因修复（2026-11-08）。

两个缺陷，各自一个独立用例文件（不跑整套）：

1. **scope 值没归一**：用户说「华南区域的吧」→ 卡上 `scope=['华南区域']`，而数据里是
   `华南` → 选期查询 0 行 → 上游把 SQL 修成语法错、repair 空转 → 最后报错。
   `knowledge.canonical_scopes` 只做**确定性**后缀裁剪 + 精确匹配，归不了原样保留。
2. **失败文案为空**：UI 上出现「SQL执行失败: 」（冒号后面什么都没有）。
   `sql_graph._failure_detail` 从校验结论 / 执行错误 / 原始返回里按序取，最后有兜底。
"""
from __future__ import annotations

import pytest

from app.agent.sql_graph import _failure_detail
from app.knowledge import registry as knowledge

pytestmark = pytest.mark.contracts


class TestCanonicalScopes:
    def test_口语后缀被裁掉(self) -> None:
        assert knowledge.canonical_scopes(["华南区域"]) == ["华南"]
        assert knowledge.canonical_scopes(["华东地区"]) == ["华东"]
        assert knowledge.canonical_scopes(["华北大区"]) == ["华北"]

    def test_真实值原样保留(self) -> None:
        assert knowledge.canonical_scopes(["华南", "华东"]) == ["华南", "华东"]

    def test_未知值不猜(self) -> None:
        # 数据里没有的取值 → 原样返回（需求层不编数据）
        assert knowledge.canonical_scopes(["火星区域"]) == ["火星区域"]
        assert knowledge.canonical_scopes(["东北"]) == ["东北"]

    def test_空值与重复被清掉(self) -> None:
        assert knowledge.canonical_scopes([]) == []
        assert knowledge.canonical_scopes(None) == []
        assert knowledge.canonical_scopes(["华南", "华南区域", "  "]) == ["华南"]

    def test_全部区域占位值不被改写(self) -> None:
        assert knowledge.canonical_scopes(["ALL"]) == ["ALL"]

    def test_单值归一接口(self) -> None:
        assert knowledge.canonical_dimension_value("区域", "华南区域") == "华南"
        assert knowledge.canonical_dimension_value("区域", "华南") == "华南"
        # 没有登记的维度 → 原样返回
        assert knowledge.canonical_dimension_value("城市", "深圳市") == "深圳市"


class TestFailureDetailNeverEmpty:
    def test_校验失败的结论优先(self) -> None:
        detail = _failure_detail(
            kind="syntax",
            validation={"valid": False, "error": "缺少「只取 2023年11月」的过滤条件"},
            parsed_raw=None,
            raw="",
        )
        assert detail.message == "缺少「只取 2023年11月」的过滤条件"
        assert detail.kind == "syntax"

    def test_执行错误其次(self) -> None:
        detail = _failure_detail(
            kind="object_not_found",
            validation={},
            parsed_raw={"error": 'relation "fact_orders2" does not exist'},
            raw='{"error": "..."}',
        )
        assert "does not exist" in detail.message

    def test_原始返回再次之(self) -> None:
        detail = _failure_detail(kind="other", validation={}, parsed_raw=None, raw="  boom  ")
        assert detail.message == "boom"

    def test_什么都没有时给兜底而不是空串(self) -> None:
        # 真机就是这个形态：预算耗尽、没校验结论也没执行结果 → 之前是「SQL执行失败: 」
        detail = _failure_detail(kind="syntax", validation={}, parsed_raw=None, raw="")
        assert detail.message.strip() != ""
        assert "SQL执行失败: " not in detail.message
        assert "未通过校验或执行失败" in detail.message
