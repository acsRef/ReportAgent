from __future__ import annotations

import json
import statistics


def trend_analysis(data_json: str) -> str:
    """分析数据趋势。"""
    data = json.loads(data_json)
    rows = data.get("rows", [])
    if len(rows) < 2:
        return "数据量不足，无法进行趋势分析"

    first = rows[0]
    numeric_keys = [k for k, v in first.items() if isinstance(v, (int, float))]
    if not numeric_keys:
        return "没有数值列，无法分析趋势"

    val_col = numeric_keys[0]
    values = [r[val_col] for r in rows if r.get(val_col) is not None]
    if len(values) >= 2:
        half = len(values) // 2
        first_avg = sum(values[:half]) / half
        second_avg = sum(values[half:]) / (len(values) - half)
        if second_avg > first_avg * 1.1:
            return f"整体呈上升趋势，后半段增长 {((second_avg / first_avg) - 1) * 100:.1f}%"
        elif first_avg > second_avg * 1.1:
            return f"整体呈下降趋势，后半段下降 {((first_avg / second_avg) - 1) * 100:.1f}%"
        else:
            return "整体趋势平稳"
    return "趋势分析完成"


def group_compare(data_json: str, group_col: str = "", value_col: str = "") -> str:
    """按维度分组对比。"""
    data = json.loads(data_json)
    rows = data.get("rows", [])
    if not rows:
        return "无数据"

    first = rows[0]
    if not group_col or group_col not in first:
        cat_keys = [k for k in first if not isinstance(first[k], (int, float))]
        group_col = cat_keys[0] if cat_keys else list(first.keys())[0]
    if not value_col or value_col not in first:
        num_keys = [k for k in first if isinstance(first[k], (int, float))]
        value_col = num_keys[0] if num_keys else list(first.keys())[-1]

    groups: dict[str, list[float]] = {}
    for r in rows:
        g = str(r.get(group_col, "未知"))
        v = r.get(value_col, 0) or 0
        groups.setdefault(g, []).append(float(v))

    summary = [
        f"{g}: 合计={sum(vals):,.2f}"
        for g, vals in sorted(groups.items(), key=lambda x: sum(x[1]), reverse=True)
    ]
    return "\n".join(summary)


def detect_anomaly(data_json: str, value_col: str = "") -> str:
    """检测数据中的异常值。"""
    data = json.loads(data_json)
    rows = data.get("rows", [])
    if not rows:
        return "无数据"

    first = rows[0]
    if not value_col or value_col not in first:
        num_keys = [k for k in first if isinstance(first[k], (int, float))]
        value_col = num_keys[0] if num_keys else ""
    if not value_col:
        return "没有数值列"

    values = [r[value_col] for r in rows if r.get(value_col) is not None]
    if len(values) < 3:
        return "数据量不足"

    try:
        mean = statistics.mean(values)
        stdev = statistics.stdev(values)
        threshold = 2 * stdev
        anomalies = []
        for r in rows:
            v = r.get(value_col, 0) or 0
            if abs(v - mean) > threshold:
                cat_keys = [k for k in first if not isinstance(first[k], (int, float))]
                label = str(r.get(cat_keys[0], "")) if cat_keys else ""
                anomalies.append(f"{label}: {v:,.2f}")
        if anomalies:
            return f"发现 {len(anomalies)} 个异常值: " + "; ".join(anomalies[:5])
        return "未发现明显异常值"
    except statistics.StatisticsError:
        return "无法计算标准差"
