# 2026-08-10 工作流式意图识别：修 no-op + 外部接口识别 + 路由

> 状态: 已完成（commit `7eb890b`；含真实 E2E）

## Context

用户反馈：意图识别需要写好，是重要模块。现状问题（查证属实）：

1. **`data_graph._detect_intent` 是 no-op**：`has_data_intent = any(k in q for k in [...])` 算了但 `return {"discovered_tables": [] if not has_data_intent else []}` 两个分支都返回 `[]`——意图门形同虚设，闲聊/非数据查询也走完整 schema 检索 + LLM 需求解析（白烧 token）。
2. **意图判定是纯关键词**：`_detect_intent`（关键词）、legacy `_classify_intent`（关键词，默认"报表"）。"帮我写个邮件"会被当报表。
3. **不识别外部接口**：`interface_dict_tools` 已把字典块标成 `data_source_type=stream|table`（"长连接/websocket/推送"→stream，不在事实表里），但意图层没有"外部接口/实时数据"类别——用户问外部接口会被误当 SQL 报表。

用户确认：闲聊→直接回复不深处理；外部接口→生成接口需求卡；模糊查询→加 LLM 分类。前端 `reportAdapter` 已能渲染 `answer.text` 纯文本（闲聊可用 `report` 事件）。

## Design

### 1. 新 `app/agent/intent.py`：工作流式意图分类器

```python
class IntentKind(str, Enum): CHITCHAT / REPORT / INTERFACE / DASHBOARD / UNKNOWN

@dataclass
class IntentResult: kind, reason, confidence

def classify_intent(user_query, dict_stream_hit=False) -> IntentResult
```

三段式（廉价 → 昂贵）：

- **Stage 1 关键词快路径**（无 LLM/无 HTTP）：闲聊（你好/谢谢/你能做什么）、看板。
- **Stage 2 外部接口检测**：入参 `dict_stream_hit`（字典检索里有 `data_source_type=stream` 命中）或接口提示词（接口/推送/实时/websocket/长连接）→ INTERFACE。
- **Stage 3 LLM 分类**：其余 → 报表 / 外部接口 / 闲聊 / 其他。输出 `{kind, reason, confidence}`。

纯函数、可单测；`safe_json_parse` 兜底 LLM 输出。

### 2. 修 `data_graph._detect_intent` no-op

改为真正门控：`has_data_intent=False` 时 `_search_schema` 短路（返回空 schema，不调 rag 检索）。与需求分析图的意图门形成双保险。

### 3. `requirement_analysis_graph` 接入意图 + 路由

图拓扑：

```
security_guard → _classify_intent → _route_intent:
   CHITCHAT   → _casual_reply → END
   INTERFACE  → _interface_requirement → persist_draft → END
   REPORT     → data_agent → requirement_parse → persist_draft
```

- `_classify_intent`（traced）：调 `classify_intent`；未走关键词/接口命中时查一次字典（`search_interface_dictionary`）得 `dict_stream_hit` + `dict_context` 存 state（`requirement_parse` 复用，避免二次检索）。LLM 分类复用 `call_llm`。
- `_route_intent`：按 `intent` 路由。
- `_casual_reply`：置 `casual_reply`（文本）到 state，END——不建卡、不跑 LLM 需求解析。
- `_interface_requirement`：**确定性构造**外部接口需求卡（不经 LLM）：
  - `summary`：`此查询涉及外部实时接口数据（<命中接口>），需接入实时数据源，非数据库报表`。
  - `assumptions`：`RequirementAssumption(key="data_source:stream", text=..., accepted=None)`——前端展示、确认流程据此短路。
  - 其余字段空/默认。
- `persist_draft` 复用（卡落库 + session 指针更新）。

### 4. `main.py` 闲聊发射

`_chat_requirement_analysis` 里 `result.get("intent") == "chitchat"` 时，改发 `report` 事件（`answer.text` = 友好回复，无表/图），不发 `requirement` 卡。复用 `reportAdapter` 文本渲染，前端零契约改动。

### 5. `confirmed_execution_graph` 外部接口短路

locked 需求卡含 `data_source:stream` assumption → 不生成 SQL，走接口说明响应（`report` 文本：这是外部实时接口，需接入数据源）。避免确认后误对星型 schema 生成 SQL。

## Files to change

- `backend/app/agent/intent.py`（新建）：`IntentKind` / `IntentResult` / `classify_intent`。
- `backend/app/agent/data_graph.py`：修 `_detect_intent` 门控。
- `backend/app/agent/requirement_analysis_graph.py`：加 `_classify_intent` / `_casual_reply` / `_interface_requirement` + `_route_intent` + 状态字段。
- `backend/app/main.py`：闲聊发射 `report` 文本。
- `backend/app/agent/confirmed_execution_graph.py`：外部接口短路。
- 测试：`tests/smoke/test_intent.py`（新建）、`tests/graphs/test_requirement_analysis_intent.py`（新建）。
- `docs/plans/2026-08-10-intent-recognition-workflow.md`（本文件）。

## Reused existing utilities

- `app.tools.interface_dict_tools.search_interface_dictionary`：字典检索得 `data_source_type`（stream/table）+ `dict_context`。
- `app.llm.call_llm` + `app.utils.text.safe_json_parse`：LLM 分类。
- `app.models.requirement.RequirementAssumption` / `RequirementCard`：外部接口卡构造。
- `persist_draft` / `_route_security` 既有节点与路由模式。
- `reportAdapter`（前端）文本渲染：闲聊 `report` 事件。

## Verification

```bash
cd backend && pytest tests/smoke/test_intent.py tests/graphs/test_requirement_analysis_intent.py -v
cd backend && pytest -q
```

新增测试：

1. `classify_intent`：闲聊关键词→CHITCHAT；接口提示词/`dict_stream_hit`→INTERFACE；报表查询→REPORT（LLM mock）；未知→LLM 判定。
2. `_detect_intent` 门控：非数据关键词→`has_data_intent=False`→`_search_schema` 短路不调 rag。
3. 需求分析图：REPORT 走 data_agent→parse；CHITCHAT 产出 `casual_reply` 不建卡；INTERFACE 产出含 `data_source:stream` assumption 的卡。
4. `main.py` 闲聊：`intent=chitchat` 时发 `report` 文本事件，不发 `requirement`。
5. confirmed 短路：locked 卡含 stream assumption → 不调 SQL 节点。

手工冒烟：

1. "你好" → 直接文本回复，SSE 无 requirement 卡。
2. "订单接口的字段含义" → 接口需求卡（含外部实时数据标注）。
3. "各区域销售额" → 正常需求卡，走确认→SQL。

## 落地记录（2026-08-10，真实 E2E）

- 起 ragent-py 实测：「你好」→ chitchat → casual 文本（无卡）；「订单接口的字段含义」→ interface → 接口卡（`data_source:stream` assumption）→ persist；报表查询路由正确（单测覆盖，需 LLM key）。
- **设计修正**：`dict_stream_hit` 不硬路由 INTERFACE——`total_amount: 订单推送…` 是字段澄清查询（REPORT），非外部接口意图；字段来自推送源不构成接口意图。强接口关键词（接口/websocket/长连接/长轮询/外部服务/消息流/事件流）才硬判 INTERFACE；字典有命中即 REPORT（需求分析处理字段澄清）；无命中才 LLM 兜底。
- 离线全量 366 passed（+10 新测试：7 intent 单测 + 3 图路由）。

## Explicitly NOT doing

- **不做** 前端新事件类型/新卡片字段——复用 `report` 文本（闲聊）与既有 `RequirementCard` + `assumptions`（外部接口），前端零契约改动。
- **不做** 外部接口的完整取数/接入流程——本轮只把意图**识别**出来 + 给接口卡 + 确认短路不生成 SQL；真正接入实时数据源是后续。
- **不做** 看板/大屏意图的全流程——识别到 DASHBOARD 先按 REPORT 处理（保持现状），不新增看板渲染。
- **不做** 把 `requirement_analysis` 的 LLM 分类做成可配置开关——默认启用，收益明确。