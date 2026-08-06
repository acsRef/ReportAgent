# 数据字典 RAG 桥 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把表结构语义与接口字段字典灌入 ragent-py 的 RAG（经新增 MCP 桥），并让 ReportAgent 在报表流程中检索字段含义、不明时经 assumptions 通道澄清。

**Architecture:** HTTP 桥接——`ragent-py/mcp_server/`（stdio MCP）经 ragent-py 既有 REST 面（login/KB/upload/状态轮询）灌入确定性命名的 Markdown 字典文档；ragent-py 新增只检索不生成的 `POST /api/v1/retrieve`；ReportAgent 以 httpx 本地工具直连 `/retrieve`，澄清复用 RequirementCard assumptions 既有管线。

**Tech Stack:** Python 3.11 · FastAPI · `mcp` SDK（stdio）· httpx · psycopg2（只读自省）· pgvector 三路混合检索（ragent-py 既有）· LangChain `@tool`（ReportAgent）。

**设计依据（硬约束）:** [docs/plans/2026-08-06-rag-dictionary-mcp-bridge.md](../../plans/2026-08-06-rag-dictionary-mcp-bridge.md)——决策基线、错误路径枚举、「明确不做」清单以该文件为准。

**前置条件:**
- docker PG 已运行（`ragent` 库，用户已确认开启）。
- ragent-py 侧测试用 `D:/miniConda/envs/rag/python.exe -m pytest`（严禁用别的环境）；ReportAgent 侧 `cd backend && pytest`。
- 两仓库各自提交；ReportAgent commit 格式 `feat(<scope>): <标题> + plan: rag-dictionary-mcp-bridge`。
- ragent-py 单测离线铁律：`tests/conftest.py` 已把凭据哨兵化，任何误触真实 DB/网络的测试会立即失败。

---

## Phase A：ragent-py（/retrieve + mcp_server）

### Task A1: schemas 模型（RetrieveRequest / RetrieveResponse / RetrievedItem）

**Files:**
- Modify: `D:/PyProject/ragent-py/app/models/schemas.py`（文件尾部追加）
- Test: `D:/PyProject/ragent-py/tests/unit/test_retrieve_schemas.py`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_retrieve_schemas.py`:

```python
"""Retrieve 端点请求/响应模型的契约测试。"""
import pytest
from pydantic import ValidationError


def test_retrieve_request_defaults():
    from app.models.schemas import RetrieveRequest
    body = RetrieveRequest(query="销售额", kb_ids=["kb-1"])
    assert body.top_k == 5


def test_retrieve_request_rejects_empty_kb_ids():
    from app.models.schemas import RetrieveRequest
    with pytest.raises(ValidationError):
        RetrieveRequest(query="销售额", kb_ids=[])


def test_retrieve_request_top_k_bounds():
    from app.models.schemas import RetrieveRequest
    with pytest.raises(ValidationError):
        RetrieveRequest(query="q", kb_ids=["k"], top_k=0)
    with pytest.raises(ValidationError):
        RetrieveRequest(query="q", kb_ids=["k"], top_k=51)


def test_retrieve_response_shape():
    from app.models.schemas import RetrieveResponse, RetrievedItem
    item = RetrievedItem(chunk_id="c1", document_id="d1", text="正文",
                         title="t", section_path="s", score=0.5)
    resp = RetrieveResponse(items=[item], degraded=False)
    assert resp.items[0].score == 0.5
```

- [ ] **Step 2: 运行确认失败**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_retrieve_schemas.py -v
```

预期：FAIL（ImportError: cannot import name 'RetrieveRequest'）。

- [ ] **Step 3: 实现——追加到 `app/models/schemas.py` 尾部**

```python
# ── Retrieve（只检索不生成，供字典桥/外部消费） ────────────

class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4096)
    kb_ids: list[str] = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class RetrievedItem(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    title: str = ""
    section_path: str = ""
    score: float


class RetrieveResponse(BaseModel):
    items: list[RetrievedItem]
    degraded: bool = False
```

- [ ] **Step 4: 运行确认通过**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_retrieve_schemas.py -v
```

预期：4 passed。

- [ ] **Step 5: 提交**

```bash
cd D:/PyProject/ragent-py && git add app/models/schemas.py tests/unit/test_retrieve_schemas.py && git commit -m "feat(schemas): RetrieveRequest/Response/Item 模型（字典桥只检索端点契约）"
```

---

### Task A2: 抽取 embedding 熔断降级共享 helper

设计约束：`retrieval.py` 现有「CircuitOpenError/异常 → 零向量 → BM25-only；纯向量模式返回 []」逻辑抽成 `embed_query_with_fallback`，retrieve 端点复用，不复制粘贴。

**Files:**
- Modify: `D:/PyProject/ragent-py/app/core/retrieval.py`（在模块级新增 helper；`retrieve` 流程原调用点改为调用 helper，保留 `ctx.track_error` 记录）
- Test: `D:/PyProject/ragent-py/tests/unit/test_embed_fallback.py`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_embed_fallback.py`:

```python
"""embed_query_with_fallback：熔断/异常降级为零向量（BM25-only），纯向量模式返回 None。"""
import asyncio

import pytest

from app.config import settings
from app.llm.base import CircuitOpenError


def test_fallback_on_circuit_open(monkeypatch):
    from app.core import retrieval

    async def _boom(text, max_retries=1):
        raise CircuitOpenError("open")

    monkeypatch.setattr(retrieval.sf_embedding, "embed", _boom)
    monkeypatch.setattr(settings, "hybrid_search_enabled", True)
    emb, degraded = asyncio.run(retrieval.embed_query_with_fallback("q"))
    assert degraded is True
    assert emb == [0.0] * settings.embedding_dimension


def test_fallback_on_generic_error(monkeypatch):
    from app.core import retrieval

    async def _boom(text, max_retries=1):
        raise RuntimeError("429")

    monkeypatch.setattr(retrieval.sf_embedding, "embed", _boom)
    monkeypatch.setattr(settings, "hybrid_search_enabled", True)
    emb, degraded = asyncio.run(retrieval.embed_query_with_fallback("q"))
    assert degraded is True
    assert emb is not None


def test_pure_vector_mode_returns_none(monkeypatch):
    from app.core import retrieval

    async def _boom(text, max_retries=1):
        raise CircuitOpenError("open")

    monkeypatch.setattr(retrieval.sf_embedding, "embed", _boom)
    monkeypatch.setattr(settings, "hybrid_search_enabled", False)
    emb, degraded = asyncio.run(retrieval.embed_query_with_fallback("q"))
    assert degraded is True
    assert emb is None


def test_happy_path(monkeypatch):
    from app.core import retrieval

    async def _ok(text, max_retries=1):
        return [0.1] * 4

    monkeypatch.setattr(retrieval.sf_embedding, "embed", _ok)
    emb, degraded = asyncio.run(retrieval.embed_query_with_fallback("q"))
    assert degraded is False
    assert emb == [0.1] * 4
```

- [ ] **Step 2: 运行确认失败**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_embed_fallback.py -v
```

预期：FAIL（AttributeError: module 'app.core.retrieval' has no attribute 'embed_query_with_fallback'）。

- [ ] **Step 3: 实现——`app/core/retrieval.py` 新增模块级函数（放在现有检索函数之前）**

```python
async def embed_query_with_fallback(query: str) -> tuple[list[float] | None, bool]:
    """查询 embedding；熔断/失败时降级为零向量（BM25-only）。

    返回 (embedding, degraded)。embedding 为 None 表示纯向量模式下
    embedding 失败——零向量余弦排序未定义，调用方应返回空结果并交由
    上层兜底。
    """
    try:
        return await sf_embedding.embed(query), False
    except CircuitOpenError:
        logger.warning("embed_query degraded — circuit open, using zero-vector (BM25-only fallback)")
    except Exception:
        logger.warning("embed_query degraded — embedding failed, using zero-vector (BM25-only fallback)")
    if not settings.hybrid_search_enabled:
        return None, True
    return [0.0] * settings.embedding_dimension, True
```

然后把 `retrieve` 流程里原有约 20 行的内联 try/except 块（`t_embed = time.monotonic()` 到零向量赋值）替换为：

```python
        t_embed = time.monotonic()
        query_emb, embedding_degraded = await embed_query_with_fallback(query)
        embed_elapsed = (time.monotonic() - t_embed) * 1000
        if embedding_degraded:
            if ctx:
                reason = "circuit open / embedding failed, BM25-only" if query_emb is not None \
                    else "pure-vector mode with failed embedding returns []"
                ctx.track_error("embedding", "EmbeddingDegraded", reason, degraded=True)
        if query_emb is None:
            return []
```

（原有两段 `logger.warning` 已由 helper 内部承担；`round_data["embedding"]` 等后续逻辑不动。）

- [ ] **Step 4: 运行新测试 + 回归既有套件**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_embed_fallback.py tests/unit/test_search_breaker.py -v
```

预期：新测试 4 passed；test_search_breaker 无回归。

- [ ] **Step 5: 提交**

```bash
cd D:/PyProject/ragent-py && git add app/core/retrieval.py tests/unit/test_embed_fallback.py && git commit -m "refactor(retrieval): 抽取 embed_query_with_fallback 共享降级 helper"
```

---

### Task A3: `POST /api/v1/retrieve` 端点 + 挂载

**Files:**
- Create: `D:/PyProject/ragent-py/app/api/retrieve.py`
- Modify: `D:/PyProject/ragent-py/app/main.py`（import + `app.include_router(retrieve_router)`，与第 28-33 行同区）
- Test: `D:/PyProject/ragent-py/tests/unit/test_retrieve_api.py`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_retrieve_api.py`:

```python
"""/api/v1/retrieve：鉴权（visibility + read_all bypass）、kb_ids 隔离、降级标志、空结果。

离线：dependency_overrides 提供假用户；hybrid_search / KB 可读性判定 monkeypatch，
不触真实 DB 与 embedding 服务。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.retrieve import router
from app.middleware.auth import get_current_user


ADMIN = {"id": "u-admin", "is_admin": True, "permissions": ["doc.read_all"], "role_ids": []}
USER = {"id": "u-1", "is_admin": False, "permissions": [], "role_ids": [2]}


@pytest.fixture
def client(request):
    user = getattr(request, "param", ADMIN)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_layers(monkeypatch):
    import app.api.retrieve as mod
    monkeypatch.setattr(mod, "_assert_kb_readable", lambda session, user, kb_ids: None)

    async def _fake_embed(query):
        return [0.0] * 4, False
    monkeypatch.setattr(mod, "embed_query_with_fallback", _fake_embed)

    calls = {}

    def _fake_hybrid(**kwargs):
        calls.update(kwargs)
        return [{"chunk_id": "c1", "document_id": "d1", "text": "字典正文",
                 "title": "dict-table_public_fact_sales.md", "section_path": "字段", "score": 0.9}]
    monkeypatch.setattr(mod, "hybrid_search", _fake_hybrid)
    return calls


def test_retrieve_happy_path(client, _stub_layers):
    resp = client.post("/api/v1/retrieve", json={"query": "销售额", "kb_ids": ["kb-dict"], "top_k": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is False
    assert body["items"][0]["document_id"] == "d1"
    # kb_ids 必须原样 pin 进 hybrid_search（隔离保证）
    assert _stub_layers["kb_ids"] == ["kb-dict"]
    assert _stub_layers["top_k"] == 3


@pytest.mark.parametrize("client", [USER], indirect=True)
def test_retrieve_forbidden_kb(client, monkeypatch, _stub_layers):
    import app.api.retrieve as mod
    from fastapi import HTTPException

    def _deny(session, user, kb_ids):
        raise HTTPException(status_code=403, detail=f"无权读取知识库: {kb_ids[0]}")
    monkeypatch.setattr(mod, "_assert_kb_readable", _deny)
    resp = client.post("/api/v1/retrieve", json={"query": "q", "kb_ids": ["kb-x"]})
    assert resp.status_code == 403


def test_retrieve_degraded_flag(client, monkeypatch, _stub_layers):
    import app.api.retrieve as mod

    async def _degraded(query):
        return [0.0] * 4, True
    monkeypatch.setattr(mod, "embed_query_with_fallback", _degraded)
    resp = client.post("/api/v1/retrieve", json={"query": "q", "kb_ids": ["kb-dict"]})
    assert resp.status_code == 200
    assert resp.json()["degraded"] is True


def test_retrieve_pure_vector_none_returns_empty(client, monkeypatch, _stub_layers):
    import app.api.retrieve as mod

    async def _none(query):
        return None, True
    monkeypatch.setattr(mod, "embed_query_with_fallback", _none)
    resp = client.post("/api/v1/retrieve", json={"query": "q", "kb_ids": ["kb-dict"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["degraded"] is True


def test_retrieve_empty_result_semantics(client, monkeypatch, _stub_layers):
    import app.api.retrieve as mod
    monkeypatch.setattr(mod, "hybrid_search", lambda **kw: [])
    resp = client.post("/api/v1/retrieve", json={"query": "不存在的字段", "kb_ids": ["kb-dict"]})
    assert resp.status_code == 200
    assert resp.json()["items"] == []
```

- [ ] **Step 2: 运行确认失败**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_retrieve_api.py -v
```

预期：FAIL（ModuleNotFoundError: app.api.retrieve）。

- [ ] **Step 3: 实现 `app/api/retrieve.py`**

```python
"""只检索不生成端点：数据字典桥与外部消费方的检索入口。

与 /chat/stream 的区别：不走意图识别/改写/cross-doc/重排/MMR，
kb_ids 由调用方 pin 死，hybrid_search 直调——防止跨知识库污染。
鉴权：admin / doc.read_all bypass；否则按 KB visibility + 角色访问判定
（与 list_kb 语义一致）。
"""
from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.core.retrieval import embed_query_with_fallback
from app.middleware.auth import get_current_user
from app.models.schemas import RetrieveRequest, RetrieveResponse, RetrievedItem
from app.store.db import KBRoleAccess, KnowledgeBase, get_session
from app.store.pgvector_store import hybrid_search

router = APIRouter(prefix="/api/v1/retrieve", tags=["retrieve"])


def _assert_kb_readable(session, user: dict, kb_ids: list[str]) -> None:
    """逐 KB 判定可读性；任一无权 → 403 并指明 kb_id。"""
    if user["is_admin"] or "doc.read_all" in user["permissions"]:
        return
    for kb_id in kb_ids:
        kb = session.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if not kb:
            raise HTTPException(status_code=403, detail=f"知识库不存在或不可读: {kb_id}")
        if kb.visibility == "public" or kb.owner_id == user["id"]:
            continue
        role_ids = user.get("role_ids") or []
        hit = (
            session.query(KBRoleAccess)
            .filter(KBRoleAccess.kb_id == kb_id, KBRoleAccess.role_id.in_(role_ids or [-1]))
            .first()
            if role_ids else None
        )
        if not hit:
            raise HTTPException(status_code=403, detail=f"无权读取知识库: {kb_id}")


@router.post("", response_model=RetrieveResponse)
async def retrieve(body: RetrieveRequest, current_user: dict = Depends(get_current_user)):
    session = get_session()
    try:
        _assert_kb_readable(session, current_user, body.kb_ids)
    finally:
        session.close()

    query_emb, degraded = await embed_query_with_fallback(body.query)
    if query_emb is None:
        return RetrieveResponse(items=[], degraded=True)

    rows = hybrid_search(
        kb_ids=body.kb_ids,
        embedding=query_emb,
        query=body.query,
        user_role_ids=current_user.get("role_ids"),
        can_read_all=current_user["is_admin"] or "doc.read_all" in current_user["permissions"],
        top_k=body.top_k,
        enable_question_channel=settings.question_channel_enabled,
        user_id=current_user["id"],
    )
    items = [
        RetrievedItem(
            chunk_id=r.get("chunk_id", ""),
            document_id=r.get("document_id", ""),
            text=r.get("text", ""),
            title=r.get("title", ""),
            section_path=r.get("section_path", ""),
            score=float(r.get("score", 0.0)),
        )
        for r in rows
    ]
    return RetrieveResponse(items=items, degraded=degraded)
```

- [ ] **Step 4: 挂载——`app/main.py`**

在 `from app.api.diagnostics import router as diag_router` 之后加：

```python
from app.api.retrieve import router as retrieve_router
```

在 `app.include_router(diag_router)` 之后加：

```python
app.include_router(retrieve_router)
```

- [ ] **Step 5: 运行测试 + 导入链**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_retrieve_api.py -v && D:/miniConda/envs/rag/python.exe -c "import app.main"
```

预期：5 passed；导入无异常。

- [ ] **Step 6: 提交**

```bash
cd D:/PyProject/ragent-py && git add app/api/retrieve.py app/main.py tests/unit/test_retrieve_api.py && git commit -m "feat(api): POST /api/v1/retrieve 只检索端点（字典桥消费面，visibility 鉴权 + kb_ids pin）"
```

---

### Task A4: `mcp_server/render.py`——字典文档渲染（唯一写入格式处）

**Files:**
- Create: `D:/PyProject/ragent-py/mcp_server/__init__.py`（空文件）
- Create: `D:/PyProject/ragent-py/mcp_server/render.py`
- Test: `D:/PyProject/ragent-py/tests/unit/test_dict_render.py`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_dict_render.py`:

```python
"""数据字典 Markdown 渲染：文件名幂等键 + 表文档 + 接口文档（含长连接分节）。"""
from mcp_server.render import (
    api_filename,
    render_api_doc,
    render_table_doc,
    table_filename,
)


def test_filenames_are_idempotency_keys():
    assert table_filename("public", "fact_sales") == "dict-table_public_fact_sales.md"
    assert api_filename("orders-push") == "dict-api_orders-push.md"


def test_render_table_doc_contains_comment_and_enums():
    md = render_table_doc(
        schema="public", table="fact_sales",
        table_comment="销售记录事实表",
        columns=[
            {"name": "sale_id", "type": "integer", "comment": "销售记录主键", "enums": None, "fk": None},
            {"name": "channel", "type": "character varying(10)", "comment": "销售渠道",
             "enums": ["线上", "线下"], "fk": None},
            {"name": "date_id", "type": "integer", "comment": "销售日期", "enums": None,
             "fk": "public.dim_date.date_id"},
        ],
    )
    assert "# 表 `public.fact_sales`" in md
    assert "销售记录事实表" in md
    assert "| channel | character varying(10) | 销售渠道 |" in md
    assert "线上 / 线下" in md
    assert "FK → public.dim_date.date_id" in md


def test_render_api_doc_http():
    md = render_api_doc(
        name="orders", description="订单查询接口", protocol="http",
        endpoint="GET /v1/orders", auth="Bearer",
        fields=[
            {"name": "order_id", "type": "string", "required": True, "desc": "订单号", "example": "SO-1"},
            {"name": "amt", "type": "number", "required": False, "desc": "订单金额", "example": "99.5"},
        ],
    )
    assert "# 接口字典: orders" in md
    assert "接口类型: HTTP 请求/响应" in md
    assert "| amt | number | 否 | 订单金额 | 99.5 |" in md


def test_render_api_doc_websocket_message_grouping():
    md = render_api_doc(
        name="market-push", description="行情长连接", protocol="websocket",
        endpoint="wss://example.com/push", auth="",
        fields=[
            {"name": "price", "type": "number", "required": True, "desc": "最新价",
             "example": "12.3", "message": "on_message"},
            {"name": "hb", "type": "string", "required": False, "desc": "心跳标识",
             "example": "ping", "message": "heartbeat"},
        ],
    )
    assert "接口类型: WebSocket 长连接" in md
    assert "消息 `on_message` 字段" in md
    assert "消息 `heartbeat` 字段" in md
```

- [ ] **Step 2: 运行确认失败**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_dict_render.py -v
```

预期：FAIL（ModuleNotFoundError: mcp_server.render）。

- [ ] **Step 3: 实现**

`mcp_server/__init__.py`：空文件。

`mcp_server/render.py`:

```python
"""数据字典 Markdown 渲染——唯一写入格式的地方。

文件名即幂等键：ragent-py 上传按「同 kb 同名」复用 document_id，
内容 hash 未变的 chunk 复用既有 embedding（增量摄入管线既有能力）。
"""
from __future__ import annotations

_PROTOCOL_LABELS = {
    "http": "HTTP 请求/响应",
    "websocket": "WebSocket 长连接",
    "sse": "SSE 服务端推送",
    "long_poll": "长轮询",
}


def table_filename(schema: str, table: str) -> str:
    return f"dict-table_{schema}_{table}.md"


def api_filename(name: str) -> str:
    return f"dict-api_{name}.md"


def render_table_doc(*, schema: str, table: str, table_comment: str, columns: list[dict]) -> str:
    """columns 每项: {name, type, comment, enums: list|None, fk: str|None}"""
    lines = [f"# 表 `{schema}.{table}`", ""]
    if table_comment:
        lines += [table_comment, ""]
    lines += ["## 字段", "", "| 字段 | 类型 | 含义 | 枚举/FK |", "|---|---|---|---|"]
    for c in columns:
        extra = []
        if c.get("fk"):
            extra.append(f"FK → {c['fk']}")
        if c.get("enums"):
            extra.append("枚举值: " + " / ".join(str(v) for v in c["enums"]))
        lines.append(f"| {c['name']} | {c['type']} | {c.get('comment') or ''} | {'; '.join(extra)} |")
    return "\n".join(lines) + "\n"


def render_api_doc(*, name: str, description: str = "", protocol: str = "http",
                   endpoint: str = "", auth: str = "", fields: list[dict]) -> str:
    """fields 每项: {name, type, required, desc, example, message?: str}

    protocol ∈ http/websocket/sse/long_poll；流式接口的字段用 message
    归属到具体消息/事件类型分节。帧时序/心跳/重连语义不进 v1。
    """
    lines = [f"# 接口字典: {name}", ""]
    lines.append(f"- 接口类型: {_PROTOCOL_LABELS.get(protocol, protocol)}")
    if endpoint:
        lines.append(f"- 地址: `{endpoint}`")
    if auth:
        lines.append(f"- 认证: {auth}")
    if description:
        lines += ["", description]

    by_message: dict[str, list[dict]] = {}
    for f in fields:
        by_message.setdefault(f.get("message") or "", []).append(f)
    for msg, fs in by_message.items():
        title = "字段" if not msg else f"消息 `{msg}` 字段"
        lines += ["", f"## {title}", "",
                  "| 字段 | 类型 | 必填 | 含义 | 示例 |", "|---|---|---|---|---|"]
        for f in fs:
            req = "是" if f.get("required") else "否"
            lines.append(
                f"| {f['name']} | {f.get('type', '')} | {req} | {f.get('desc', '')} | {f.get('example', '')} |"
            )
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: 运行确认通过**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_dict_render.py -v
```

预期：4 passed。

- [ ] **Step 5: 提交**

```bash
cd D:/PyProject/ragent-py && git add mcp_server/__init__.py mcp_server/render.py tests/unit/test_dict_render.py && git commit -m "feat(mcp): 数据字典 Markdown 渲染（表结构 + 接口字典，含长连接分节）"
```

---

### Task A5: `mcp_server/introspect.py`——PG 只读自省

**Files:**
- Create: `D:/PyProject/ragent-py/mcp_server/introspect.py`
- Test: `D:/PyProject/ragent-py/tests/unit/test_dict_introspect.py`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_dict_introspect.py`:

```python
"""PG 自省：fake cursor 驱动——列注释、FK、低基数枚举采样、表过滤。"""
import pytest


class FakeCursor:
    """按执行顺序回放预设结果集；记录 SQL 供断言。"""

    def __init__(self, results: list[list[tuple]]):
        self._results = list(results)
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params or ()))

    def fetchall(self):
        return self._results.pop(0)

    def fetchone(self):
        rows = self._results.pop(0)
        return rows[0] if rows else None


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def test_introspect_builds_columns_with_comment_enum_fk(monkeypatch):
    import mcp_server.introspect as mod

    cur = FakeCursor([
        [("fact_sales",)],                                        # tables 列表
        [("销售记录事实表",)],                                    # obj_description
        [                                                          # pg_attribute 列
            ("sale_id", "integer", "销售记录主键", "int4"),
            ("channel", "character varying(10)", "销售渠道", "varchar"),
            ("date_id", "integer", "销售日期", "int4"),
        ],
        [],                                                        # sale_id 无 FK
        None,                                                      # sale_id 非 FK → 枚举采样: distinct 超限(None 表示 >20)
        [("public.dim_date.date_id",)],                            # date_id 的 FK 命中
        [("线上",), ("线下",)],                                    # channel 枚举采样
    ])
    monkeypatch.setattr(mod.psycopg2, "connect", lambda dsn, connect_timeout=5: FakeConn(cur))

    infos = mod.introspect_schema("postgresql://fake", "public")
    assert len(infos) == 1
    info = infos[0]
    assert info["table"] == "fact_sales"
    assert info["table_comment"] == "销售记录事实表"
    by_name = {c["name"]: c for c in info["columns"]}
    assert by_name["channel"]["enums"] == ["线上", "线下"]
    assert by_name["date_id"]["fk"] == "public.dim_date.date_id"
    assert by_name["sale_id"]["enums"] is None


def test_introspect_table_filter(monkeypatch):
    import mcp_server.introspect as mod

    cur = FakeCursor([
        [("fact_sales",), ("dim_date",)],   # tables 列表（过滤发生在 Python 侧）
        [("销售记录事实表",)],
        [],                                  # 无列（简化）
    ])
    monkeypatch.setattr(mod.psycopg2, "connect", lambda dsn, connect_timeout=5: FakeConn(cur))
    infos = mod.introspect_schema("postgresql://fake", "public", tables=["fact_sales"])
    assert [i["table"] for i in infos] == ["fact_sales"]
```

- [ ] **Step 2: 运行确认失败**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_dict_introspect.py -v
```

预期：FAIL（ModuleNotFoundError: mcp_server.introspect）。

- [ ] **Step 3: 实现 `mcp_server/introspect.py`**

```python
"""PG 结构自省（只读连接）：类型/FK 来自 information_schema 系视图，
语义来自 COMMENT ON（col_description/obj_description）。

低基数枚举采样：白名单类型 + distinct ≤20；不带自由样例（避 PII 红线）。
"""
from __future__ import annotations

import psycopg2
from psycopg2 import sql as psql

_ENUM_BASE_TYPES = {"varchar", "bpchar", "text", "bool", "int2", "int4"}
_ENUM_MAX_DISTINCT = 20


def introspect_schema(dsn: str, schema: str = "public", tables: list[str] | None = None) -> list[dict]:
    conn = psycopg2.connect(dsn, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
                (schema,),
            )
            names = [r[0] for r in cur.fetchall()]
            if tables:
                wanted = set(tables)
                names = [t for t in names if t in wanted]
            return [_introspect_table(cur, schema, t) for t in names]
    finally:
        conn.close()


def _introspect_table(cur, schema: str, table: str) -> dict:
    cur.execute("SELECT obj_description(%s::regclass, 'pg_class')", (f"{schema}.{table}",))
    row = cur.fetchone()
    table_comment = (row[0] if row else "") or ""

    cur.execute(
        """
        SELECT a.attname,
               format_type(a.atttypid, a.atttypmod),
               col_description(a.attrelid, a.attnum),
               t.typname
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_type t ON t.oid = a.atttypid
        WHERE n.nspname = %s AND c.relname = %s
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
        (schema, table),
    )
    columns = []
    for name, col_type, comment, typname in cur.fetchall():
        col = {"name": name, "type": col_type, "comment": comment or "", "enums": None, "fk": None}
        fk = _fk_target(cur, schema, table, name)
        if fk:
            col["fk"] = fk
        elif typname in _ENUM_BASE_TYPES:
            col["enums"] = _sample_distinct(cur, schema, table, name)
        columns.append(col)
    return {"schema": schema, "table": table, "table_comment": table_comment, "columns": columns}


def _fk_target(cur, schema: str, table: str, column: str) -> str | None:
    cur.execute(
        """
        SELECT ccu.table_schema, ccu.table_name, ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = %s AND tc.table_name = %s AND kcu.column_name = %s
        LIMIT 1
        """,
        (schema, table, column),
    )
    row = cur.fetchone()
    return f"{row[0]}.{row[1]}.{row[2]}" if row else None


def _sample_distinct(cur, schema: str, table: str, column: str) -> list | None:
    """distinct ≤20 → 返回枚举值；超限 → None（视为自由值列，不采样）。"""
    query = psql.SQL(
        "SELECT DISTINCT {col} FROM {tbl} WHERE {col} IS NOT NULL LIMIT %s"
    ).format(col=psql.Identifier(column), tbl=psql.Identifier(schema, table))
    cur.execute(query, (_ENUM_MAX_DISTINCT + 1,))
    vals = [r[0] for r in cur.fetchall()]
    if len(vals) > _ENUM_MAX_DISTINCT:
        return None
    return vals
```

- [ ] **Step 4: 运行确认通过**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_dict_introspect.py -v
```

预期：2 passed。

- [ ] **Step 5: 提交**

```bash
cd D:/PyProject/ragent-py && git add mcp_server/introspect.py tests/unit/test_dict_introspect.py && git commit -m "feat(mcp): PG 只读自省（COMMENT 语义 + FK + 低基数枚举采样）"
```

---

### Task A6: `mcp_server/client.py`——ragent-py HTTP 客户端

**Files:**
- Create: `D:/PyProject/ragent-py/mcp_server/client.py`
- Test: `D:/PyProject/ragent-py/tests/unit/test_dict_client.py`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_dict_client.py`:

```python
"""RagentClient：登录/401 重登、KB ensure 按名复用、上传、状态轮询、retrieve。

httpx MockTransport 全离线。
"""
import asyncio
import json

import httpx
import pytest

from mcp_server.client import RagentClient, RagentClientError


def _handler(routes: dict):
    def handle(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key not in routes:
            return httpx.Response(404, json={"detail": "not found"})
        resp = routes[key]
        return resp(request) if callable(resp) else resp
    return handle


def _client(handler) -> RagentClient:
    c = RagentClient(base_url="http://fake", username="admin", password="admin123")
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://fake")
    return c


def test_login_failure_message():
    async def run():
        c = _client(_handler({
            ("POST", "/api/v1/auth/login"): httpx.Response(401, json={"detail": "bad"}),
        }))
        try:
            with pytest.raises(RagentClientError, match="登录失败"):
                await c.ensure_kb("数据字典")
        finally:
            await c.aclose()
    asyncio.run(run())


def test_ensure_kb_reuses_existing_by_name():
    async def run():
        c = _client(_handler({
            ("POST", "/api/v1/auth/login"): httpx.Response(200, json={"access_token": "t"}),
            ("GET", "/api/v1/kb"): httpx.Response(200, json=[{"id": "kb-9", "name": "数据字典"}]),
        }))
        try:
            kb_id = await c.ensure_kb("数据字典")
            assert kb_id == "kb-9"
        finally:
            await c.aclose()
    asyncio.run(run())


def test_ensure_kb_creates_when_absent():
    async def run():
        c = _client(_handler({
            ("POST", "/api/v1/auth/login"): httpx.Response(200, json={"access_token": "t"}),
            ("GET", "/api/v1/kb"): httpx.Response(200, json=[]),
            ("POST", "/api/v1/kb"): httpx.Response(200, json={"id": "kb-new", "name": "数据字典"}),
        }))
        try:
            assert await c.ensure_kb("数据字典") == "kb-new"
        finally:
            await c.aclose()
    asyncio.run(run())


def test_upload_then_wait_indexed():
    state = {"calls": 0}

    def status(request):
        state["calls"] += 1
        if state["calls"] < 2:
            return httpx.Response(200, json={"document_id": "d1", "status": "processing", "chunk_count": 0})
        return httpx.Response(200, json={"document_id": "d1", "status": "indexed", "chunk_count": 3})

    async def run():
        c = _client(_handler({
            ("POST", "/api/v1/auth/login"): httpx.Response(200, json={"access_token": "t"}),
            ("POST", "/api/v1/documents/upload"): httpx.Response(
                200, json={"document_id": "d1", "filename": "dict-api_x.md", "status": "processing", "chunk_count": 0}),
            ("GET", "/api/v1/documents/d1"): status,
        }))
        try:
            up = await c.upload_document("kb-9", "dict-api_x.md", "# x")
            doc = await c.wait_indexed(up["document_id"], interval_s=0.01)
            assert doc["status"] == "indexed"
            assert doc["chunk_count"] == 3
        finally:
            await c.aclose()
    asyncio.run(run())


def test_retrieve_passes_payload():
    def check(request):
        body = json.loads(request.content)
        assert body["kb_ids"] == ["kb-9"]
        return httpx.Response(200, json={"items": [], "degraded": False})

    async def run():
        c = _client(_handler({
            ("POST", "/api/v1/auth/login"): httpx.Response(200, json={"access_token": "t"}),
            ("POST", "/api/v1/retrieve"): check,
        }))
        try:
            out = await c.retrieve("销售额", ["kb-9"], top_k=3)
            assert out["items"] == []
        finally:
            await c.aclose()
    asyncio.run(run())
```

- [ ] **Step 2: 运行确认失败**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_dict_client.py -v
```

预期：FAIL（ModuleNotFoundError: mcp_server.client）。

- [ ] **Step 3: 实现 `mcp_server/client.py`**

```python
"""ragent-py HTTP 客户端：JWT 登录（401 重登一次）、KB 按名 ensure、
确定性文件名上传、摄入状态轮询、/retrieve 调用。

凭据只从构造函数/env 读——MCP 工具入参永不携带凭据。
"""
from __future__ import annotations

import asyncio
import os

import httpx


class RagentClientError(RuntimeError):
    """面向 MCP 工具调用方的可读错误——直接作为工具返回文本。"""


class RagentClient:
    def __init__(self, base_url: str = "", username: str = "", password: str = "", timeout: float = 30.0):
        self.base_url = (base_url or os.getenv("RAGENT_URL", "http://localhost:8000")).rstrip("/")
        self.username = username or os.getenv("RAGENT_USER", "")
        self.password = password or os.getenv("RAGENT_PASSWORD", "")
        self._token: str | None = None
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _login(self) -> None:
        try:
            resp = await self._http.post(
                "/api/v1/auth/login",
                json={"username": self.username, "password": self.password},
            )
        except httpx.HTTPError as exc:
            raise RagentClientError(f"ragent-py 服务不可达（{self.base_url}）: {exc}") from exc
        if resp.status_code == 401:
            raise RagentClientError("登录失败：请检查 RAGENT_USER / RAGENT_PASSWORD")
        if resp.status_code != 200:
            raise RagentClientError(f"登录异常：HTTP {resp.status_code}")
        self._token = resp.json()["access_token"]

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self._token:
            await self._login()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._token}"
        try:
            resp = await self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise RagentClientError(f"ragent-py 服务不可达（{self.base_url}）: {exc}") from exc
        if resp.status_code == 401:  # token 过期 → 重登一次
            self._token = None
            await self._login()
            headers["Authorization"] = f"Bearer {self._token}"
            try:
                resp = await self._http.request(method, path, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                raise RagentClientError(f"ragent-py 服务不可达（{self.base_url}）: {exc}") from exc
        return resp

    async def ensure_kb(self, name: str, visibility: str = "internal") -> str:
        resp = await self._request("GET", "/api/v1/kb")
        if resp.status_code != 200:
            raise RagentClientError(f"知识库列表获取失败：HTTP {resp.status_code}")
        for kb in resp.json():
            if kb.get("name") == name:
                return kb["id"]
        resp = await self._request("POST", "/api/v1/kb", json={"name": name, "visibility": visibility})
        if resp.status_code == 403:
            raise RagentClientError("服务账号缺少 kb.create 权限")
        if resp.status_code != 200:
            raise RagentClientError(f"知识库创建失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()["id"]

    async def upload_document(self, kb_id: str, filename: str, content: str) -> dict:
        files = {"file": (filename, content.encode("utf-8"), "text/markdown")}
        resp = await self._request("POST", "/api/v1/documents/upload",
                                   files=files, data={"kb_id": kb_id})
        if resp.status_code != 200:
            raise RagentClientError(f"上传失败 {filename}：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def wait_indexed(self, document_id: str, timeout_s: float = 180.0, interval_s: float = 2.0) -> dict:
        remaining = timeout_s
        while remaining > 0:
            resp = await self._request("GET", f"/api/v1/documents/{document_id}")
            if resp.status_code == 200:
                doc = resp.json()
                if doc.get("status") in ("indexed", "failed"):
                    return doc
            await asyncio.sleep(interval_s)
            remaining -= interval_s
        return {"document_id": document_id, "status": "processing", "chunk_count": 0,
                "error_message": f"轮询超时（{timeout_s:.0f}s），请用 GET /api/v1/documents/{document_id} 复查"}

    async def retrieve(self, query: str, kb_ids: list[str], top_k: int = 5) -> dict:
        resp = await self._request("POST", "/api/v1/retrieve",
                                   json={"query": query, "kb_ids": kb_ids, "top_k": top_k})
        if resp.status_code == 401:
            raise RagentClientError("登录失败：请检查 RAGENT_USER / RAGENT_PASSWORD")
        if resp.status_code == 403:
            raise RagentClientError(f"无权读取字典知识库：{resp.text[:200]}")
        if resp.status_code != 200:
            raise RagentClientError(f"检索失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def list_documents(self, kb_id: str, limit: int = 200) -> list[dict]:
        """GET /documents 无 kb_id 参数——取一页后客户端侧过滤。"""
        resp = await self._request("GET", "/api/v1/documents", params={"limit": limit})
        if resp.status_code != 200:
            raise RagentClientError(f"文档列表获取失败：HTTP {resp.status_code}")
        return [d for d in resp.json() if d.get("kb_id") == kb_id]
```

- [ ] **Step 4: 运行确认通过**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_dict_client.py -v
```

预期：5 passed。

- [ ] **Step 5: 提交**

```bash
cd D:/PyProject/ragent-py && git add mcp_server/client.py tests/unit/test_dict_client.py && git commit -m "feat(mcp): ragent-py HTTP 客户端（登录重登/KB ensure/上传/轮询/retrieve）"
```

---

### Task A7: `mcp_server/server.py`——MCP 工具面 + requirements

**Files:**
- Create: `D:/PyProject/ragent-py/mcp_server/server.py`
- Create: `D:/PyProject/ragent-py/mcp_server/requirements.txt`
- Test: `D:/PyProject/ragent-py/tests/unit/test_dict_server.py`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_dict_server.py`:

```python
"""MCP 工具分发：stub 掉 client/introspect，只测 server 编排与错误契约。"""
import asyncio
import json

import pytest

mcp = pytest.importorskip("mcp")  # mcp SDK 未安装时跳过本文件

from mcp_server import server as srv
from mcp_server.client import RagentClientError


class FakeClient:
    def __init__(self, kb_id="kb-9", upload=None, wait=None, retrieve=None, docs=None):
        self.kb_id = kb_id
        self._upload = upload or {"document_id": "d1", "filename": "f.md", "status": "processing", "chunk_count": 0}
        self._wait = wait or {"document_id": "d1", "status": "indexed", "chunk_count": 2}
        self._retrieve = retrieve or {"items": [], "degraded": False}
        self._docs = docs if docs is not None else []

    async def ensure_kb(self, name, visibility="internal"):
        return self.kb_id

    async def upload_document(self, kb_id, filename, content):
        self.last_upload = (filename, content)
        return dict(self._upload)

    async def wait_indexed(self, document_id, timeout_s=180.0, interval_s=2.0):
        return dict(self._wait)

    async def retrieve(self, query, kb_ids, top_k=5):
        return dict(self._retrieve)

    async def list_documents(self, kb_id, limit=200):
        return list(self._docs)

    async def aclose(self):
        pass


def test_ingest_table_schemas_happy(monkeypatch):
    monkeypatch.setenv("DICT_PG_DSN", "postgresql://fake")
    monkeypatch.setattr(srv, "introspect_schema", lambda dsn, schema, tables=None: [{
        "schema": "public", "table": "fact_sales", "table_comment": "销售",
        "columns": [{"name": "sale_id", "type": "integer", "comment": "主键", "enums": None, "fk": None}],
    }])
    fake = FakeClient()
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = json.loads(asyncio.run(srv.cmd_ingest_table_schemas({"schema": "public"})))
    assert out[0]["status"] == "indexed"
    assert fake.last_upload[0] == "dict-table_public_fact_sales.md"


def test_ingest_table_schemas_missing_dsn(monkeypatch):
    monkeypatch.delenv("DICT_PG_DSN", raising=False)
    out = asyncio.run(srv.cmd_ingest_table_schemas({}))
    assert "DICT_PG_DSN" in out


def test_ingest_ragent_error_is_text(monkeypatch):
    monkeypatch.setenv("DICT_PG_DSN", "postgresql://fake")
    monkeypatch.setattr(srv, "introspect_schema", lambda dsn, schema, tables=None: [
        {"schema": "public", "table": "t", "table_comment": "", "columns": []}])

    class Boom(FakeClient):
        async def ensure_kb(self, name, visibility="internal"):
            raise RagentClientError("登录失败：请检查 RAGENT_USER / RAGENT_PASSWORD")

    monkeypatch.setattr(srv, "RagentClient", lambda: Boom())
    out = asyncio.run(srv.cmd_ingest_table_schemas({}))
    assert "登录失败" in out


def test_upsert_api_dictionary_renders_and_uploads(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = json.loads(asyncio.run(srv.cmd_upsert_api_dictionary({
        "name": "orders", "description": "订单接口",
        "fields": [{"name": "amt", "type": "number", "required": True, "desc": "金额"}],
    })))
    assert out["status"] == "indexed"
    fname, content = fake.last_upload
    assert fname == "dict-api_orders.md"
    assert "| amt | number | 是 | 金额 |" in content


def test_search_dictionary_empty_semantics(monkeypatch):
    fake = FakeClient(kb_id="kb-9", retrieve={"items": [], "degraded": False})
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = asyncio.run(srv.cmd_search_dictionary({"query": "不存在", "top_k": 3}))
    assert "字典库无匹配" in out


def test_list_dictionary_docs(monkeypatch):
    fake = FakeClient(docs=[{"document_id": "d1", "filename": "dict-table_public_fact_sales.md",
                             "status": "indexed", "kb_id": "kb-9", "chunk_count": 2}])
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = json.loads(asyncio.run(srv.cmd_list_dictionary_docs({})))
    assert out[0]["filename"].startswith("dict-")


def test_mcp_tool_surface():
    tools = asyncio.run(srv.handle_list_tools())
    names = {t.name for t in tools}
    assert names == {"ingest_table_schemas", "upsert_api_dictionary", "search_dictionary", "list_dictionary_docs"}
```

- [ ] **Step 2: 运行确认失败**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_dict_server.py -v
```

预期：FAIL（ImportError 或 skip——若 skip，先 `D:/miniConda/envs/rag/python.exe -m pip install "mcp>=1.0.0"` 再跑，应 FAIL 于 ImportError）。

- [ ] **Step 3: 实现 `mcp_server/server.py`**

```python
"""ragent-py 数据字典 MCP 服务（stdio）。

启动: D:/miniConda/envs/rag/python.exe -m mcp_server.server
配置（env，绝不进工具入参）:
  RAGENT_URL        ragent-py 地址，默认 http://localhost:8000
  RAGENT_USER / RAGENT_PASSWORD   服务账号（需 kb.create / doc.upload / doc.read_all）
  DICT_PG_DSN       自省用只读 PG 连接串
  DICT_KB_NAME      字典知识库名，默认「数据字典」

Claude Code 配置示例（mcpServers）:
  "ragent-dictionary": {
    "command": "D:/miniConda/envs/rag/python.exe",
    "args": ["-m", "mcp_server.server"],
    "cwd": "D:/PyProject/ragent-py",
    "env": {"RAGENT_USER": "...", "RAGENT_PASSWORD": "...", "DICT_PG_DSN": "..."}
  }
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.types import TextContent, Tool

from mcp_server.client import RagentClient, RagentClientError
from mcp_server.introspect import introspect_schema
from mcp_server.render import api_filename, render_api_doc, render_table_doc, table_filename

server = Server("ragent-dictionary")


def _kb_name() -> str:
    return os.getenv("DICT_KB_NAME", "数据字典")


# ── 工具实现（模块级函数，便于单测 stub） ─────────────────────


async def cmd_ingest_table_schemas(arguments: dict) -> str:
    dsn = os.getenv("DICT_PG_DSN", "")
    if not dsn:
        return "DICT_PG_DSN 未配置，无法自省数据库"
    schema = arguments.get("schema") or "public"
    tables = arguments.get("tables") or None
    try:
        infos = await asyncio.to_thread(introspect_schema, dsn, schema, tables)
    except Exception as exc:
        return f"数据库自省失败: {exc}"
    client = RagentClient()
    try:
        kb_id = await client.ensure_kb(_kb_name())
        results = []
        for info in infos:
            fname = table_filename(info["schema"], info["table"])
            md = render_table_doc(schema=info["schema"], table=info["table"],
                                  table_comment=info["table_comment"], columns=info["columns"])
            up = await client.upload_document(kb_id, fname, md)
            doc = await client.wait_indexed(up["document_id"])
            results.append({
                "table": f"{info['schema']}.{info['table']}",
                "filename": fname,
                "document_id": doc.get("document_id", up["document_id"]),
                "status": doc.get("status", "unknown"),
                "chunk_count": doc.get("chunk_count", 0),
                "error": doc.get("error_message", ""),
            })
        return json.dumps(results, ensure_ascii=False, indent=2)
    except RagentClientError as exc:
        return str(exc)
    finally:
        await client.aclose()


async def cmd_upsert_api_dictionary(arguments: dict) -> str:
    name = (arguments.get("name") or "").strip()
    if not name:
        return "缺少必填参数 name"
    fields = arguments.get("fields") or []
    if not fields:
        return "缺少必填参数 fields（至少一个字段）"
    md = render_api_doc(
        name=name,
        description=arguments.get("description", ""),
        protocol=arguments.get("protocol", "http"),
        endpoint=arguments.get("endpoint", ""),
        auth=arguments.get("auth", ""),
        fields=fields,
    )
    client = RagentClient()
    try:
        kb_id = await client.ensure_kb(_kb_name())
        fname = api_filename(name)
        up = await client.upload_document(kb_id, fname, md)
        doc = await client.wait_indexed(up["document_id"])
        return json.dumps({
            "name": name,
            "filename": fname,
            "document_id": doc.get("document_id", up["document_id"]),
            "status": doc.get("status", "unknown"),
            "chunk_count": doc.get("chunk_count", 0),
            "error": doc.get("error_message", ""),
        }, ensure_ascii=False, indent=2)
    except RagentClientError as exc:
        return str(exc)
    finally:
        await client.aclose()


async def cmd_search_dictionary(arguments: dict) -> str:
    query = (arguments.get("query") or "").strip()
    if not query:
        return "缺少必填参数 query"
    top_k = int(arguments.get("top_k") or 5)
    client = RagentClient()
    try:
        kb_id = await client.ensure_kb(_kb_name())
        data = await client.retrieve(query, [kb_id], top_k=top_k)
        items = data.get("items", [])
        if not items:
            return f"字典库无匹配：{query}"
        return json.dumps({"matches": items, "degraded": data.get("degraded", False)},
                          ensure_ascii=False, indent=2)
    except RagentClientError as exc:
        return str(exc)
    finally:
        await client.aclose()


async def cmd_list_dictionary_docs(arguments: dict) -> str:
    client = RagentClient()
    try:
        kb_id = await client.ensure_kb(_kb_name())
        docs = await client.list_documents(kb_id)
        return json.dumps(docs, ensure_ascii=False, indent=2)
    except RagentClientError as exc:
        return str(exc)
    finally:
        await client.aclose()


# ── MCP 接线（模式同 ReportAgent mcp_schema_server/server.py） ──


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="ingest_table_schemas",
            description=(
                "自省 PostgreSQL 表结构（COMMENT 语义 + FK + 低基数枚举），渲染为数据字典文档并灌入 RAG 字典知识库。\n"
                "输入：schema（默认 public）、tables（可选，表名过滤数组）。\n"
                "输出：每表 JSON 结果 {table, filename, document_id, status, chunk_count, error}。\n"
                "用于：首次建库、表结构或注释变更后刷新字典。重复执行幂等（同名文档增量更新）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "schema": {"type": "string", "description": "PG schema，默认 public"},
                    "tables": {"type": "array", "items": {"type": "string"}, "description": "可选表名过滤"},
                },
            },
        ),
        Tool(
            name="upsert_api_dictionary",
            description=(
                "登记/更新一个接口的字段字典（含长连接协议标记），灌入 RAG 字典知识库。\n"
                "输入：name（必填）、description、protocol（http/websocket/sse/long_poll）、endpoint、auth、\n"
                "fields: [{name, type, required, desc, example, message?}]（流式接口用 message 标注消息类型）。\n"
                "输出：{filename, document_id, status, chunk_count, error}。同名全量覆盖（幂等）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "protocol": {"type": "string", "enum": ["http", "websocket", "sse", "long_poll"]},
                    "endpoint": {"type": "string"},
                    "auth": {"type": "string"},
                    "fields": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["name", "fields"],
            },
        ),
        Tool(
            name="search_dictionary",
            description=(
                "在数据字典知识库中检索字段/表/接口含义（混合检索，只检索不生成）。\n"
                "输入：query（必填，中文自然语言）、top_k（默认 5）。\n"
                "输出：匹配 chunk 列表（内容片段 + 来源文档 + 分数）；无匹配时返回「字典库无匹配：<query>」。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_dictionary_docs",
            description="列出字典知识库中已登记的字典文档及摄入状态。无输入。",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


_DISPATCH = {
    "ingest_table_schemas": cmd_ingest_table_schemas,
    "upsert_api_dictionary": cmd_upsert_api_dictionary,
    "search_dictionary": cmd_search_dictionary,
    "list_dictionary_docs": cmd_list_dictionary_docs,
}


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    handler = _DISPATCH.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: {name}")
    text = await handler(arguments or {})
    return [TextContent(type="text", text=text)]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(server_name="ragent-dictionary", server_version="1.0.0"),
        )


if __name__ == "__main__":
    asyncio.run(main())
```

`mcp_server/requirements.txt`:

```text
mcp>=1.0.0
httpx
psycopg2-binary
```

- [ ] **Step 4: 安装依赖并运行测试**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pip install "mcp>=1.0.0" && D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_dict_server.py -v
```

预期：7 passed。

- [ ] **Step 5: 提交**

```bash
cd D:/PyProject/ragent-py && git add mcp_server/server.py mcp_server/requirements.txt tests/unit/test_dict_server.py && git commit -m "feat(mcp): ragent-dictionary MCP 服务（stdio，4 工具 + 错误契约）"
```

---

### Task A8: ragent-py plan 登记 + 全量回归

- [ ] **Step 1: 在 `D:/PyProject/ragent-py/docs/plans/README.md` 「进行中」区登记**

把「## 进行中」下的 `（暂无）` 替换为：

```markdown
- [2026-08-06-dictionary-mcp-bridge](2026-08-06-dictionary-mcp-bridge.md) — 数据字典 MCP 桥：`POST /api/v1/retrieve` 只检索端点 + `mcp_server/`（stdio，表结构/接口字典灌入 + 检索）。设计权威在 ReportAgent 仓库 `docs/plans/2026-08-06-rag-dictionary-mcp-bridge.md`
```

并创建 `docs/plans/2026-08-06-dictionary-mcp-bridge.md`，内容为：

```markdown
> 状态: 进行中

# 数据字典 MCP 桥（ragent-py 侧）

本仓库侧的改动清单与验证见 ReportAgent 仓库
`docs/plans/2026-08-06-rag-dictionary-mcp-bridge.md`（设计权威）与
`docs/superpowers/plans/2026-08-06-rag-dictionary-mcp-bridge.md`（任务计划 Phase A）。

## 本仓库改动面

- `app/api/retrieve.py`（新增）+ `app/main.py` 挂载：只检索端点，visibility 鉴权 + kb_ids pin
- `app/core/retrieval.py`：抽取 `embed_query_with_fallback`
- `app/models/schemas.py`：Retrieve 三模型
- `mcp_server/`（新增）：stdio MCP 桥（ingest/upsert/search/list）

## 明确不做（本仓库侧）

- 不新增 RBAC 权限项；不改 db.py 模型；不动摄入管线
```

- [ ] **Step 2: 全量回归**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m pytest -q && D:/miniConda/envs/rag/python.exe -c "import app.main"
```

预期：unit 全过；integration 若 PG 可达亦应全过（ragent_test 库自动创建）。失败则按 `superpowers:systematic-debugging` 处理，不许跳过测试交差。

- [ ] **Step 3: 提交**

```bash
cd D:/PyProject/ragent-py && git add docs/plans/ && git commit -m "docs(plan): 数据字典 MCP 桥 plan 登记"
```

---

## Phase B：ReportAgent（COMMENT + 字典工具 + 澄清闭环）

### Task B1: `seed_pg.sql` 补全 COMMENT ON（字段语义权威源落地）

**Files:**
- Modify: `d:/PyProject/ReportAgent/backend/scripts/seed_pg.sql`（10 张表 DDL 之后、文件末尾追加注释块）

说明：无独立单测——验证走 Phase C 冒烟（`col_description` 查询）与「重跑 seed 不报错」。注释中的关系语义（→ 目标列）弥补 seed 中无 FK 约束的现状。

- [ ] **Step 1: 在 seed_pg.sql 末尾追加**

```sql
-- ── 字段语义注释（数据字典 RAG 桥的权威语义源，mcp_server.introspect 读取） ──

COMMENT ON TABLE dim_date IS '日期维度表，包含年/季度/月/周以及节假日标记';
COMMENT ON COLUMN dim_date.date_id IS '日期主键，格式 yyyymmdd';
COMMENT ON COLUMN dim_date.full_date IS '完整日期';
COMMENT ON COLUMN dim_date.year IS '年份';
COMMENT ON COLUMN dim_date.quarter_num IS '季度序号（1-4）';
COMMENT ON COLUMN dim_date.quarter IS '季度标签（Q1-Q4）';
COMMENT ON COLUMN dim_date.week_of_year IS '年内周数';
COMMENT ON COLUMN dim_date.day_name IS '星期（中文）';
COMMENT ON COLUMN dim_date.is_holiday IS '节假日标记：0=工作日，1=节假日';

COMMENT ON TABLE dim_region IS '区域和城市映射表，包含大区及对应省市';
COMMENT ON COLUMN dim_region.region_id IS '区域主键';
COMMENT ON COLUMN dim_region.region_name IS '大区名称（华北/华东/华南/西南/西北/东北）';
COMMENT ON COLUMN dim_region.province IS '省份';
COMMENT ON COLUMN dim_region.city IS '城市';
COMMENT ON COLUMN dim_region.tier IS '城市等级（一线/二线/三线）';

COMMENT ON TABLE dim_product IS '产品信息表，包含品类、品牌与价格';
COMMENT ON COLUMN dim_product.product_id IS '产品主键';
COMMENT ON COLUMN dim_product.product_name IS '产品名称';
COMMENT ON COLUMN dim_product.category IS '产品大类（电子产品/服装鞋帽/食品饮料/家电/日用品）';
COMMENT ON COLUMN dim_product.sub_category IS '产品子品类';
COMMENT ON COLUMN dim_product.brand IS '品牌';
COMMENT ON COLUMN dim_product.unit_price IS '单价（元）';
COMMENT ON COLUMN dim_product.cost_price IS '成本价（元）';
COMMENT ON COLUMN dim_product.supplier IS '供应商';

COMMENT ON TABLE dim_customer IS '客户维度表，包含等级、行业与注册信息';
COMMENT ON COLUMN dim_customer.customer_id IS '客户主键';
COMMENT ON COLUMN dim_customer.customer_name IS '客户名称';
COMMENT ON COLUMN dim_customer.customer_tier IS '客户等级（钻石/金卡/银卡/普通）';
COMMENT ON COLUMN dim_customer.industry IS '所属行业';
COMMENT ON COLUMN dim_customer.city IS '所在城市';
COMMENT ON COLUMN dim_customer.register_date IS '注册日期';

COMMENT ON TABLE dim_warehouse IS '仓库维度表';
COMMENT ON COLUMN dim_warehouse.warehouse_id IS '仓库主键';
COMMENT ON COLUMN dim_warehouse.warehouse_name IS '仓库名称';
COMMENT ON COLUMN dim_warehouse.city IS '所在城市';
COMMENT ON COLUMN dim_warehouse.capacity IS '容量上限（件）';

COMMENT ON TABLE dim_employee IS '员工维度表';
COMMENT ON COLUMN dim_employee.employee_id IS '员工主键';
COMMENT ON COLUMN dim_employee.employee_name IS '员工姓名';
COMMENT ON COLUMN dim_employee.department IS '部门';
COMMENT ON COLUMN dim_employee.position IS '岗位';
COMMENT ON COLUMN dim_employee.city IS '工作城市';
COMMENT ON COLUMN dim_employee.hire_date IS '入职日期';

COMMENT ON TABLE fact_sales IS '销售记录事实表，每条记录代表一笔销售';
COMMENT ON COLUMN fact_sales.sale_id IS '销售记录主键';
COMMENT ON COLUMN fact_sales.date_id IS '销售日期（关联 dim_date.date_id）';
COMMENT ON COLUMN fact_sales.product_id IS '产品（关联 dim_product.product_id）';
COMMENT ON COLUMN fact_sales.region_id IS '区域（关联 dim_region.region_id）';
COMMENT ON COLUMN fact_sales.customer_id IS '客户（关联 dim_customer.customer_id）';
COMMENT ON COLUMN fact_sales.channel IS '销售渠道（线上/线下）';
COMMENT ON COLUMN fact_sales.quantity IS '销售数量';
COMMENT ON COLUMN fact_sales.unit_price IS '成交单价（元）';
COMMENT ON COLUMN fact_sales.discount IS '折扣率（如 0.90 表示九折）';
COMMENT ON COLUMN fact_sales.total_amount IS '销售金额（元），等于 quantity × unit_price × discount';
COMMENT ON COLUMN fact_sales.cost_amount IS '成本金额（元）';
COMMENT ON COLUMN fact_sales.profit IS '毛利（元），等于 total_amount − cost_amount';

COMMENT ON TABLE fact_returns IS '退货记录事实表，关联销售记录';
COMMENT ON COLUMN fact_returns.return_id IS '退货记录主键';
COMMENT ON COLUMN fact_returns.sale_id IS '关联销售记录（关联 fact_sales.sale_id）';
COMMENT ON COLUMN fact_returns.product_id IS '退货产品（关联 dim_product.product_id）';
COMMENT ON COLUMN fact_returns.return_date_id IS '退货日期（关联 dim_date.date_id）';
COMMENT ON COLUMN fact_returns.return_quantity IS '退货数量';
COMMENT ON COLUMN fact_returns.return_amount IS '退货金额（元）';
COMMENT ON COLUMN fact_returns.return_reason IS '退货原因（质量问题/不适用/运输损坏/描述不符）';
COMMENT ON COLUMN fact_returns.handling IS '处理方式（退款/换货）';

COMMENT ON TABLE fact_inventory IS '库存记录事实表，按产品+仓库+日期记录';
COMMENT ON COLUMN fact_inventory.inventory_id IS '库存记录主键';
COMMENT ON COLUMN fact_inventory.product_id IS '产品（关联 dim_product.product_id）';
COMMENT ON COLUMN fact_inventory.warehouse_id IS '仓库（关联 dim_warehouse.warehouse_id）';
COMMENT ON COLUMN fact_inventory.date_id IS '快照日期（关联 dim_date.date_id）';
COMMENT ON COLUMN fact_inventory.quantity_on_hand IS '在库数量';
COMMENT ON COLUMN fact_inventory.quantity_reserved IS '预留数量';
COMMENT ON COLUMN fact_inventory.quantity_available IS '可售数量，等于 quantity_on_hand − quantity_reserved';

COMMENT ON TABLE fact_attendance IS '考勤记录事实表，关联员工';
COMMENT ON COLUMN fact_attendance.attendance_id IS '考勤记录主键';
COMMENT ON COLUMN fact_attendance.employee_id IS '员工（关联 dim_employee.employee_id）';
COMMENT ON COLUMN fact_attendance.date_id IS '考勤日期（关联 dim_date.date_id）';
COMMENT ON COLUMN fact_attendance.status IS '考勤状态（正常/请假 等）';
COMMENT ON COLUMN fact_attendance.work_hours IS '工时（小时）';
```

- [ ] **Step 2: 重跑 seed 验证（docker PG 已开）**

```bash
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/seed_pg.sql
docker exec -i ragent-postgres psql -U ragent -d ragent -c "SELECT count(*) FROM pg_description d JOIN pg_class c ON d.objoid=c.oid JOIN pg_namespace n ON c.relnamespace=n.oid WHERE n.nspname='public';"
```

预期：seed 无报错；count ≥ 80（10 表注释 + 全部列注释）。

- [ ] **Step 3: 提交**

```bash
cd d:/PyProject/ReportAgent && git add backend/scripts/seed_pg.sql && git commit -m "feat(db): seed_pg.sql 补全 COMMENT ON——字段语义权威源 + plan: rag-dictionary-mcp-bridge"
```

---

### Task B2: `interface_dict_tools.py`——ReportAgent 侧字典检索工具

**Files:**
- Create: `d:/PyProject/ReportAgent/backend/app/tools/interface_dict_tools.py`
- Test: `d:/PyProject/ReportAgent/backend/tests/contracts/test_interface_dict_tools.py`

- [ ] **Step 1: 写失败测试**

`tests/contracts/test_interface_dict_tools.py`:

```python
"""search_interface_dictionary：未配置降级、命中序列化、401 重登、不可达不抛栈。"""
import json

import pytest

pytestmark = pytest.mark.contracts


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def dict_env(monkeypatch):
    monkeypatch.setenv("RAGENT_URL", "http://fake:8000")
    monkeypatch.setenv("RAGENT_USER", "admin")
    monkeypatch.setenv("RAGENT_PASSWORD", "admin123")
    monkeypatch.setenv("DICT_KB_NAME", "数据字典")


def test_unset_env_degrades_gracefully(monkeypatch):
    from app.tools.interface_dict_tools import search_interface_dictionary
    monkeypatch.delenv("RAGENT_URL", raising=False)
    out = json.loads(search_interface_dictionary.invoke({"query": "销售额"}))
    assert "未配置" in out["error"]


def test_happy_path_serializes_matches(monkeypatch, dict_env):
    import app.tools.interface_dict_tools as mod
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        return _Resp(200, {"access_token": "t"})

    def fake_request(method, url, **kw):
        if url.endswith("/api/v1/kb"):
            return _Resp(200, [{"id": "kb-9", "name": "数据字典"}])
        return _Resp(200, {"items": [{"chunk_id": "c1", "document_id": "d1",
                                      "text": "total_amount 销售金额", "title": "dict-table_public_fact_sales.md",
                                      "section_path": "", "score": 0.8}], "degraded": False})

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    monkeypatch.setattr(mod.httpx, "request", fake_request)
    mod._token_cache.clear()

    out = json.loads(search_interface_dictionary.invoke({"query": "total_amount 是什么", "top_k": 3}))
    assert out["matches"][0]["text"].startswith("total_amount")
    assert out["matches"][0]["source"] == "dict-table_public_fact_sales.md"


def test_unreachable_returns_error_text(monkeypatch, dict_env):
    import httpx as real_httpx
    import app.tools.interface_dict_tools as mod

    def boom(*a, **kw):
        raise real_httpx.ConnectError("refused")

    monkeypatch.setattr(mod.httpx, "post", boom)
    mod._token_cache.clear()
    out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
    assert "不可达" in out["error"]


def test_empty_result_semantics(monkeypatch, dict_env):
    import app.tools.interface_dict_tools as mod

    def fake_post(url, **kw):
        return _Resp(200, {"access_token": "t"})

    def fake_request(method, url, **kw):
        if url.endswith("/api/v1/kb"):
            return _Resp(200, [{"id": "kb-9", "name": "数据字典"}])
        return _Resp(200, {"items": [], "degraded": False})

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    monkeypatch.setattr(mod.httpx, "request", fake_request)
    mod._token_cache.clear()
    out = json.loads(search_interface_dictionary.invoke({"query": "不存在的字段"}))
    assert out["matches"] == []
    assert "无匹配" in out["note"]
```

- [ ] **Step 2: 运行确认失败**

```bash
cd d:/PyProject/ReportAgent/backend && python -m pytest tests/contracts/test_interface_dict_tools.py -v
```

预期：FAIL（ModuleNotFoundError: app.tools.interface_dict_tools）。

- [ ] **Step 3: 实现 `backend/app/tools/interface_dict_tools.py`**

```python
"""接口/表字段字典检索工具：httpx 直连 ragent-py 的 /retrieve。

数据字典（表结构语义 + 接口字段字典）存放在 ragent-py 的专用知识库，
灌入由 ragent-py/mcp_server 负责；本模块是 ReportAgent 侧的读取面。
RAGENT_URL 未配置或字典库无匹配时静默降级——绝不阻塞 SQL 生成。
"""
from __future__ import annotations

import json
import logging
import os
import threading

import httpx
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_TOKEN_LOCK = threading.Lock()
_token_cache: dict[str, str] = {}
_kb_id_cache: dict[str, str] = {}

_MAX_MATCH_TEXT = 400
_MAX_MATCHES = 8


def _base() -> str:
    return os.getenv("RAGENT_URL", "").rstrip("/")


def _login_token(base: str) -> str:
    with _TOKEN_LOCK:
        cached = _token_cache.get(base)
        if cached:
            return cached
        resp = httpx.post(
            f"{base}/api/v1/auth/login",
            json={"username": os.getenv("RAGENT_USER", ""), "password": os.getenv("RAGENT_PASSWORD", "")},
            timeout=10,
        )
        resp.raise_for_status()
        _token_cache[base] = resp.json()["access_token"]
        return _token_cache[base]


def _dict_kb_id(base: str, token: str) -> str:
    """按名解析字典 KB id（GET /api/v1/kb），缓存。"""
    with _TOKEN_LOCK:
        cached = _kb_id_cache.get(base)
        if cached:
            return cached
    kb_name = os.getenv("DICT_KB_NAME", "数据字典")
    resp = httpx.request("GET", f"{base}/api/v1/kb",
                         headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    for kb in resp.json():
        if kb.get("name") == kb_name:
            with _TOKEN_LOCK:
                _kb_id_cache[base] = kb["id"]
            return kb["id"]
    raise LookupError(f"ragent-py 中不存在名为 {kb_name} 的知识库")


@tool
def search_interface_dictionary(query: str, top_k: int = 5) -> str:
    """在数据字典知识库中检索字段/接口/表的含义释义。
    输入：query（中文自然语言，如 'total_amount 是什么'），top_k 返回条数（默认 5）。
    输出：JSON，matches 为命中片段 [{text, source, score}]；无匹配时 matches=[] 且 note 说明；
    字典服务未配置/不可达时返回 error 字段（调用方按无字典处理，不阻塞主流程）。
    用于：用户问题涉及接口字段或不明确字段含义时查释义；写 SQL 前确认业务口径。
    不要用来找数据表——用 search_tables；不要用来执行查询——此工具只读字典文档。"""
    base = _base()
    if not base:
        return json.dumps({"error": "字典服务未配置（RAGENT_URL 为空）"}, ensure_ascii=False)
    try:
        token = _login_token(base)
        kb_id = _dict_kb_id(base, token)
        resp = httpx.request(
            "POST", f"{base}/api/v1/retrieve",
            headers={"Authorization": f"Bearer {token}"},
            json={"query": query, "kb_ids": [kb_id], "top_k": top_k},
            timeout=30,
        )
        if resp.status_code == 401:  # token 过期 → 清缓存重登一次
            with _TOKEN_LOCK:
                _token_cache.pop(base, None)
            token = _login_token(base)
            resp = httpx.request(
                "POST", f"{base}/api/v1/retrieve",
                headers={"Authorization": f"Bearer {token}"},
                json={"query": query, "kb_ids": [kb_id], "top_k": top_k},
                timeout=30,
            )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return json.dumps({"matches": [], "note": f"字典库无匹配：{query}"}, ensure_ascii=False)
        matches = [
            {"text": (it.get("text") or "")[:_MAX_MATCH_TEXT],
             "source": it.get("title") or it.get("document_id", ""),
             "score": it.get("score", 0.0)}
            for it in items[:_MAX_MATCHES]
        ]
        return json.dumps({"matches": matches}, ensure_ascii=False)
    except LookupError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    except httpx.HTTPError as exc:
        logger.warning("dictionary lookup failed: %s", exc)
        return json.dumps({"error": f"字典服务不可达：{exc}"}, ensure_ascii=False)
    except Exception as exc:  # 最后防线：字典链路任何异常都不阻塞主流程
        logger.warning("dictionary lookup unexpected error: %s", exc)
        return json.dumps({"error": f"字典检索异常：{exc}"}, ensure_ascii=False)
```

- [ ] **Step 4: 运行确认通过**

```bash
cd d:/PyProject/ReportAgent/backend && python -m pytest tests/contracts/test_interface_dict_tools.py -v
```

预期：4 passed。

- [ ] **Step 5: 提交**

```bash
cd d:/PyProject/ReportAgent && git add backend/app/tools/interface_dict_tools.py backend/tests/contracts/test_interface_dict_tools.py && git commit -m "feat(tools): search_interface_dictionary 字典检索工具（httpx 直连 ragent /retrieve）+ plan: rag-dictionary-mcp-bridge"
```

---

### Task B3: 工具注册进 registry + prompt 断言核查

**Files:**
- Modify: `d:/PyProject/ReportAgent/backend/app/tools/__init__.py`（`register_all_tools` 内追加注册）
- Test: `d:/PyProject/ReportAgent/backend/tests/contracts/test_tool_registry.py`（若已存在则追加用例，否则新建）

- [ ] **Step 1: 写失败测试**

`tests/contracts/test_tool_registry.py`（新文件；已存在则仅追加函数）:

```python
"""工具注册表契约：字典工具在册、元数据正确、SQL 门控工具集不变。"""
import pytest

pytestmark = pytest.mark.contracts


def test_search_interface_dictionary_registered():
    from app.tools import register_all_tools
    from app.tools.registry import registry

    register_all_tools()
    meta = registry.get_metadata("search_interface_dictionary")
    assert meta is not None
    assert meta.agent_type == "data"
    assert meta.capability == "dictionary_search"
    assert meta.risk_level == "low"


def test_sql_tools_still_registered():
    from app.tools import register_all_tools
    from app.tools.registry import registry

    register_all_tools()
    for name in ("validate_sql", "execute_sql"):
        assert registry.get_metadata(name) is not None
```

- [ ] **Step 2: 运行确认失败**

```bash
cd d:/PyProject/ReportAgent/backend && python -m pytest tests/contracts/test_tool_registry.py -v
```

预期：第一个用例 FAIL（meta is None）。

- [ ] **Step 3: 实现——`backend/app/tools/__init__.py`**

顶部 import 行追加模块：

```python
from app.tools import data_tools, sql_tools, report_tools, interface_dict_tools
```

在 `register_all_tools` 内「数据 Agent 工具」区块末尾（list_tables 注册之后）追加：

```python
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
```

- [ ] **Step 4: 运行注册测试 + 被钉住的 prompt 断言全量核查**

```bash
cd d:/PyProject/ReportAgent/backend && python -m pytest tests/contracts/test_tool_registry.py tests/graphs/test_sql_generation.py -v
```

预期：注册测试 2 passed。`test_sql_generation.py` 若因 `_format_tools_for_prompt()` 未过滤调用点（新工具进入 tools_block）而 FAIL：逐个定位 FAIL 的调用点（`grep -rn "_format_tools_for_prompt(" backend/app`），对**仅为既有工具设计**的调用点改为传 whitelist（如 `_format_tools_for_prompt({"search_tables", "get_table_ddl", "list_tables"})`——以该调用点原应包含的工具集为准）；若 FAIL 断言本身是「工具块包含全部注册工具」这类合理变化，则更新断言。处理后重跑直至全绿。

- [ ] **Step 5: 提交**

```bash
cd d:/PyProject/ReportAgent && git add backend/app/tools/__init__.py backend/tests/contracts/test_tool_registry.py backend/app/agent/ && git commit -m "feat(tools): search_interface_dictionary 注册进 registry + plan: rag-dictionary-mcp-bridge"
```

（若本步未改 agent/ 下文件，git add 该目录无副作用。）

---

### Task B4: `requirement_parser` 接入 dictionary_context + 澄清规则

**Files:**
- Modify: `d:/PyProject/ReportAgent/backend/app/agent/requirement_parser.py`
- Test: `d:/PyProject/ReportAgent/backend/tests/graphs/test_requirement_dictionary_clarify.py`

- [ ] **Step 1: 写失败测试**

`tests/graphs/test_requirement_dictionary_clarify.py`:

```python
"""dictionary_context 注入 + field_meaning 澄清规则：prompt 契约与 assumption 透传。"""
import json

import pytest

pytestmark = pytest.mark.graphs


def test_dictionary_context_injected_into_prompt(monkeypatch):
    import app.agent.requirement_parser as rp
    captured = {}

    def fake_llm(prompt, **kwargs):
        captured["prompt"] = prompt
        return json.dumps({
            "summary": "查询销售额", "target_metrics": ["销售额"],
            "time_range": "今年", "scope": [], "dimensions": ["时间"],
            "analysis_methods": ["trend_analysis"], "confidence": 0.9,
            "missing_fields": [], "assumptions": [],
        })

    monkeypatch.setattr(rp, "call_llm", fake_llm)
    rp.parse_requirement(
        user_query="统计订单推送的 amt 字段总额",
        schema_context=None,
        dictionary_context="- dict-api_orders-push.md: amt = 实付金额（元）",
    )
    assert "【数据字典参考】" in captured["prompt"]
    assert "amt = 实付金额" in captured["prompt"]
    assert "field_meaning" in captured["prompt"]  # 澄清规则在场


def test_field_meaning_assumption_passthrough(monkeypatch):
    import app.agent.requirement_parser as rp

    def fake_llm(prompt, **kwargs):
        return json.dumps({
            "summary": "查询金额", "target_metrics": ["金额"],
            "time_range": None, "scope": [], "dimensions": [],
            "analysis_methods": [], "confidence": 0.6,
            "missing_fields": ["time_range"],
            "assumptions": [{
                "key": "field_meaning:amt",
                "text": "字段 amt 推测为实付金额（元），请确认",
                "alternatives": [{"label": "应付金额", "value": "应付金额（元）"}],
            }],
        })

    monkeypatch.setattr(rp, "call_llm", fake_llm)
    card = rp.parse_requirement(user_query="amt 总额", schema_context=None)
    keys = [a.key for a in card.assumptions]
    assert "field_meaning:amt" in keys
    target = next(a for a in card.assumptions if a.key == "field_meaning:amt")
    assert target.accepted is None  # 待用户确认，gate 会拦截


def test_no_dictionary_context_is_noop(monkeypatch):
    import app.agent.requirement_parser as rp
    captured = {}

    def fake_llm(prompt, **kwargs):
        captured["prompt"] = prompt
        return json.dumps({
            "summary": "s", "target_metrics": [], "time_range": None, "scope": [],
            "dimensions": [], "analysis_methods": [], "confidence": 0.5,
            "missing_fields": [], "assumptions": [],
        })

    monkeypatch.setattr(rp, "call_llm", fake_llm)
    rp.parse_requirement(user_query="今年销售额", schema_context=None)
    assert "【数据字典参考】" not in captured["prompt"]
```

- [ ] **Step 2: 运行确认失败**

```bash
cd d:/PyProject/ReportAgent/backend && python -m pytest tests/graphs/test_requirement_dictionary_clarify.py -v
```

预期：FAIL（TypeError: parse_requirement() got an unexpected keyword argument 'dictionary_context'）。

- [ ] **Step 3: 实现——`backend/app/agent/requirement_parser.py`**

3a. `_PARSE_PROMPT` 中 `可用表结构:` 块之后追加占位区块（format 参数名 `dictionary_block`）：

```python
{dictionary_block}
```

并在「维度判断规则」之后、「输出 JSON」之前追加规则段：

```text
字段释义规则：
- 「数据字典参考」中给出释义的字段，直接采用其含义，不要再生成对应假设
- 用户提及的字段在字典中无释义或释义歧义时，输出 assumption：
  key 固定为 "field_meaning:<字段名>"，text 写你的最佳猜测释义（注明「请确认」），
  alternatives 给候选释义（可为空数组）。用户确认前该字段含义不得用于 SQL 生成
```

3b. `_call_llm_for_parse` 签名与 prompt 组装改为：

```python
def _call_llm_for_parse(
    user_query: str,
    schema: SchemaContext | None,
    conversation_context: str | None = None,
    dictionary_context: str | None = None,
) -> dict:
    """Call the LLM and parse the JSON response. Returns {} on parse failure."""
    dictionary_block = ""
    if dictionary_context:
        # C-7 同款边界：字典片段来自外部 RAG，长度不受信任
        bounded = dictionary_context[:4000]
        dictionary_block = f"【数据字典参考】\n{bounded}"
    prompt = _PARSE_PROMPT.format(
        user_query=user_query,
        schema_text=_schema_text(schema),
        dictionary_block=dictionary_block,
    )
    if conversation_context:
        prompt = f"{format_context_block(conversation_context)}\n\n{prompt}"
    raw = call_llm(prompt, max_tokens=1500)
    ...（其余不动）
```

3c. `parse_requirement` 签名追加 `dictionary_context: str | None = None`，并把它透传给 `_call_llm_for_parse`。

- [ ] **Step 4: 运行确认通过 + parser 既有测试回归**

```bash
cd d:/PyProject/ReportAgent/backend && python -m pytest tests/graphs/test_requirement_dictionary_clarify.py -v && python -m pytest tests/ -k "requirement" -q
```

预期：新测试 3 passed；既有 requirement 相关测试无回归（`_PARSE_PROMPT.format` 新增参数若有其他调用点，一并补齐 `dictionary_block=""`）。

- [ ] **Step 5: 提交**

```bash
cd d:/PyProject/ReportAgent && git add backend/app/agent/requirement_parser.py backend/tests/graphs/test_requirement_dictionary_clarify.py && git commit -m "feat(requirement): 需求解析接入数据字典上下文 + field_meaning 澄清规则 + plan: rag-dictionary-mcp-bridge"
```

---

### Task B5: `_requirement_parse` 接线程序化字典检索 + sqlgate 扩展

**Files:**
- Modify: `d:/PyProject/ReportAgent/backend/app/agent/requirement_analysis_graph.py`（`_requirement_parse` 节点）
- Test: `d:/PyProject/ReportAgent/backend/tests/graphs/test_requirement_analysis_sqlgate.py`（追加用例）

- [ ] **Step 1: 写失败测试——sqlgate 文件追加**

```python
def test_dictionary_lookup_degrades_without_ragent(monkeypatch):
    """RAGENT_URL 未配置：字典检索静默降级，需求分析全链路仍完成。"""
    monkeypatch.delenv("RAGENT_URL", raising=False)
    from app.agent.requirement_analysis_graph import build_requirement_analysis_graph

    async def fake_parse(*args, **kwargs):
        return None

    # 避免真实 LLM：parse 节点内部函数打桩
    import app.agent.requirement_analysis_graph as g
    captured = {}
    real_parse = g.parse_requirement

    def spy_parse(**kwargs):
        captured.update(kwargs)
        from app.models.requirement import RequirementCard
        return RequirementCard(id="t", status="missing", summary="s")

    monkeypatch.setattr(g, "parse_requirement", spy_parse)
    graph = build_requirement_analysis_graph()
    result = asyncio.run(graph.ainvoke({
        "user_query": "统计订单推送的 amt 总额",
        "user_id": 1,
        "session_id": str(uuid.uuid4()),
        "trace_id": "",
    }))
    # SQL 门控由既有 tripwire 保障；此处断言字典上下文降级为 None 且不阻塞
    assert captured.get("dictionary_context") in (None, "")
    assert result.get("requirement_card") is not None
```

说明：若 `build_requirement_analysis_graph` 导出名不同（以模块实际为准），或 `_requirement_parse` 内 `build_session_context` 需一并打桩（它读 DB——单测不可达），则在测试里 monkeypatch `g.build_session_context` 为 async 返回 ""。按实际签名微调，断言不变。

- [ ] **Step 2: 运行确认失败**

```bash
cd d:/PyProject/ReportAgent/backend && python -m pytest tests/graphs/test_requirement_analysis_sqlgate.py::test_dictionary_lookup_degrades_without_ragent -v
```

预期：FAIL（parse spy 收不到 dictionary_context 键，或节点未传参）。

- [ ] **Step 3: 实现——`requirement_analysis_graph.py::_requirement_parse`**

在既有 `conversation_context` 获取块之后、`parse_requirement` 调用之前插入：

```python
    # 数据字典程序化检索：命中则注入释义上下文；任何失败降级为空（同 conversation_context 策略）
    dictionary_context = ""
    try:
        from app.tools.interface_dict_tools import search_interface_dictionary
        raw = search_interface_dictionary.invoke({"query": state["user_query"], "top_k": 5})
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        matches = parsed.get("matches") or []
        if matches:
            dictionary_context = "\n".join(
                f"- {m.get('source', '')}: {(m.get('text') or '')[:300]}" for m in matches
            )
    except Exception as exc:
        logger.warning("dictionary lookup in requirement analysis failed: %s", exc)
```

`parse_requirement(...)` 调用追加 `dictionary_context=dictionary_context or None`。模块顶部确认 `import json` 存在（无则补）。

- [ ] **Step 4: 运行 sqlgate 全文件 + graphs 回归**

```bash
cd d:/PyProject/ReportAgent/backend && python -m pytest tests/graphs/test_requirement_analysis_sqlgate.py -v && python -m pytest -m graphs -q
```

预期：含新用例在内全 passed；既有 SQL 门控 tripwire 用例不回归。

- [ ] **Step 5: 提交**

```bash
cd d:/PyProject/ReportAgent && git add backend/app/agent/requirement_analysis_graph.py backend/tests/graphs/test_requirement_analysis_sqlgate.py && git commit -m "feat(requirement): 需求分析接入字典检索旁路（失败降级）+ sqlgate 扩展 + plan: rag-dictionary-mcp-bridge"
```

---

### Task B6: `.env.example` + ReportAgent 全量回归

**Files:**
- Modify: `d:/PyProject/ReportAgent/backend/.env.example`（文件末尾追加）

- [ ] **Step 1: 追加配置段**

```text

# ---- 数据字典桥（ragent-py RAG） -------------------------------------------
# 字段/接口字典存放在 ragent-py 的专用知识库；未配置时字典检索静默降级。

# ragent-py 服务地址（如 http://localhost:8000）
RAGENT_URL=

# 服务账号（需 kb.create / doc.upload / doc.read_all 权限）
RAGENT_USER=
RAGENT_PASSWORD=

# 字典知识库名（与 ragent-py/mcp_server 的 DICT_KB_NAME 保持一致）
DICT_KB_NAME=数据字典
```

- [ ] **Step 2: 全量回归**

```bash
cd d:/PyProject/ReportAgent/backend && python -m pytest -q
```

预期：全量离线套件通过（persistence/e2e 无环境自动 skip 属正常）。

- [ ] **Step 3: 提交**

```bash
cd d:/PyProject/ReportAgent && git add backend/.env.example && git commit -m "docs(env): .env.example 补 RAGENT_* 字典桥配置 + plan: rag-dictionary-mcp-bridge"
```

---

## Phase C：跨进程冒烟（手工，docker PG 已开）

前置：ragent-py 的 `.env` 有真实 `MINIMAX_API_KEY` / `SILICONFLOW_API_KEY` / `JWT_SECRET` / `PII_ENCRYPTION_KEY`；`.env` 或 shell 里设 `RAGENT_USER=admin RAGENT_PASSWORD=admin123`。

### Task C1: ragent-py 侧冒烟

- [ ] **Step 1: 起 ragent-py**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -m app.main
```

预期：`http://localhost:8000` 就绪，`curl http://localhost:8000/health` 通。

- [ ] **Step 2: 灌入表结构字典（首灌）**

```bash
cd D:/PyProject/ragent-py && DICT_PG_DSN="postgresql://ragent:ragent@localhost:5432/ragent" D:/miniConda/envs/rag/python.exe -c "
import asyncio, json
from mcp_server import server as srv
print(asyncio.run(srv.cmd_ingest_table_schemas({'schema': 'public'})))
"
```

预期：10 表结果，全部 `status=indexed`，`chunk_count > 0`，文件名为 `dict-table_public_*.md`。注意 Windows cmd 下 env 前缀语法差异——不可用时改为先 `set DICT_PG_DSN=...` 再执行。

- [ ] **Step 3: 幂等性（连灌第二次）**

重跑 Step 2 命令。预期：`document_id` 与首灌相同（同名复用），`chunk_count` 稳定，ragent-py 日志出现增量 hash 复用（无变化文档近乎零 embedding 调用）。

- [ ] **Step 4: 接口字典（http + websocket 各一）**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -c "
import asyncio
from mcp_server import server as srv
print(asyncio.run(srv.cmd_upsert_api_dictionary({
  'name': 'orders-push', 'description': '订单推送长连接', 'protocol': 'websocket',
  'endpoint': 'wss://example.com/orders',
  'fields': [
    {'name': 'order_id', 'type': 'string', 'required': True, 'desc': '订单号', 'message': 'on_message'},
    {'name': 'amt', 'type': 'number', 'required': True, 'desc': '实付金额（元）', 'message': 'on_message'},
    {'name': 'hb', 'type': 'string', 'required': False, 'desc': '心跳标识', 'message': 'heartbeat'}],
})))
"
```

预期：`status=indexed`。

- [ ] **Step 5: 检索验证**

```bash
cd D:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -c "
import asyncio
from mcp_server import server as srv
print(asyncio.run(srv.cmd_search_dictionary({'query': '2024年各区域销售额用哪些字段', 'top_k': 5})))
print(asyncio.run(srv.cmd_search_dictionary({'query': 'amt 字段是什么意思', 'top_k': 3})))
"
```

预期：第一查命中 `dict-table_public_fact_sales.md` / `dim_region`；第二查命中 `dict-api_orders-push.md` 且文本含「实付金额」。

- [ ] **Step 6: 错误路径**

```bash
# 错误密码
cd D:/PyProject/ragent-py && RAGENT_PASSWORD=wrong D:/miniConda/envs/rag/python.exe -c "
import asyncio
from mcp_server import server as srv
print(asyncio.run(srv.cmd_search_dictionary({'query': 'x'})))
"
```

预期：返回「登录失败：请检查 RAGENT_USER / RAGENT_PASSWORD」。再停掉 ragent-py 重跑：返回「ragent-py 服务不可达…」；`DICT_PG_DSN` 置空跑 `cmd_ingest_table_schemas`：返回「DICT_PG_DSN 未配置」。

- [ ] **Step 7: MCP stdio 端到端（可选，Claude Code 配置后验证）**

按 `server.py` docstring 的 mcpServers 配置接入 Claude Code，调用 `list_dictionary_docs`，应返回已登记的 `dict-*` 文档清单。

### Task C2: ReportAgent 侧冒烟

- [ ] **Step 1: COMMENT 落地确认**

```bash
docker exec -i ragent-postgres psql -U ragent -d ragent -c "SELECT col_description('public.fact_sales'::regclass, (SELECT attnum FROM pg_attribute WHERE attrelid='public.fact_sales'::regclass AND attname='total_amount'));"
```

预期：返回「销售金额（元），等于 quantity × unit_price × discount」。

- [ ] **Step 2: 字典工具直连验证**

ReportAgent `.env` 配置 `RAGENT_URL=http://localhost:8000 RAGENT_USER=admin RAGENT_PASSWORD=admin123` 后：

```bash
cd d:/PyProject/ReportAgent/backend && python -c "
from app.tools.interface_dict_tools import search_interface_dictionary
print(search_interface_dictionary.invoke({'query': 'profit 毛利怎么算', 'top_k': 3}))
"
```

预期：matches 命中 fact_sales 字典片段（含「毛利」释义）。

- [ ] **Step 3: 澄清闭环手测（需真实 LLM）**

```bash
REPORTAGENT_E2E=1 python -m pytest backend/tests/e2e/test_full_flow.py -s   # 回归既有全链路不破坏
```

随后手工：起 backend + frontend，提问「统计订单推送接口的 amt 字段总额」（字典已收录 amt）与一个**未收录**字段名（如 `xyz_amt`）：
- 已收录：需求卡不应出现该字段的 assumption，流程正常。
- 未收录：需求卡出现 `field_meaning:xyz_amt` assumption（含猜测释义）；未确认时点确认 → 409（gate 拦截）；确认后出报表。

- [ ] **Step 4: 收尾——两仓库 plan 状态更新**

全部通过后：两份 plan 顶部状态改 `已完成`（附本轮 commit 列表），索引行移入「已完成」区并填写落地摘要；提交 docs 变更。

---

## Self-Review 结论（写计划时已执行）

1. **Spec 覆盖**：设计文档决策基线 10 项 → Task A1-A8（ragent 侧 6 项）+ B1-B6（语义源/消费方/澄清/隔离配置）+ C1-C2（幂等/错误路径/闭环验证）全部有对应任务；「明确不做」清单无任何任务越界。
2. **占位符**：无 TBD/TODO；所有代码步骤含完整代码。B5 测试对导出名/DB 打桩的现场微调已显式说明判断依据，非占位。
3. **类型一致性**：`RetrieveRequest/Response/RetrievedItem`（A1）与 retrieve.py（A3）、client.py（A6）字段一致；`cmd_*` 函数名在 A7 实现与测试间一致；`search_interface_dictionary` 返回 JSON 键（matches/source/text/note/error）在 B2 实现与 B5 消费端一致；`table_filename/api_filename` 在 render（A4）与 server（A7）一致。
4. **已知风险**：ragent-py `GET /documents` 无 kb_id 参数 → client 侧过滤（A6 已实现并注明）；首传显式 document_id 404 → 全部走确定性文件名（A4/A7 已遵守）。
