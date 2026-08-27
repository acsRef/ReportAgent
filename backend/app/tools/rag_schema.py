"""Schema 从 ragent-py 字典知识库来：检索 + 解析字典表结构文档。

替代被删除的硬编码 `_TABLES`。字典 KB 里每张表一个文档（`ingest_table_schemas` 灌入），
chunk 文本格式实测：
    # 表 `public.fact_sales`
    销售记录事实表,每条记录代表一笔销售
    ## 字段
    字段 sale_id 类型 integer 含义 销售记录主键 枚举/FK
    ...
本模块把检索到的 chunk 解析回结构化 {table_name, description, columns}。

P2 RAG/MCP Boundary 改造（docs/plans/2026-08-26-p2-rag-mcp-boundary.md）：
  - search_tables / get_table_ddl 正路走 MCP `search_dictionary`，HTTP 直连降级为
    flag-gated fallback（PHASE2_MCP_ONLY 未锁 + UNAVAILABLE 时走 _retrieve_dict_http）；
    MCP_INVALID_RESPONSE → 不 retry（_call_with_retry 仅 MCP_TIMEOUT 触发重试）
    + 不 fallback（dispatcher 显式分支直接 raise）。
  - list_tables 无 MCP 等价工具，仍走 HTTP 直连（_list_dict_docs）。
  - 失败路径把 MCPBoundaryError 的 code/detail 写进 log，工具契约对 Agent 保持不变
    （search_tables 仍返回 []，get_table_ddl 仍返回 None）。
"""
from __future__ import annotations

import json
import logging
import os
import re

import httpx

from app.tools.interface_dict_tools import _base, _dict_kb_id, _login_token
from app.tools.mcp_client import _fallback_allowed, _validate_matches_contract, get_rag_mcp_client
from app.tools.mcp_errors import MCPBoundaryError

logger = logging.getLogger(__name__)

_DICT_KB_NAME = os.getenv("DICT_KB_NAME", "数据字典")
_MAX_RETRIEVE = 6


def _is_analytical_table(name: str) -> bool:
    """只暴露星型模型分析表（dim_*/fact_*），与 check_sql_safety 白名单一致。

    两项目共享 ragent 库，字典 KB 里混有 ragent-py 系统表（users/documents 等），
    必须过滤，否则系统表会污染 schema 发现与 SQL 生成。
    """
    return name.startswith(("dim_", "fact_"))


def _build_ddl(table_name: str, columns: list[dict]) -> str:
    lines = [f"CREATE TABLE {table_name} ("]
    lines.append(",\n".join(f"  {c['name']} {c['type']}" for c in columns))
    lines.append(");")
    return "\n".join(lines)


def _parse_table_doc(text: str) -> dict | None:
    """解析字典表结构文档 chunk → {table_name, description, columns}；失败返回 None。"""
    if not text:
        return None
    table_name = None
    description_lines: list[str] = []
    columns: list[dict] = []
    in_fields = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("# 表 `"):
            inner = line.split("`")[1] if "`" in line else line
            table_name = inner.split(".")[-1].strip()
        elif line.startswith("## 字段"):
            in_fields = True
        elif in_fields:
            m = re.match(r"^字段 (\S+) 类型 (.*?) 含义", line)
            if m:
                columns.append({"name": m.group(1), "type": m.group(2).strip()})
        elif not in_fields and table_name and line and not line.startswith(("#", "【")):
            description_lines.append(line)
    if not table_name or not columns:
        return None
    return {
        "table_name": table_name,
        "description": " ".join(description_lines)[:200],
        "columns": columns,
    }


def _retrieve_dict_via_mcp(query: str, top_k: int) -> list[dict]:
    """正路：经 MCP client 调 ragent-py `search_dictionary`，返回 items 列表。

    Items 形态由 mcp_client.call_tool 返回的 dict.matches 决定，与 HTTP /retrieve
    同 schema（chunk_id / document_id / text / title / section_path / score）。
    mcp_client runtime annotation（_note / degraded）由本函数丢弃——只 items 透传。

    业务契约校验（review 第 2 轮 P1 修订）：_validate_matches_contract 检查
    result 形态 + 每个 item 含 text(str) + score(numeric)；失败 → MCPBoundaryError
    (INVALID_RESPONSE)，由 _retrieve_dict dispatcher 决定上抛还是 fallback。

    Raises:
        MCPBoundaryError: 失败时显式分类（MCP_TIMEOUT / MCP_UNAVAILABLE / MCP_INVALID_RESPONSE）
    """
    result = get_rag_mcp_client().call_tool(
        "search_dictionary", {"query": query, "top_k": top_k}
    )
    return _validate_matches_contract(result)


def _retrieve_dict_http(query: str, top_k: int) -> list[dict]:
    """HTTP 直连 fallback（ragent-py /api/v1/retrieve）；PHASE2_MCP_ONLY 未锁时触发。

    保留原 httpx + token 缓存 + KB 按名解析逻辑；失败抛错（调用方降级）。
    """
    base = _base()
    if not base:
        raise RuntimeError("字典服务未配置（RAGENT_URL 为空）")
    token = _login_token(base)
    kb_id = _dict_kb_id(base, token)
    resp = httpx.request(
        "POST", f"{base}/api/v1/retrieve",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "kb_ids": [kb_id], "top_k": top_k},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def _retrieve_dict(query: str, top_k: int) -> list[dict]:
    """字典 KB 检索 dispatcher：MCP-first + flag-gated HTTP fallback。

    行为：
      - MCP 成功 → 返回 MCP items
      - MCPBoundaryError(UNAVAILABLE) + _fallback_allowed() → HTTP fallback
      - MCPBoundaryError(UNAVAILABLE) + flag 锁定 → 上抛（调用方按各自契约处理）
      - MCPBoundaryError(INVALID_RESPONSE) → 上抛（不 retry 不 fallback）

    Raises:
        MCPBoundaryError: UNAVAILABLE flag-locked 或 INVALID_RESPONSE（调用方按工具
            契约返回 [] 或 None；其它 Exception 走通用 except）。
    """
    try:
        return _retrieve_dict_via_mcp(query, top_k)
    except MCPBoundaryError as exc:
        if exc.code.value == "MCP_INVALID_RESPONSE":
            # 协议错：_call_with_retry 仅 MCP_TIMEOUT 触发重试 + dispatcher
            # 不走 HTTP fallback（重试同结果也不值得）→ 直接上抛。
            raise
        if not _fallback_allowed():
            # flag 锁定：不走 HTTP fallback
            raise
        logger.warning(
            "MCP unavailable, falling back to HTTP: %s [%s]",
            exc.detail, exc.code.value,
        )
        return _retrieve_dict_http(query, top_k)


def _list_dict_docs() -> list[dict]:
    """列出字典 KB 文档（含 filename）。失败抛错。

    无 MCP 等价工具；MCP-first 改造（P2）后此函数维持 HTTP 直连，
    由 list_tables_from_rag 直接调用（不经 dispatcher）。
    """
    base = _base()
    if not base:
        raise RuntimeError("字典服务未配置（RAGENT_URL 为空）")
    token = _login_token(base)
    kb_id = _dict_kb_id(base, token)
    resp = httpx.request(
        "GET", f"{base}/api/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": 200},
        timeout=30,
    )
    resp.raise_for_status()
    return [d for d in resp.json() if d.get("kb_id") == kb_id]


def search_tables_from_rag(query: str, top_k: int = 3) -> list[dict]:
    """检索字典 KB → 解析命中表文档 → 结构化 schema 列表。

    失败处理（MCP-first）：
      - MCP 成功 / HTTP fallback 成功 → 按 score 排序取 top_k
      - MCPBoundaryError flag-locked 或 INVALID_RESPONSE → 返回 []（graceful 契约）
      - 其它 Exception → 返回 []（兜底）

    不抛错到上游——SQL 生成主流程不应因 schema 检索失败阻塞。
    """
    try:
        items = _retrieve_dict(query, top_k * _MAX_RETRIEVE)
    except MCPBoundaryError as exc:
        logger.warning(
            "schema search from rag failed (MCP): %s [%s]",
            exc.detail, exc.code.value,
        )
        return []
    except Exception as exc:
        logger.warning("schema search from rag failed: %s", exc)
        return []
    results = []
    for it in items:
        parsed = _parse_table_doc(it.get("text") or "")
        if parsed and _is_analytical_table(parsed["table_name"]):
            parsed["ddl"] = _build_ddl(parsed["table_name"], parsed["columns"])
            parsed["score"] = float(it.get("score") or 0.0)
            results.append(parsed)
    results.sort(key=lambda x: -x["score"])
    return results[:top_k]


def get_table_ddl_from_rag(table_name: str) -> str | None:
    """精确取单张表 DDL。检索表名命中 → 解析重建；找不到/不可达 → None。

    失败处理同 search_tables_from_rag：MCP 失败 → 返回 None。
    """
    try:
        items = _retrieve_dict(table_name, top_k=_MAX_RETRIEVE)
    except MCPBoundaryError as exc:
        logger.warning(
            "get_table_ddl from rag failed (MCP): %s [%s]",
            exc.detail, exc.code.value,
        )
        return None
    except Exception as exc:
        logger.warning("get_table_ddl from rag failed: %s", exc)
        return None
    for it in items:
        parsed = _parse_table_doc(it.get("text") or "")
        if parsed and parsed["table_name"] == table_name and _is_analytical_table(table_name):
            return _build_ddl(table_name, parsed["columns"])
    return None


def list_tables_from_rag() -> list[dict]:
    """列出字典 KB 里全部表文档。column_count 以文档 chunk 数近似（元数据无列数）。

    维持 HTTP 直连路径（MCP 无 list 工具）；失败返回 []。
    """
    try:
        docs = _list_dict_docs()
    except Exception as exc:
        logger.warning("list_tables from rag failed: %s", exc)
        return []
    results = []
    for d in docs:
        fname = d.get("filename", "")
        if not fname.startswith("dict-table_"):
            continue
        base = fname[len("dict-table_"):].removesuffix(".md")
        # dict-table_<schema>_<table>.md；schema 默认 public（无下划线）
        table = base.split("_", 1)[1] if "_" in base else base
        if not _is_analytical_table(table):
            continue
        results.append({
            "table_name": table,
            "description": (d.get("title") or "")[:120],
            "column_count": int(d.get("chunk_count") or 0),
        })
    return results
