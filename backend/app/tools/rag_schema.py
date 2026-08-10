"""Schema 从 ragent-py 字典知识库来：检索 + 解析字典表结构文档。

替代被删除的硬编码 `_TABLES`。字典 KB 里每张表一个文档（`ingest_table_schemas` 灌入），
chunk 文本格式实测：
    # 表 `public.fact_sales`
    销售记录事实表,每条记录代表一笔销售
    ## 字段
    字段 sale_id 类型 integer 含义 销售记录主键 枚举/FK
    ...
本模块把检索到的 chunk 解析回结构化 {table_name, description, columns}。

复用 interface_dict_tools 的 httpx + token 缓存 + KB 按名解析模式（同包）。
ragent-py 不可达/未配置 → 各 *_from_rag 返回空/None，不抛（SQL 生成降级不崩）。
"""
from __future__ import annotations

import json
import logging
import os
import re

import httpx

from app.tools.interface_dict_tools import _base, _dict_kb_id, _login_token

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


def _retrieve_dict(query: str, top_k: int) -> list[dict]:
    """字典 KB 混合检索，返回 items 列表。失败抛错（调用方降级）。"""
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


def _list_dict_docs() -> list[dict]:
    """列出字典 KB 文档（含 filename）。失败抛错。"""
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
    """检索字典 KB → 解析命中表文档 → 结构化 schema 列表。不可达/无命中 → []。"""
    try:
        items = _retrieve_dict(query, top_k * _MAX_RETRIEVE)
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
    """精确取单张表 DDL。检索表名命中 → 解析重建；找不到/不可达 → None。"""
    try:
        items = _retrieve_dict(table_name, top_k=_MAX_RETRIEVE)
    except Exception as exc:
        logger.warning("get_table_ddl from rag failed: %s", exc)
        return None
    for it in items:
        parsed = _parse_table_doc(it.get("text") or "")
        if parsed and parsed["table_name"] == table_name and _is_analytical_table(table_name):
            return _build_ddl(table_name, parsed["columns"])
    return None


def list_tables_from_rag() -> list[dict]:
    """列出字典 KB 里全部表文档。column_count 以文档 chunk 数近似（元数据无列数）。"""
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