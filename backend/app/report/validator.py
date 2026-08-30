"""三层 ReportSpec Validator（P10，伞形 §十二 / report-runtime.md §二）。

校验对象是 **ReportSpec → QueryResult 映射**（不是渲染产物）：
1. structure  —— chart/table/kpi 字段必须存在于 QueryResult 列。
2. numeric    —— kpi.value 必须等于 aggregation 重算；data_binding.rows 值变形即违规。
3. fabrication—— 行必须可投影自 QueryResult.rows（不存在 / 膨胀即违规）。

明确不做文本（insight/HTML）正则数字审计——伞形 §十二：`12345 / 12,345 / 1.23万`
会大量误报。EMPTY / qr=None 短路 ok=True：三态中只有 SUCCESS 进三层。
"""
from __future__ import annotations

import math
from typing import Literal, Optional

from pydantic import BaseModel

from app.models.contracts import QueryResult
from app.report.spec import ReportSpec

_ViolationLayer = Literal["structure", "numeric", "fabrication"]

_REL_TOL = 1e-9


class SpecViolation(BaseModel):
    layer: _ViolationLayer
    block: str  # "kpi[0]" / "table" / "components[0]" / "components"
    field: str = ""
    detail: str = ""


class SpecValidationResult(BaseModel):
    ok: bool
    violations: list[SpecViolation] = []


def _aggregate(values: list, aggregation: str) -> Optional[float]:
    """对列值做聚合重算；非数值列（count 除外）返回 None。"""
    if aggregation == "count":
        return float(len(values))
    numeric = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not values or len(numeric) != len(values):
        return None
    if aggregation == "sum":
        return float(sum(numeric))
    if aggregation == "avg":
        return float(sum(numeric)) / len(numeric)
    if aggregation == "min":
        return float(min(numeric))
    return float(max(numeric))


def _row_matches(row: dict, qr_rows: list[dict]) -> bool:
    """行是否可投影自某 QueryResult 行（逐值子集，允许只取锚定字段）。"""
    for qr_row in qr_rows:
        if all(k in qr_row and qr_row[k] == v for k, v in row.items()):
            return True
    return False


def validate_report_spec(spec, query_result) -> SpecValidationResult:
    """三层校验单一入口。spec / query_result 接受模型或 dict（state 透传归一）。"""
    if not isinstance(spec, ReportSpec):
        spec = ReportSpec.model_validate(spec)
    if query_result is None:
        return SpecValidationResult(ok=True)
    qr = query_result if isinstance(query_result, QueryResult) else QueryResult.model_validate(query_result)
    if not qr.rows:
        # EMPTY 合法零行不进三层（三态语义：只有 SUCCESS 声称有数据）。
        return SpecValidationResult(ok=True)

    columns = {
        c.get("name", "") if isinstance(c, dict) else str(c)
        for c in qr.columns
    }
    violations: list[SpecViolation] = []

    # --- 1. structure：字段锚点存在性 ----------------------------------------
    for i, kpi in enumerate(spec.kpi):
        if kpi.field not in columns:
            violations.append(SpecViolation(
                layer="structure", block=f"kpi[{i}]", field=kpi.field,
                detail=f"KPI 字段 {kpi.field} 不在 QueryResult 列中",
            ))
    if spec.table is not None:
        if not spec.table.columns:
            violations.append(SpecViolation(
                layer="structure", block="table",
                detail="table 块未声明任何列",
            ))
        for col in spec.table.columns:
            if col not in columns:
                violations.append(SpecViolation(
                    layer="structure", block="table", field=col,
                    detail=f"table 列 {col} 不在 QueryResult 列中",
                ))
    for i, comp in enumerate(spec.components):
        if not comp.data_binding.fields:
            violations.append(SpecViolation(
                layer="structure", block=f"components[{i}]",
                detail="data-bearing 组件未声明字段锚点",
            ))
        for f in comp.data_binding.fields:
            if f not in columns:
                violations.append(SpecViolation(
                    layer="structure", block=f"components[{i}]", field=f,
                    detail=f"组件字段 {f} 不在 QueryResult 列中",
                ))

    # --- 2. numeric：KPI 数值必须等于聚合重算 --------------------------------
    for i, kpi in enumerate(spec.kpi):
        if kpi.field not in columns:
            continue  # 结构层已报，数值层不再重复噪声
        if kpi.value is None:
            violations.append(SpecViolation(
                layer="numeric", block=f"kpi[{i}]", field=kpi.field,
                detail="KPI 声明了字段但未填重算值",
            ))
            continue
        expected = _aggregate([r.get(kpi.field) for r in qr.rows], kpi.aggregation)
        if expected is None:
            violations.append(SpecViolation(
                layer="numeric", block=f"kpi[{i}]", field=kpi.field,
                detail=f"列 {kpi.field} 含非数值，无法 {kpi.aggregation} 聚合",
            ))
            continue
        if not math.isclose(float(kpi.value), expected, rel_tol=_REL_TOL, abs_tol=_REL_TOL):
            violations.append(SpecViolation(
                layer="numeric", block=f"kpi[{i}]", field=kpi.field,
                detail=f"{kpi.aggregation}={kpi.value} 与 QueryResult 重算值 {expected} 不符",
            ))

    # --- 3. fabrication：行必须可投影自 QueryResult.rows ---------------------
    # 维度值编造与数值变形在 DataBinding（无字段语义）下不可区分，且本质相同：
    # 渲染出的行数据不存在于 QueryResult —— 统一 fabrication（「不生成不存在的数据」）。
    bound_row_count = 0
    for i, comp in enumerate(spec.components):
        for row in comp.data_binding.rows:
            bound_row_count += 1
            if not _row_matches(row, qr.rows):
                violations.append(SpecViolation(
                    layer="fabrication", block=f"components[{i}]",
                    detail=f"行在 QueryResult 中不存在（自由生成）: {row}",
                ))
    if bound_row_count > len(qr.rows):
        violations.append(SpecViolation(
            layer="fabrication", block="components",
            detail=f"绑定行数 {bound_row_count} 超出 QueryResult 行数 {len(qr.rows)}（复制膨胀）",
        ))

    return SpecValidationResult(ok=not violations, violations=violations)
