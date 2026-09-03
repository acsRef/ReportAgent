from __future__ import annotations

from app.tools import data_tools, faq_tools, interface_dict_tools, report_tools, sql_tools
from app.tools.registry import ToolMetadata, registry


def register_all_tools():
    registry.register(
        "search_tables",
        data_tools.search_tables,
        ToolMetadata(
            name="search_tables",
            purpose="根据中文业务关键词搜索数据库表，返回表名、字段结构、DDL 和匹配分",
            when_to_use="不知道数据在哪个表时找表，如 销售额/退款/趋势 等业务概念需定位表",
            when_not_to_use="已知精确表名仅需字段时不要用，改用 get_table_ddl；需全量表清单时不要用，改用 list_tables",
            description=(
                "根据中文业务关键词搜索数据库表，返回表名、字段结构、DDL 和匹配分。"
                "输入：中文自然语言描述（如 '2024年各区域销售额'），top_k 返回条数（默认 3）。"
                "输出：JSON 数组，每项含 table_name/description/columns/ddl/score；无匹配时返回默认 Top 表。"
                "用于：不知道数据在哪个表时找表，如 search_tables('各区域销售额') → fact_orders + dim_store。"
                "不要用来查看已知表名的字段——用 get_table_ddl；不要用来浏览数据库有哪些表——用 list_tables；此工具不查业务数据行。"
                "什么时候调：用户提问含未定位的业务实体且需先找表。"
                "什么时候不调：已知表名、仅需全表列表或仅需字典释义时不调。"
                "调用前：提供中文 query 与可选 top_k。"
                "调用后：得到候选表及 DDL，供后续 get_table_ddl 或 SQL 生成使用。"
            ),
            capability="schema_search",
            agent_type="data",
            source="mcp",
            risk_level="low",
            permission=[],
            permission_required=[],
            input_schema={"query": "string", "top_k": "int"},
            output_schema={"tables": "array"},
            preconditions=["MCP 可用或降级路径可达", "query 为非空中文描述"],
            postconditions=["返回候选表列表", "每项含 table_name 与 DDL"],
            failure_policy="MCP_TIMEOUT 重试 2 次后抛 MCPBoundaryError；UNAVAILABLE 时显式错误不伪装空结果",
            side_effects="只读检索，无持久化副作用",
            examples=["search_tables('各区域销售额') → fact_orders + dim_store", "search_tables('区域退款率', top_k=5)"],
        ),
    )
    registry.register(
        "get_table_ddl",
        data_tools.get_table_ddl,
        ToolMetadata(
            name="get_table_ddl",
            purpose="获取指定单张表的完整 CREATE TABLE DDL（字段名、类型、精度）",
            when_to_use="已定位目标表、写 SQL 前需确认字段名和类型存在时",
            when_not_to_use="不知道表名时不要用，先用 search_tables 搜索；需全表总览时不要用，改用 list_tables",
            description=(
                "获取指定单张表的完整 CREATE TABLE DDL（字段名、类型、精度）。"
                "输入：精确表名（如 'fact_orders'）。"
                "输出：CREATE TABLE 语句文本；表不存在时返回 'Table xxx not found'。"
                "用于：写 SQL 前确认字段名和类型存在，如 get_table_ddl('dim_store') → region/city/store_type 等字段。"
                "不要用来在不知道表名时猜表——先用 search_tables 搜索；不要用来总览全部表——用 list_tables；此工具不返回业务数据。"
                "什么时候调：已确定表名且需字段明细以拼 SQL。"
                "什么时候不调：未做表搜索直接猜表名时不调。"
                "调用前：提供精确表名且该表已在候选结果中。"
                "调用后：得到建表语句，用于校验字段并生成 SQL。"
            ),
            capability="schema_read",
            agent_type="data",
            source="mcp",
            risk_level="low",
            permission=[],
            permission_required=[],
            input_schema={"table_name": "string"},
            output_schema={"type": "string"},
            preconditions=["table_name 为合法标识", "MCP 或字典 KB 可用"],
            postconditions=["返回 CREATE TABLE 文本或 not found 提示"],
            failure_policy="MCP_TIMEOUT 重试 2 次；UNAVAILABLE 显式错误不 fallback 伪装",
            side_effects="只读，无副作用",
            examples=["get_table_ddl('dim_store') → 含 region/city/store_type", "get_table_ddl('fact_orders')"],
        ),
    )
    registry.register(
        "list_tables",
        data_tools.list_tables,
        ToolMetadata(
            name="list_tables",
            purpose="列出数据库全部表的表名、中文描述和字段数",
            when_to_use="首次了解维度表和事实表全貌，或 search_tables 未返回理想结果时兜底",
            when_not_to_use="需具体字段结构时不要用，改用 get_table_ddl；需按业务概念搜表时不要用，改用 search_tables",
            description=(
                "列出数据库全部表的表名、中文描述和字段数。"
                "输入：无参数。"
                "输出：JSON 数组，每项含 table_name/description/column_count；数据库不可用时返回空数组。"
                "用于：首次了解维度表和事实表全貌，或 search_tables 未返回理想结果时兜底。"
                "不要用来查看具体字段结构——用 get_table_ddl；不要用来按业务概念搜表——用 search_tables。"
                "什么时候调：需要全库表清单以做探索式分析。"
                "什么时候不调：已锁定目标表或只需单表 DDL 时不调。"
                "调用前：无前置参数。"
                "调用后：得到全表清单，用于后续筛选与 get_table_ddl。"
            ),
            capability="schema_list",
            agent_type="data",
            source="local",
            risk_level="low",
            permission=[],
            permission_required=[],
            input_schema={},
            output_schema={"tables": "array"},
            preconditions=["字典 KB 文档可列出"],
            postconditions=["返回表清单，每项含 column_count"],
            failure_policy="字典服务不可达返回空数组并打 warning，不抛异常阻塞主流程",
            side_effects="只读，无副作用",
            examples=["list_tables() → 含 fact_orders/fact_payments 等 7 表（2 事实 + 5 维度）"],
        ),
    )
    registry.register(
        "search_interface_dictionary",
        interface_dict_tools.search_interface_dictionary,
        ToolMetadata(
            name="search_interface_dictionary",
            purpose="在数据字典知识库中检索字段/接口/表的含义释义",
            when_to_use="用户问题涉及接口字段或字段含义不明确、写 SQL 前需确认业务口径时",
            when_not_to_use="找数据表时不要用，改用 search_tables；需执行查询时不要用，此工具只读字典",
            description=(
                "在数据字典知识库中检索字段/接口/表的含义释义，返回命中片段与来源。"
                "输入：query 中文自然语言（如 'order_amount 是什么'），top_k 返回条数（默认 5）。"
                "输出：JSON，matches=[{text, source, score}]；无匹配时 matches=[]；字典服务未配置/不可达时返回 error 字段。"
                "用于：用户问题涉及接口字段或字段含义不明确时查释义；写 SQL 前确认业务口径。"
                "不要用来找数据表——用 search_tables；此工具只读字典文档，不查业务数据行。"
                "什么时候调：字段口径模糊或需 distinguished_field 释义时。"
                "什么时候不调：已明确字段含义且仅需表结构时不调。"
                "调用前：提供中文释义 query。"
                "调用后：得到 matches 供 prompt 注入或澄清。"
            ),
            capability="dictionary_search",
            agent_type="data",
            source="mcp",
            risk_level="low",
            permission=[],
            permission_required=[],
            input_schema={"query": "string", "top_k": "int"},
            output_schema={"matches": "array"},
            preconditions=["query 非空", "字典 KB 可用"],
            postconditions=["返回 matches 或 error 字段"],
            failure_policy="MCP_INVALID_RESPONSE 直接 error；UNAVAILABLE 时显式 error 不伪装",
            side_effects="只读，无副作用",
            examples=["search_interface_dictionary('order_amount 是什么')", "search_interface_dictionary('GMV 口径')"],
        ),
    )
    registry.register(
        "search_faq",
        faq_tools.search_faq,
        ToolMetadata(
            name="search_faq",
            purpose="在 Schema FAQ 知识库中检索常见分析问题的 SQL 模板与业务口径要点",
            when_to_use="写 SQL 前查这类问题以前怎么算，如销售额口径/退款率/销售占比/趋势等口径与分组排序模板",
            when_not_to_use="找数据表时不要用，改用 search_tables；查业务数据行时不要用，此工具只读 FAQ",
            description=(
                "在 Schema FAQ 知识库中检索最常见分析问题的 SQL 模板与业务口径要点。"
                "输入：query 中文自然语言（如 '各区域销售额排名'、'区域退款率'），top_k 返回条数（默认 3）。"
                "输出：JSON，matches=[{question, text, score}]；无匹配时 matches=[]。"
                "用于：写 SQL 前查「这类问题以前怎么算」——销售额口径/退款率/销售占比/月度趋势等业务口径"
                "和常见分组/排序模板。"
                "不要用来找数据表——用 search_tables；不要用来查业务数据行——此工具只读 FAQ 知识库。"
                "什么时候调：需复用历史口径或 SQL 模板以提升生成质量时。"
                "什么时候不调：仅需表结构或字典释义时不调。"
                "调用前：提供中文业务问题。"
                "调用后：得到 FAQ matches 注入 SQL 生成 prompt。"
            ),
            capability="faq_search",
            agent_type="data",
            source="mcp",
            risk_level="low",
            permission=[],
            permission_required=[],
            input_schema={"query": "string", "top_k": "int"},
            output_schema={"matches": "array"},
            preconditions=["query 非空", "FAQ KB 可用"],
            postconditions=["返回 matches 列表，元素含 question/text/score"],
            failure_policy="MCP_TIMEOUT 重试 2 次；INVALID_RESPONSE 抛显式错误",
            side_effects="只读，无副作用",
            examples=["search_faq('区域退款率')", "search_faq('月度销售趋势')"],
        ),
    )
    registry.register(
        "validate_sql",
        sql_tools.validate_sql,
        ToolMetadata(
            name="validate_sql",
            purpose="三重校验 SQL 安全性与语法：黑名单 + AST SELECT 校验 + PostgreSQL EXPLAIN",
            when_to_use="每次 execute_sql 之前必须调用；重试生成新 SQL 后重新校验",
            when_not_to_use="不要用来执行查询获取数据，改用 execute_sql",
            description=(
                "三重校验 SQL 安全性与语法：DDL/DML 黑名单 + sqlglot AST 必须为 SELECT + PostgreSQL EXPLAIN，不执行查询。"
                "输入：待校验的 SQL 文本。"
                "输出：{valid: bool, error: 失败原因}。"
                "用于：每次 execute_sql 之前必须调用；重试生成新 SQL 后重新校验。"
                "不要用来执行查询获取数据——用 execute_sql；校验通过只代表安全可执行，不代表业务逻辑正确。"
                "错误示例：{\"valid\":false, \"error\":\"仅允许 SELECT 语句\"} → SQL 含 DDL/DML 关键字"
                "什么时候调：SQL 生成后、执行前必调。"
                "什么时候不调：已校验通过的同一 SQL 重复执行前可不重复校验。"
                "调用前：提供待校验 SQL 字符串。"
                "调用后：得到 valid 与 error，valid=true 方可执行。"
            ),
            capability="sql_validate",
            agent_type="sql",
            source="local",
            risk_level="medium",
            permission=[],
            permission_required=[],
            input_schema={"sql": "string"},
            output_schema={"valid": "bool", "error": "string"},
            preconditions=["sql 为单条语句文本"],
            postconditions=["返回 valid 与 error 字段"],
            failure_policy="语法或黑名单命中返回 valid=false，不抛异常；EXPLAIN 失败映射为 valid=false",
            side_effects="只读校验，不执行查询",
            examples=["validate_sql('SELECT * FROM fact_orders') → valid=true", "validate_sql('DROP TABLE x') → valid=false"],
        ),
    )
    registry.register(
        "execute_sql",
        sql_tools.execute_sql,
        ToolMetadata(
            name="execute_sql",
            purpose="执行只读 SELECT 查询，返回列结构和行数据",
            when_to_use="validate_sql 校验 valid=true 后立即执行，为报表生成提供数据",
            when_not_to_use="不要用来执行任何 DDL/DML——连接为只读；未校验的 SQL 不要直接执行",
            description=(
                "执行只读 SELECT 查询，返回列结构和行数据。"
                "输入：合法 SELECT 语句（必须先经 validate_sql 校验 valid=true，字段名须出自已确认的表结构）。"
                "输出：{columns: [{name, type}], rows: [每行 {字段: 值}], error: 空为成功}。"
                "用于：校验通过后立即执行，为报表生成提供数据。"
                "不要用来执行任何 DDL/DML——连接为只读；执行失败时按 error 修正 SQL 重试，3 次失败转用户澄清。"
                "错误返回示例：{\"error\": \"relation xxx does not exist\", \"error_kind\": \"object\"} → 表/字段不存在，先用 get_table_ddl 确认正确名称后修正 SQL"
                "什么时候调：校验通过且需真实数据时。"
                "什么时候不调：未通过 validate_sql 或字段未确认时不调。"
                "调用前：提供已校验的 SELECT 且字段来自已确认 DDL。"
                "调用后：得到 columns/rows 或 error_kind 供修复。"
            ),
            capability="sql_execute",
            agent_type="sql",
            source="local",
            risk_level="high",
            permission=["data.query.execute"],
            permission_required=["data.query.execute"],
            input_schema={"sql": "string"},
            output_schema={"columns": "array", "rows": "array", "error": "string"},
            preconditions=["SQL 已通过 validate_sql", "字段名出自已确认表结构"],
            postconditions=["返回 columns/rows 或 error/error_kind"],
            failure_policy="按 error_kind 分类：timeout/connection 可重试，其余映射为用户可见错误并持久化",
            side_effects="只读查询，不修改数据；消耗只读连接与超时预算",
            examples=["execute_sql('SELECT s.region, SUM(o.order_amount) FROM fact_orders o JOIN dim_store s ON o.store_id = s.store_id GROUP BY s.region')"],
        ),
    )
    registry.register(
        "chart_advisor",
        sql_tools.chart_advisor,
        ToolMetadata(
            name="chart_advisor",
            purpose="根据 SQL 查询结果推荐图表类型（饼图/柱状图/表格）和配置",
            when_to_use="数据就绪后需决定如何以图表展示时",
            when_not_to_use="不要用来做数值摘要，改用 insight_analyst；数据为空时不调",
            description=(
                "根据 SQL 查询结果推荐图表类型（饼图/柱状图/表格）和配置：1 个分类 + 1 个数值维度且 ≤8 行 → 饼图，>8 行 → 柱状图，无合适维度 → 表格。"
                "输入：查询结果 JSON（含 columns 和 rows）。"
                "输出：{type: pie|bar|table, config: {data, dimensions}}。"
                "用于：数据就绪后决定如何以图表展示。"
                "不要用来做数值摘要（合计/均值）——用 insight_analyst；此工具不查数据库。"
                "什么时候调：需为 report 选择可视化形态时。"
                "什么时候不调：已有明确图表需求或仅需数值摘要时不调。"
                "调用前：提供完整查询结果 JSON。"
                "调用后：得到 type 与 config 供前端渲染。"
            ),
            capability="chart_recommend",
            agent_type="report",
            source="local",
            risk_level="medium",
            permission=[],
            permission_required=[],
            input_schema={"sql_result": "string"},
            output_schema={"type": "string", "config": "object"},
            preconditions=["sql_result 含 columns 与 rows"],
            postconditions=["返回推荐图表类型与配置"],
            failure_policy="输入非法返回 table 类型兜底，不抛异常",
            side_effects="纯计算，无副作用",
            examples=["chart_advisor('{\"columns\":...,\"rows\":[...]}') → pie"],
        ),
    )
    registry.register(
        "insight_analyst",
        sql_tools.insight_analyst,
        ToolMetadata(
            name="insight_analyst",
            purpose="计算查询结果前 3 个数值列的合计、均值、最大值、最小值",
            when_to_use="需要告诉用户数据整体怎么样时",
            when_not_to_use="不要用来生成图表，改用 chart_advisor；不要用来做趋势判断，改用 trend_analysis",
            description=(
                "计算查询结果前 3 个数值列的合计、均值、最大值、最小值。"
                "输入：查询结果 JSON（含 columns 和 rows）。"
                "输出：多行文本，每行一个数值列的统计，如 '销售额: 合计=1,234,567.00, 平均=102,880.58'；空数据返回提示文本。"
                "用于：需要告诉用户「数据整体怎么样」时。"
                "不要用来生成图表——用 chart_advisor；不要用来做趋势判断——用 trend_analysis；此工具不查数据库。"
                "什么时候调：需数值摘要洞察时。"
                "什么时候不调：需趋势或分组对比时不调。"
                "调用前：提供查询结果 JSON。"
                "调用后：得到统计文本供 report 合成。"
            ),
            capability="insight_generate",
            agent_type="report",
            source="local",
            risk_level="low",
            permission=[],
            permission_required=[],
            input_schema={"sql_result": "string"},
            output_schema={"type": "string"},
            preconditions=["sql_result 非空", "含数值列"],
            postconditions=["返回统计文本或空数据提示"],
            failure_policy="空数据返回提示文本，不抛异常",
            side_effects="纯计算，无副作用",
            examples=["insight_analyst(result_json) → '销售额: 合计=...'"],
        ),
    )
    registry.register(
        "trend_analysis",
        report_tools.trend_analysis,
        ToolMetadata(
            name="trend_analysis",
            purpose="判断时间序列数据的趋势方向（上升/下降/平稳）及变化幅度",
            when_to_use="想看某个指标随时间走高还是走低时",
            when_not_to_use="不要用来横向对比组间高低，改用 group_compare；不要用来找异常点，改用 detect_anomaly",
            description=(
                "判断时间序列数据的趋势方向（上升/下降/平稳）及变化幅度：后半段均值对比前半段，相差超 10% 判为升/降。"
                "输入：查询结果 JSON（数据须按时间排序，至少 2 行，自动取首个数值列）。"
                "输出：趋势文本，如 '整体呈上升趋势，后半段增长 23.5%'。"
                "用于：想看某个指标随时间走高还是走低。"
                "不要用来横向对比组间高低——用 group_compare；不要用来找异常点——用 detect_anomaly。"
                "什么时候调：数据含时间维度且需趋势结论时。"
                "什么时候不调：数据无时序或仅需分组排名时不调。"
                "调用前：提供按时间排序的查询结果。"
                "调用后：得到趋势文本。"
            ),
            capability="trend_analysis",
            agent_type="report",
            source="local",
            risk_level="low",
            permission=[],
            permission_required=[],
            input_schema={"data_json": "string", "value_col": "string"},
            output_schema={"type": "string"},
            preconditions=["数据按时间排序且 ≥2 行"],
            postconditions=["返回趋势描述文本"],
            failure_policy="非法输入返回参数错误提示，不抛异常",
            side_effects="纯计算，无副作用",
            examples=["trend_analysis(time_series_json) → '上升 23.5%'"],
        ),
    )
    registry.register(
        "group_compare",
        report_tools.group_compare,
        ToolMetadata(
            name="group_compare",
            purpose="按维度分组汇总数值，输出各组排名（降序）",
            when_to_use="哪个区域销售额最高、哪个产品最畅销等横向对比时",
            when_not_to_use="不要用来观察时间走势，改用 trend_analysis；不要用来检测异常，改用 detect_anomaly",
            description=(
                "按维度（区域/产品/客户等）分组汇总数值，输出各组排名（降序）。"
                "输入：查询结果 JSON；group_col 分组字段、value_col 数值字段可选（缺省自动选首个非数值列和首个数值列）。"
                "输出：多行文本 '分组名: 合计=数值'（降序），如 '华东: 合计=1,234,567.00'。"
                "用于：哪个区域销售额最高、哪个产品最畅销等横向对比。"
                "不要用来观察时间走势——用 trend_analysis；不要用来检测异常——用 detect_anomaly。"
                "什么时候调：需分组横向排名时。"
                "什么时候不调：需时序趋势时不调。"
                "调用前：提供查询结果与可选分组/数值字段。"
                "调用后：得到排名文本。"
            ),
            capability="group_compare",
            agent_type="report",
            source="local",
            risk_level="low",
            permission=[],
            permission_required=[],
            input_schema={"data_json": "string", "group_col": "string", "value_col": "string"},
            output_schema={"type": "string"},
            preconditions=["数据含分组与数值列"],
            postconditions=["返回降序排名文本"],
            failure_policy="字段缺失自动推断，仍缺失返回提示文本",
            side_effects="纯计算，无副作用",
            examples=["group_compare(json, 'region', 'amount') → '华东: 合计=...'"],
        ),
    )
    registry.register(
        "detect_anomaly",
        report_tools.detect_anomaly,
        ToolMetadata(
            name="detect_anomaly",
            purpose="用 2 倍标准差检测异常高/低数据点",
            when_to_use="想看哪里数据不正常或做数据质量检查时",
            when_not_to_use="不要用来分析整体趋势，改用 trend_analysis；不要用来做分组对比，改用 group_compare",
            description=(
                "用 2 倍标准差检测异常高/低数据点（偏离均值超过 2σ 视为异常）。"
                "输入：查询结果 JSON（至少 3 行）；value_col 数值字段可选。"
                "输出：异常值列表文本，如 '发现 2 个异常值: 华东: 1,234.56; 华南: 987.65'；无异常时返回 '未发现明显异常值'。"
                "用于：想看「哪里数据不正常」或做数据质量检查。"
                "不要用来分析整体趋势——用 trend_analysis；不要用来做分组对比——用 group_compare。"
                "什么时候调：需异常检测时。"
                "什么时候不调：数据量 <3 或仅需趋势/排名时不调。"
                "调用前：提供查询结果与可选数值字段。"
                "调用后：得到异常列表文本。"
            ),
            capability="anomaly_detection",
            agent_type="report",
            source="local",
            risk_level="low",
            permission=[],
            permission_required=[],
            input_schema={"data_json": "string", "value_col": "string"},
            output_schema={"type": "string"},
            preconditions=["数据 ≥3 行", "含数值列"],
            postconditions=["返回异常列表或无异常提示"],
            failure_policy="数据不足返回提示，不抛异常",
            side_effects="纯计算，无副作用",
            examples=["detect_anomaly(json) → '发现 2 个异常值...'"],
        ),
    )
