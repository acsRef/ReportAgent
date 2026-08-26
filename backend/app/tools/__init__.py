from app.tools.registry import registry, ToolMetadata
from app.tools import data_tools, sql_tools, report_tools, interface_dict_tools, faq_tools


# 工具描述是模型选择工具的主要依据：每条描述都按「用途 + 输入 + 输出 +
# 适用 + 不要用来（→ 替代工具）」五要素撰写（中文），由
# app.llm._format_tools_for_prompt 完整注入 prompt（不截断首行）。
# 修改描述时保持这一格式——「不要用来」一行直接防止最常见的误选。


def register_all_tools():
    # =========================================
    # 数据 Agent 工具 —— 表结构发现
    # =========================================

    registry.register(
        "search_tables", data_tools.search_tables,
        ToolMetadata(
            name="search_tables",
            description=(
                "根据中文业务关键词搜索数据库表，返回表名、字段结构、DDL 和匹配分。"
                "输入：中文自然语言描述（如 '2024年各区域销售额'），top_k 返回条数（默认 3）。"
                "输出：JSON 数组，每项含 table_name/description/columns/ddl/score；无匹配时返回默认 Top 表。"
                "用于：不知道数据在哪个表时找表，如 search_tables('退货原因分析') → fact_returns。"
                "不要用来查看已知表名的字段——用 get_table_ddl；不要用来浏览数据库有哪些表——用 list_tables；此工具不查业务数据行。"
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
                "获取指定单张表的完整 CREATE TABLE DDL（字段名、类型、精度）。"
                "输入：精确表名（如 'fact_sales'）。"
                "输出：CREATE TABLE 语句文本；表不存在时返回 'Table xxx not found'。"
                "用于：写 SQL 前确认字段名和类型存在，如 get_table_ddl('dim_region') → region_name/province/city 等字段。"
                "不要用来在不知道表名时猜表——先用 search_tables 搜索；不要用来总览全部表——用 list_tables；此工具不返回业务数据。"
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
                "列出数据库全部表的表名、中文描述和字段数。"
                "输入：无参数。"
                "输出：JSON 数组，每项含 table_name/description/column_count；数据库不可用时返回空数组。"
                "用于：首次了解维度表和事实表全貌，或 search_tables 未返回理想结果时兜底。"
                "不要用来查看具体字段结构——用 get_table_ddl；不要用来按业务概念搜表——用 search_tables。"
            ),
            capability="schema_list",
            agent_type="data",
            risk_level="low",
            output_schema={"tables": "array"},
        ),
    )

    registry.register(
        "search_interface_dictionary", interface_dict_tools.search_interface_dictionary,
        ToolMetadata(
            name="search_interface_dictionary",
            description=(
                "在数据字典知识库中检索字段/接口/表的含义释义，返回命中片段与来源。"
                "输入：query 中文自然语言（如 'total_amount 是什么'），top_k 返回条数（默认 5）。"
                "输出：JSON，matches=[{text, source, score}]；无匹配时 matches=[]；字典服务未配置/不可达时返回 error 字段。"
                "用于：用户问题涉及接口字段或字段含义不明确时查释义；写 SQL 前确认业务口径。"
                "不要用来找数据表——用 search_tables；此工具只读字典文档，不查业务数据行。"
            ),
            capability="dictionary_search",
            agent_type="data",
            risk_level="low",
            input_schema={"query": "string", "top_k": "int"},
            output_schema={"matches": "array"},
        ),
    )

    registry.register(
        "search_faq", faq_tools.search_faq,
        ToolMetadata(
            name="search_faq",
            description=(
                "在 Schema FAQ 知识库中检索最常见分析问题的 SQL 模板与业务口径要点。"
                "输入：query 中文自然语言（如 '区域退货率'、'毛利率'），top_k 返回条数（默认 3）。"
                "输出：JSON，matches=[{question, text, score}]——MCP 路径与本地 fallback "
                "路径暴露同一 Tool Contract；无匹配时 matches=[]。"
                "用于：写 SQL 前查「这类问题以前怎么算」——毛利率/退货率/出勤率/库存周转等业务口径和常见分组/排序模板。"
                "不要用来找数据表——用 search_tables；不要用来查业务数据行——此工具只读 FAQ 知识库。"
            ),
            capability="faq_search",
            agent_type="data",
            risk_level="low",
            input_schema={"query": "string", "top_k": "int"},
            output_schema={"matches": "array"},
        ),
    )

    # =========================================
    # SQL Agent 工具 —— 校验与执行
    # =========================================

    registry.register(
        "validate_sql", sql_tools.validate_sql,
        ToolMetadata(
            name="validate_sql",
            description=(
                "三重校验 SQL 安全性与语法：DDL/DML 黑名单 + sqlglot AST 必须为 SELECT + PostgreSQL EXPLAIN，不执行查询。"
                "输入：待校验的 SQL 文本。"
                "输出：{valid: bool, error: 失败原因}。"
                "用于：每次 execute_sql 之前必须调用；重试生成新 SQL 后重新校验。"
                "不要用来执行查询获取数据——用 execute_sql；校验通过只代表安全可执行，不代表业务逻辑正确。"
                "错误示例：{\"valid\":false, \"error\":\"仅允许 SELECT 语句\"} → SQL 含 DDL/DML 关键字"
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
                "执行只读 SELECT 查询，返回列结构和行数据。"
                "输入：合法 SELECT 语句（必须先经 validate_sql 校验 valid=true，字段名须出自已确认的表结构）。"
                "输出：{columns: [{name, type}], rows: [每行 {字段: 值}], error: 空为成功}。"
                "用于：校验通过后立即执行，为报表生成提供数据。"
                "不要用来执行任何 DDL/DML——连接为只读；执行失败时按 error 修正 SQL 重试，3 次失败转用户澄清。"
                "错误返回示例：\n"
                "- {\"error\": \"relation xxx does not exist\", \"error_kind\": \"object\"} → 表/字段不存在，先用 get_table_ddl 确认正确名称后修正 SQL\n"
                "- {\"error\": \"canceling statement due to statement timeout\", \"error_kind\": \"timeout\"} → 查询超时（>30s），尝试增加 WHERE 时间筛选或减少维度\n"
                "- {\"error\": \"permission denied for table yyy\", \"error_kind\": \"permission\"} → 无权限，换用其他表或缩小查询范围\n"
                "- {\"error\": \"syntax error at or near ...\", \"error_kind\": \"syntax\"} → SQL 语法错误，根据提示位置修正后重新 validate\n"
                "- {\"error\": \"column xxx does not exist\", \"error_kind\": \"object\"} → 字段名错误，用 get_table_ddl 确认字段后修正"
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
    # 报告 Agent 工具 —— 可视化与洞察
    # =========================================

    registry.register(
        "chart_advisor", sql_tools.chart_advisor,
        ToolMetadata(
            name="chart_advisor",
            description=(
                "根据 SQL 查询结果推荐图表类型（饼图/柱状图/表格）和配置：1 个分类 + 1 个数值维度且 ≤8 行 → 饼图，>8 行 → 柱状图，无合适维度 → 表格。"
                "输入：查询结果 JSON（含 columns 和 rows）。"
                "输出：{type: pie|bar|table, config: {data, dimensions}}。"
                "用于：数据就绪后决定如何以图表展示。"
                "不要用来做数值摘要（合计/均值）——用 insight_analyst；此工具不查数据库。"
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
                "计算查询结果前 3 个数值列的合计、均值、最大值、最小值。"
                "输入：查询结果 JSON（含 columns 和 rows）。"
                "输出：多行文本，每行一个数值列的统计，如 '销售额: 合计=1,234,567.00, 平均=102,880.58'；空数据返回提示文本。"
                "用于：需要告诉用户「数据整体怎么样」时。"
                "不要用来生成图表——用 chart_advisor；不要用来做趋势判断——用 trend_analysis；此工具不查数据库。"
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
                "判断时间序列数据的趋势方向（上升/下降/平稳）及变化幅度：后半段均值对比前半段，相差超 10% 判为升/降。"
                "输入：查询结果 JSON（数据须按时间排序，至少 2 行，自动取首个数值列）。"
                "输出：趋势文本，如 '整体呈上升趋势，后半段增长 23.5%'。"
                "用于：想看某个指标随时间走高还是走低。"
                "不要用来横向对比组间高低——用 group_compare；不要用来找异常点——用 detect_anomaly。"
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
                "按维度（区域/产品/客户等）分组汇总数值，输出各组排名（降序）。"
                "输入：查询结果 JSON；group_col 分组字段、value_col 数值字段可选（缺省自动选首个非数值列和首个数值列）。"
                "输出：多行文本 '分组名: 合计=数值'（降序），如 '华东: 合计=1,234,567.00'。"
                "用于：哪个区域销售额最高、哪个产品最畅销等横向对比。"
                "不要用来观察时间走势——用 trend_analysis；不要用来检测异常——用 detect_anomaly。"
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
                "用 2 倍标准差检测异常高/低数据点（偏离均值超过 2σ 视为异常）。"
                "输入：查询结果 JSON（至少 3 行）；value_col 数值字段可选。"
                "输出：异常值列表文本，如 '发现 2 个异常值: 华东: 1,234.56; 华南: 987.65'；无异常时返回 '未发现明显异常值'。"
                "用于：想看「哪里数据不正常」或做数据质量检查。"
                "不要用来分析整体趋势——用 trend_analysis；不要用来做分组对比——用 group_compare。"
            ),
            capability="anomaly_detection",
            agent_type="report",
            risk_level="low",
            input_schema={"data_json": "string", "value_col": "string"},
            output_schema={"type": "string"},
        ),
    )
