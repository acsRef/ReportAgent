"""数值精确性工具（Final Hardening ③）。

背景：PostgreSQL numeric 经 psycopg2 返回为 Decimal；JSON transport 一律用
字符串保 exact（int 保持 JSON number）。因此 QueryResult.rows / ReportSpec 里
的数值可能是 int / float（double 列）/ str（numeric 列的精确十进制字符串）/
Decimal（序列化前）——任何对数值做识别或计算的下游（chart 分类、统计摘要、
分组汇总、KPI 重算）必须经本模块收敛，禁止裸 isinstance((int, float)) 判断
（会把 numeric 字符串误判为非数值），也禁止无条件 float()（丢精度）。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional


def to_decimal(v: Any) -> Optional[Decimal]:
    """把数值 / 数值字符串收敛为 Decimal；非数值（None / bool / 文本）返回 None。

    bool 是 int 子类但语义非数值（KPI 计数列除外），显式排除。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float, str)):
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError):
            return None
    return None


def is_numeric_value(v: Any) -> bool:
    """兼容数值识别：int / float / Decimal / 可解析的数值字符串 → True。

    取代 `isinstance(v, (int, float))` 裸判断（对 numeric 字符串会漏判，
    导致 chart/insight 把金额列当非数值列）。
    """
    return to_decimal(v) is not None
