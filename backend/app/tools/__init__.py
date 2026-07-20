from app.tools.registry import registry, ToolMetadata
from app.tools import data_tools, sql_tools, report_tools


def register_all_tools():
    # Data Agent tools
    registry.register(
        "search_tables", data_tools.search_tables,
        ToolMetadata(
            name="search_tables",
            description="语义搜索数据库表",
            capability="schema_search",
            agent_type="data",
        ),
    )
    registry.register(
        "get_table_ddl", data_tools.get_table_ddl,
        ToolMetadata(
            name="get_table_ddl",
            description="获取表完整 DDL",
            capability="schema_read",
            agent_type="data",
        ),
    )
    registry.register(
        "list_tables", data_tools.list_tables,
        ToolMetadata(
            name="list_tables",
            description="列出所有可用表",
            capability="schema_list",
            agent_type="data",
        ),
    )

    # SQL Agent tools
    registry.register(
        "validate_sql", sql_tools.validate_sql,
        ToolMetadata(
            name="validate_sql",
            description="验证 SQL 语法和安全性",
            capability="sql_validate",
            agent_type="sql",
        ),
    )
    registry.register(
        "execute_sql", sql_tools.execute_sql,
        ToolMetadata(
            name="execute_sql",
            description="执行只读 SQL 查询",
            capability="sql_execute",
            agent_type="sql",
            permission_required=["data.query.execute"],
        ),
    )

    # Report Agent tools
    registry.register(
        "chart_advisor", sql_tools.chart_advisor,
        ToolMetadata(
            name="chart_advisor",
            description="推荐图表类型",
            capability="chart_recommend",
            agent_type="report",
        ),
    )
    registry.register(
        "insight_analyst", sql_tools.insight_analyst,
        ToolMetadata(
            name="insight_analyst",
            description="分析数值洞察",
            capability="insight_generate",
            agent_type="report",
        ),
    )
    registry.register(
        "trend_analysis", report_tools.trend_analysis,
        ToolMetadata(
            name="trend_analysis",
            description="分析数据趋势",
            capability="trend_analysis",
            agent_type="report",
        ),
    )
    registry.register(
        "group_compare", report_tools.group_compare,
        ToolMetadata(
            name="group_compare",
            description="按维度分组对比",
            capability="group_compare",
            agent_type="report",
        ),
    )
    registry.register(
        "detect_anomaly", report_tools.detect_anomaly,
        ToolMetadata(
            name="detect_anomaly",
            description="检测异常值",
            capability="anomaly_detection",
            agent_type="report",
        ),
    )
