"""ReportSpec 域契约（P10，伞形 §十二 / report-runtime.md §一·§四）。

`agent/report_graph.py` = Agent 怎么决定报告结构；本模块 = 报告 Domain Object
怎么定义。数据真实性原则：所有数值、排名、统计、图表数据必须来自实际
Query Result——provenance 由 ``DataBinding``/``KpiSpec.field`` 显式锚定，
由 ``report.validator`` 三层校验钉住。

命名约定（P10-2 澄清）：「v2」指 **schema 契约版本**（P10 扩展 kpi/table/data_binding）；
payload 的 ``version`` 字段是**域版本**、保持 ``"1.0"`` 不动——两回事，勿混。

向后兼容：v2 新增 kpi/table/data_binding 结构化字段均有缺省值，旧落库
payload 形状仍可 parse；``app.models.contracts`` 为 re-export shim。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


class KpiSpec(BaseModel):
    """KPI 数值块——value 必须等于 aggregation(field) 重算（validator 数值层）。

    Final Hardening ③：value 用 Decimal——PostgreSQL numeric 语义精确到 decimal，
    用 float 承载会丢精度（大额 SUM / 高 scale 场景）。pydantic JSON 模式把
    Decimal 序列化为精确字符串（"123456789012345678.91"），与 rows 的
    numeric 字符串表示同构。
    """

    label: str
    field: str  # QueryResult 列名（来源锚点）
    aggregation: Literal["sum", "avg", "min", "max", "count"] = "sum"
    value: Optional[Decimal] = None


class TableSpec(BaseModel):
    """表格块——columns ⊆ QueryResult 列名；行数据不进 schema（fabrication 封死）。"""

    columns: list[str] = Field(default_factory=list)


class DataBinding(BaseModel):
    """数据来源锚点——source 唯一合法值 query_result（禁止自由来源）。"""

    source: Literal["query_result"] = "query_result"
    fields: list[str] = Field(default_factory=list)  # chart 维度/值字段（⊆ columns）
    rows: list[dict] = Field(default_factory=list)  # 每行必须可投影自 QueryResult.rows


class ComponentSpec(BaseModel):
    id: str = ""
    type: str = ""
    title: str = ""
    layout: dict = {}
    data_binding: DataBinding = Field(default_factory=DataBinding)
    visual_config: dict = {}


class ReportSpec(BaseModel):
    version: str = "1.0"
    components: list[ComponentSpec] = []
    insight: str = ""
    kpi: list[KpiSpec] = []
    table: Optional[TableSpec] = None
