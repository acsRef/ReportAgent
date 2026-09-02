"""sql dim harness——P14 阶段即实装：复用 execution 子段 sql/rows_gt 判定。

D2 边界：本子包是「exp schema 已定义 + observation 可用」——返回真实 sections，
通过 dispatcher 写入到 `sql.<key>` section。在 dim_results[sql] 显示 pass/fail，
不计入 deferred。

注：legacy execution 段对相同字段写 `execution.<key>` section——两套并存但 prefix
不同（sql.* vs execution.*），互不重复。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("sql")
def assert_sql(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    sections: dict[str, str] = {}
    deferred: list[str] = []
    # sql_nonempty
    if exp.get("sql_nonempty") is True:
        sections["sql_nonempty"] = (
            "pass" if bool(obs.sql and obs.sql.strip()) else "fail"
        )
    # rows_gt
    if exp.get("rows_gt") is not None:
        rc = obs.row_count
        sections["rows_gt"] = "pass" if rc is not None and rc > exp["rows_gt"] else "fail"
    # verdict
    if exp.get("verdict") is not None:
        sections["verdict"] = (
            "pass" if (
                (exp["verdict"] == "FAILED" and obs.error_code)
                or (exp["verdict"] == "EMPTY" and (obs.row_count == 0))
                or (exp["verdict"] == "SUCCESS" and (obs.row_count and obs.row_count > 0))
            ) else "fail"
        )
    return sections, deferred
