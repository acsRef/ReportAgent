"""报告输出的显示层：列名中文化 + 重复列去掉（plan 2026-11-04-requirement-slot-contract 收尾）。

## 问题

报告表格/图表的表头此前**直通 SQL 别名**。模型偶尔会把同一列输出两次、且第二次没有
中文别名——实测：

```sql
SELECT dim_date.week_of_year AS 周, dim_product.category AS 品类,
       dim_date.week_of_year AS week_of_year,        -- 重复列 + 漏写中文别名
       SUM(fact_orders.order_amount) AS 销售额 ...
```

界面上于是出现一列 `week_of_year`（与「周」完全重复），图表轴上也可能是物理列名。
用户看到的就是「怎么还有英文」。

## 做两件确定性的事（都不碰 SQL / 不碰 qr）

1. **中文化**：`title` 走注册表映射（`knowledge.display_label`），`key` 保持原样
   （前端按 key 取数，不能动）；
2. **去重**：整列取值完全相同的列只保留第一个（SQL 里 `AS 周` 与 `AS week_of_year`
   是同一个表达式，值序列必然相同）。

数据全部来自 `metrics.yaml` / `schema_hints.yaml`——换 schema 只改 YAML。
"""
from __future__ import annotations

from app.knowledge import registry as knowledge


def column_label(name: str) -> str:
    """列名 → 展示标题（认不出就原样返回）。"""
    return knowledge.display_label(name) or str(name)


def drop_duplicate_columns(columns: list[str], rows: list[dict]) -> list[str]:
    """去掉「整列取值完全相同」的重复列（保持首次出现的顺序）。

    不按列名判重：模型给同一表达式起了两个别名（`AS 周` / `AS week_of_year`），
    名字不同但数据一模一样——这正是实测里多出来的那一列。
    仅当还有其它列可展示时才删（单列结果不能删空）。
    """
    names = [str(c) for c in columns]
    if len(names) <= 1 or not rows:
        return names
    seen: set[tuple] = set()
    kept: list[str] = []
    for name in names:
        signature = tuple(str(row.get(name)) for row in rows)
        if signature in seen:
            continue
        seen.add(signature)
        kept.append(name)
    return kept or names


def build_display_table(
    columns: list[str], rows: list[dict],
) -> tuple[dict, dict[str, str]]:
    """构造 `answer.table`（key 原样 / title 中文），并返回**全量** key→label 映射。

    返回的映射供图表同步改名用：图表的轴名来自行键，不一起改就会出现
    「表格中文、图轴英文」。**被去掉的重复列也进映射**（指向同伴的中文标签）——
    否则图表行里会残留一个 `week_of_year` 键，轴名/系列名又露英文。
    """
    names = [str(c) for c in columns]
    kept = drop_duplicate_columns(names, rows)
    label_map = {name: column_label(name) for name in kept}

    # 重复列（值序列与某个保留列完全相同）→ 复用保留列的中文标签
    signatures: dict[tuple, str] = {}
    for name in kept:
        signatures.setdefault(tuple(str(row.get(name)) for row in rows), name)
    for name in names:
        if name in label_map:
            continue
        twin = signatures.get(tuple(str(row.get(name)) for row in rows))
        label_map[name] = label_map.get(twin, column_label(name)) if twin else column_label(name)

    table = {
        "columns": [{"key": name, "title": label_map[name]} for name in kept],
        "rows": rows,
    }
    return table, label_map


def localize_chart(chart: dict | None, label_map: dict[str, str]) -> dict | None:
    """把图表配置里的列键换成中文标签（行键、dimensions、xField/yField 都换）。

    图表是「按行键取值」渲染的：只改表格标题会让轴的刻度名还是 `week_of_year`。
    这里一次性把 data 行键与字段声明改成同一套中文名，前端无需改动。
    """
    if not isinstance(chart, dict) or not label_map:
        return chart

    def _rename(name: object) -> object:
        return label_map.get(str(name), name) if name is not None else name

    config = chart.get("config")
    if isinstance(config, dict):
        config = dict(config)

        data = config.get("data") or config.get("dataset")
        if isinstance(data, list):
            renamed_rows: list[dict] = []
            for row in data:
                if not isinstance(row, dict):
                    renamed_rows.append(row)
                    continue
                new_row: dict = {}
                for key, value in row.items():
                    new_row[_rename(key)] = value
                renamed_rows.append(new_row)
            if "data" in config:
                config["data"] = renamed_rows
            if "dataset" in config:
                config["dataset"] = renamed_rows

        dimensions = config.get("dimensions")
        if isinstance(dimensions, dict):
            config["dimensions"] = {k: _rename(v) for k, v in dimensions.items()}
        for field in ("xField", "yField", "x", "y"):
            if field in config:
                config[field] = _rename(config[field])

    localized = dict(chart)
    localized["config"] = config if isinstance(config, dict) else chart.get("config")
    return localized
