from __future__ import annotations

import json
import statistics
from decimal import Decimal

from app.utils.numbers import is_numeric_value, to_decimal


def _numeric_keys(row: dict) -> list:
    """数值列识别：int/float/Decimal/数值字符串都算（numeric 列 JSON 后是 str）。"""
    return [k for k, v in row.items() if is_numeric_value(v)]


def trend_analysis(data_json: str) -> str:
    """判断时间序列数据的整体趋势方向（上升/下降/平稳）及变化幅度。
    用途：数据包含按时间排序的数值列时，自动判断是涨了还是跌了。
    输入：data_json（execute_sql 返回的 JSON），会自动取第一个数值列
    输出：趋势文本，如 "整体呈上升趋势，后半段增长 23.5%"
    判断方式：数据切半，比较前后半段均值。
      后半段均值 > 前半段 110% → 上升趋势
      前半段均值 > 后半段 110% → 下降趋势
      否则 → 平稳
    限制：数据少于 2 行时无法分析。不做分组对比（用 group_compare）。不做异常检测（用 detect_anomaly）。"""
    data = json.loads(data_json)
    rows = data.get("rows", [])
    if len(rows) < 2:
        return "数据量不足，无法进行趋势分析"

    first = rows[0]
    numeric_keys = _numeric_keys(first)
    if not numeric_keys:
        return "没有数值列，无法分析趋势"

    val_col = numeric_keys[0]
    values = [to_decimal(r[val_col]) for r in rows if r.get(val_col) is not None]
    values = [v for v in values if v is not None]
    if len(values) >= 2:
        half = len(values) // 2
        first_avg = sum(values[:half], Decimal(0)) / half
        second_avg = sum(values[half:], Decimal(0)) / (len(values) - half)
        if second_avg > first_avg * Decimal("1.1"):
            return f"整体呈上升趋势，后半段增长 {((second_avg / first_avg) - 1) * 100:.1f}%"
        elif first_avg > second_avg * Decimal("1.1"):
            return f"整体呈下降趋势，后半段下降 {((first_avg / second_avg) - 1) * 100:.1f}%"
        else:
            return "整体趋势平稳"
    return "趋势分析完成"


def group_compare(data_json: str, group_col: str = "", value_col: str = "") -> str:
    """按指定维度分组，汇总数值合计，对比哪个组表现最好/最差。
    用途：横向对比不同区域/产品/客户等维度的数值差异，输出排名。
    输入：
      data_json（execute_sql 返回的 JSON）
      group_col（分组字段，可选。不指定时自动选第一个非数值列）
      value_col（数值字段，可选。不指定时自动选第一个数值列）
    输出：多行文本，每行 "分组名: 合计=数值"，按合计降序排列
    示例输入：
      group_col='region', value_col='order_amount'
      → 返回 "华东: 合计=1,234,567.00\n华南: 合计=987,654.00"
    不要用来：不执行 SQL 查询。不需要分组对比时不用。需要图表可视化时用 chart_advisor。"""
    data = json.loads(data_json)
    rows = data.get("rows", [])
    if not rows:
        return "无数据"

    first = rows[0]
    if not group_col or group_col not in first:
        cat_keys = [k for k in first if not is_numeric_value(first[k])]
        group_col = cat_keys[0] if cat_keys else list(first.keys())[0]
    if not value_col or value_col not in first:
        num_keys = _numeric_keys(first)
        value_col = num_keys[0] if num_keys else list(first.keys())[-1]

    groups: dict[str, list[Decimal]] = {}
    for r in rows:
        g = str(r.get(group_col, "未知"))
        v = to_decimal(r.get(value_col))
        groups.setdefault(g, []).append(v if v is not None else Decimal(0))

    summary = [
        f"{g}: 合计={sum(vals, Decimal(0)):,.2f}"
        for g, vals in sorted(groups.items(), key=lambda x: sum(x[1]), reverse=True)
    ]
    return "\n".join(summary)


def detect_anomaly(data_json: str, value_col: str = "") -> str:
    """用标准差方法检测数据中的异常高/低值（偏离均值超过 2 倍标准差）。
    用途：数据质量检查，找"哪个数据点不正常"。
    输入：
      data_json（execute_sql 返回的 JSON）
      value_col（数值字段名，可选。不指定时自动选第一个数值列）
    输出：异常值列表文本，如 "发现 2 个异常值: 华东: 1,234.56; 华南: 987.65"
        未发现异常时返回 "未发现明显异常值"
    限制：
      - 数据至少需要 3 行才能计算标准差
      - 只检测单个数值列的异常，不跨列关联
      - 不做趋势分析（用 trend_analysis）。不做分组对比（用 group_compare）。"""
    data = json.loads(data_json)
    rows = data.get("rows", [])
    if not rows:
        return "无数据"

    first = rows[0]
    if not value_col or value_col not in first:
        num_keys = _numeric_keys(first)
        value_col = num_keys[0] if num_keys else ""
    if not value_col:
        return "没有数值列"

    values = [to_decimal(r[value_col]) for r in rows if r.get(value_col) is not None]
    values = [v for v in values if v is not None]
    if len(values) < 3:
        return "数据量不足"

    try:
        # 统计口径用 float（Decimal 的 stdev 语义复杂度不值当）；仅在此处转换，
        # 展示格式 {:,.2f} 仍基于 Decimal 原值，避免摘要级精度损失。
        flts = [float(v) for v in values]
        mean = sum(flts) / len(flts)
        stdev = statistics.stdev(flts)
        threshold = 2 * stdev
        anomalies = []
        for r in rows:
            v = to_decimal(r.get(value_col))
            if v is None:
                continue
            if abs(float(v) - mean) > threshold:
                cat_keys = [k for k in first if not is_numeric_value(first[k])]
                label = str(r.get(cat_keys[0], "")) if cat_keys else ""
                anomalies.append(f"{label}: {v:,.2f}")
        if anomalies:
            return f"发现 {len(anomalies)} 个异常值: " + "; ".join(anomalies[:5])
        return "未发现明显异常值"
    except statistics.StatisticsError:
        return "无法计算标准差"
