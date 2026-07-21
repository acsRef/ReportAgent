from app.tools.registry import registry, ToolMetadata
from app.tools import data_tools, sql_tools, report_tools


def register_all_tools():
    # =========================================
    # Data Agent tools — schema discovery
    # =========================================

    registry.register(
        "search_tables", data_tools.search_tables,
        ToolMetadata(
            name="search_tables",
            description=(
                "根据自然语言查询语义搜索相关数据库表，返回表名、字段结构和匹配分。\n\n"
                "适用场景：\n"
                "- 首次分析，不清楚哪些表包含所需数据\n"
                "- 用户问题中提到了业务概念（如\"销售额\"\"退货率\"），需要映射到物理表\n\n"
                "禁止场景：\n"
                "- 已确认目标表后不再需要调用\n"
                "- 不查询业务数据，只做元数据发现\n\n"
                "输入：query（自然语言描述），top_k（返回数量，默认3）\n\n"
                "输出：JSON 数组，每条包含 table_name, columns[{name,type}], ddl, description\n\n"
                "失败：无匹配时返回默认 Top 表（按优先级排序）"
            ),
            capability="schema_search",
            agent_type="data",
            risk_level="low",
            input_schema={"query": "string", "top_k": "int"},
            output_schema={"tables": "array"},
        ),
    )

    registry.register(
        "get_table_ddl", data_tools.get_table_ddl,
        ToolMetadata(
            name="get_table_ddl",
            description=(
                "获取指定表的完整 CREATE TABLE DDL，含全部字段名和类型。\n\n"
                "适用场景：\n"
                "- 已定位目标表，需要查看精确字段名和类型用于写 SQL\n"
                "- 在 SQL Agent 生成 SQL 前确认字段是否存在\n\n"
                "禁止场景：\n"
                "- 不需要 DDL 时不要调用（直接写 SQL 即可）\n"
                "- 不返回业务数据行\n\n"
                "输入：table_name（表名字符串）\n\n"
                "输出：CREATE TABLE 格式的 DDL 文本\n\n"
                "失败：表不存在时返回 \"Table 'xxx' not found\""
            ),
            capability="schema_read",
            agent_type="data",
            risk_level="low",
            input_schema={"table_name": "string"},
            output_schema={"type": "string"},
        ),
    )

    registry.register(
        "list_tables", data_tools.list_tables,
        ToolMetadata(
            name="list_tables",
            description=(
                "列出数据库中所有可用表及其简要描述和字段数量。\n\n"
                "适用场景：\n"
                "- 完全不熟悉数据库结构时，先总览有哪些表\n"
                "- search_tables 未返回理想结果时作为兜底\n\n"
                "禁止场景：\n"
                "- 已明确目标表后不需要重复调\n\n"
                "输入：无\n\n"
                "输出：JSON 数组，每条包含 table_name, description, column_count"
            ),
            capability="schema_list",
            agent_type="data",
            risk_level="low",
            output_schema={"tables": "array"},
        ),
    )

    # =========================================
    # SQL Agent tools — validate & execute
    # =========================================

    registry.register(
        "validate_sql", sql_tools.validate_sql,
        ToolMetadata(
            name="validate_sql",
            description=(
                "验证 SQL 语句的语法正确性和安全性，不实际执行查询。\n\n"
                "适用场景：\n"
                "- 每次 execute_sql 之前必须调用\n"
                "- SQL Agent 重试生成新 SQL 后需要重新校验\n\n"
                "校验内容：\n"
                "1. 黑名单检查：禁止 DDL/DML 关键字（INSERT/UPDATE/DELETE/DROP 等）\n"
                "2. AST 解析：必须是标准 SELECT 语句\n"
                "3. EXPLAIN 执行：用 DuckDB 的 EXPLAIN 捕获语法错误\n\n"
                "禁止场景：\n"
                "- 不要用它执行 SQL，它只校验不查询\n\n"
                "输入：sql（待校验的 SQL 文本）\n\n"
                "输出：{\"valid\": bool, \"error\": string}\n\n"
                "失败：校验不通过时 error 包含具体原因"
            ),
            capability="sql_validate",
            agent_type="sql",
            risk_level="medium",
            input_schema={"sql": "string"},
            output_schema={"valid": "bool", "error": "string"},
        ),
    )

    registry.register(
        "execute_sql", sql_tools.execute_sql,
        ToolMetadata(
            name="execute_sql",
            description=(
                "执行只读 SELECT 查询，返回结构化查询结果。\n\n"
                "重要限制：\n"
                "- 必须先通过 validate_sql 校验且返回 valid=true\n"
                "- 只允许 SELECT 语句，禁止任何 DDL/DML\n"
                "- 连接的是只读 DuckDB 副本，无法修改数据\n\n"
                "适用场景：\n"
                "- validate_sql 校验通过后立即执行\n"
                "- 获取查询结果用于后续报表生成\n\n"
                "输入：sql（合法的 SELECT 语句，字段名必须引用已确认的表结构）\n\n"
                "输出：{\"columns\": [{name, type}], \"rows\": [dict], \"error\": string}\n"
                "- columns：列名和类型的数组\n"
                "- rows：行数据数组，每行为 {字段名: 值}\n"
                "- error：为空表示成功，有值表示执行失败\n\n"
                "错误处理：\n"
                "- 执行失败时返回 error 字段，SQL Agent 根据错误信息修正后重试\n"
                "- 重试 3 次仍未通过则转 clarify 节点"
            ),
            capability="sql_execute",
            agent_type="sql",
            risk_level="high",
            permission_required=["data.query.execute"],
            input_schema={"sql": "string"},
            output_schema={"columns": "array", "rows": "array", "error": "string"},
        ),
    )

    # =========================================
    # Report Agent tools — visualization & insight
    # =========================================

    registry.register(
        "chart_advisor", sql_tools.chart_advisor,
        ToolMetadata(
            name="chart_advisor",
            description=(
                "根据 SQL 查询结果推荐最佳图表类型和配置。\n\n"
                "适用场景：\n"
                "- SQL 执行成功并返回数据后\n"
                "- report_agent 组装看板时决定可视化方式\n\n"
                "判断规则：\n"
                "- 1 个分类维度 + 1 个数值维度，且行数 ≤ 8 → 饼图\n"
                "- 1 个分类维度 + 1 个数值维度，且行数 > 8 → 柱状图\n"
                "- 无合适维度 → 纯表格\n\n"
                "禁止场景：\n"
                "- 不查询数据库\n"
                "- 不修改数据\n\n"
                "输入：SQL 查询结果 JSON（含 columns 和 rows）\n\n"
                "输出：{\"type\": \"pie\"|\"bar\"|\"table\", \"config\": {data, dimensions}}"
            ),
            capability="chart_recommend",
            agent_type="report",
            risk_level="medium",
            input_schema={"sql_result": "string"},
            output_schema={"type": "string", "config": "object"},
        ),
    )

    registry.register(
        "insight_analyst", sql_tools.insight_analyst,
        ToolMetadata(
            name="insight_analyst",
            description=(
                "分析 SQL 查询结果的数值列，生成汇总统计洞察。\n\n"
                "适用场景：\n"
                "- SQL 执行成功并返回数据后\n"
                "- 需要告诉用户\"数据怎么样\"时\n\n"
                "计算内容：合计、平均值、最大值、最小值（对前 3 个数值列）\n\n"
                "禁止场景：\n"
                "- 不查询数据库\n"
                "- 不生成图表配置\n"
                "- 不做趋势预测\n\n"
                "输入：SQL 查询结果 JSON（含 columns 和 rows）\n\n"
                "输出：多行文本，每行对应一个数值列的统计摘要"
            ),
            capability="insight_generate",
            agent_type="report",
            risk_level="low",
            input_schema={"sql_result": "string"},
            output_schema={"type": "string"},
        ),
    )

    registry.register(
        "trend_analysis", report_tools.trend_analysis,
        ToolMetadata(
            name="trend_analysis",
            description=(
                "分析时间序列数据的整体趋势方向（上升/下降/平稳）。\n\n"
                "适用场景：\n"
                "- 数据按时间排序，想知道整体走势\n"
                "- 判断后半段相对前半段的变化幅度\n\n"
                "判断规则：\n"
                "- 后半段均值 > 前半段 110% → \"上升趋势\"\n"
                "- 前半段均值 > 后半段 110% → \"下降趋势\"\n"
                "- 否则 → \"平稳\"\n\n"
                "禁止场景：\n"
                "- 数据少于 2 行时不适用\n"
                "- 不查询数据库\n\n"
                "输入：SQL 查询结果 JSON（需包含至少一个数值列）\n\n"
                "输出：趋势判断文本，如 \"整体呈上升趋势，后半段增长 23.5%\""
            ),
            capability="trend_analysis",
            agent_type="report",
            risk_level="low",
            input_schema={"data_json": "string", "value_col": "string"},
            output_schema={"type": "string"},
        ),
    )

    registry.register(
        "group_compare", report_tools.group_compare,
        ToolMetadata(
            name="group_compare",
            description=(
                "按指定维度分组，对比各组的数值合计。\n\n"
                "适用场景：\n"
                "- 需要按区域/产品/客户等维度做横向对比\n"
                "- 比较\"哪个组表现最好/最差\"\n\n"
                "输入参数：\n"
                "- data_json：SQL 查询结果\n"
                "- group_col：分组字段名（不指定时自动选首个非数值列）\n"
                "- value_col：数值字段名（不指定时自动选首个数值列）\n\n"
                "输出：多行文本，每行 \"分组名: 合计=数值\"，按合计降序排列"
            ),
            capability="group_compare",
            agent_type="report",
            risk_level="low",
            input_schema={"data_json": "string", "group_col": "string", "value_col": "string"},
            output_schema={"type": "string"},
        ),
    )

    registry.register(
        "detect_anomaly", report_tools.detect_anomaly,
        ToolMetadata(
            name="detect_anomaly",
            description=(
                "用标准差方法检测数据中的异常值（偏离均值超过 2 倍标准差）。\n\n"
                "适用场景：\n"
                "- 需要找出\"异常高\"或\"异常低\"的数据点\n"
                "- 数据质量检查或异常预警\n\n"
                "判断规则：\n"
                "- 计算均值和标准差\n"
                "- 偏离均值超过 2σ 的标记为异常\n"
                "- 数据少于 3 行时无法计算\n\n"
                "禁止场景：\n"
                "- 不查询数据库\n"
                "- 不做趋势预测\n\n"
                "输入：SQL 查询结果 JSON，value_col（数值字段名，可选）\n\n"
                "输出：异常值列表文本，如 \"发现 2 个异常值: 华东: 1,234.56; 华南: 987.65\""
            ),
            capability="anomaly_detection",
            agent_type="report",
            risk_level="low",
            input_schema={"data_json": "string", "value_col": "string"},
            output_schema={"type": "string"},
        ),
    )
