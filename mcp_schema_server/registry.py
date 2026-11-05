"""Schema 注册表：从 ragent-py 字典知识库检索 + 解析，替代硬编码 _TABLES。

本 MCP server 不再本地索引表结构——schema 权威在 ragent-py 的「数据字典」KB
（ingest_table_schemas 灌入）。本模块经 HTTP 调 ragent-py /api/v1/retrieve 与
/api/v1/documents 取字典文档，解析回结构化表 schema。

配置（env，绝不进工具入参）：RAGENT_URL / RAGENT_USER / RAGENT_PASSWORD / DICT_KB_NAME。
不可达/未配置 → 各方法返回空/None，不抛（调用方降级）。
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import httpx

from mcp_schema_server import token_cache

logger = logging.getLogger(__name__)

# FAQ 知识库单一数据源（与 backend/app/tools/faq_tools.py 读取同一份 JSON）。
_FAQ_PATH = __import__("pathlib").Path(__file__).resolve().parent.parent / "backend" / "scripts" / "schema_faq.json"
_FAQ_ENTRIES: list[dict] | None = None

_DICT_KB_NAME = os.getenv("DICT_KB_NAME", "数据字典")
_MAX_RETRIEVE = 6

_token_cache: dict[str, str] = {}
_kb_id_cache: dict[str, str] = {}


def _load_faq() -> list[dict]:
    """惰性加载 FAQ 知识库；文件缺失/损坏降级为空列表。"""
    global _FAQ_ENTRIES
    if _FAQ_ENTRIES is not None:
        return _FAQ_ENTRIES
    try:
        if _FAQ_PATH.exists():
            with open(_FAQ_PATH, encoding="utf-8") as f:
                _FAQ_ENTRIES = json.load(f)
    except Exception:
        _FAQ_ENTRIES = []
    if _FAQ_ENTRIES is None:
        _FAQ_ENTRIES = []
    return _FAQ_ENTRIES


def _base() -> str:
    return os.getenv("RAGENT_URL", "").rstrip("/")


def _login_token(base: str) -> str:
    cached = _token_cache.get(base)
    if cached:
        return cached
    # 跨进程共享缓存：命中复用，避免多次进程各自登录撞限流。
    shared = token_cache.get_token(base)
    if shared:
        _token_cache[base] = shared
        return shared
    resp = httpx.post(
        f"{base}/api/v1/auth/login",
        json={"username": os.getenv("RAGENT_USER", ""), "password": os.getenv("RAGENT_PASSWORD", "")},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _token_cache[base] = token
    token_cache.set_token(base, token)
    return token


def _dict_kb_id(base: str, token: str) -> str:
    cached = _kb_id_cache.get(base)
    if cached:
        return cached
    resp = httpx.request("GET", f"{base}/api/v1/kb",
                         headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    for kb in resp.json():
        if kb.get("name") == _DICT_KB_NAME:
            _kb_id_cache[base] = kb["id"]
            return kb["id"]
    raise LookupError(f"ragent-py 中不存在名为 {_DICT_KB_NAME} 的知识库")


def _retrieve_dict(query: str, top_k: int) -> list[dict]:
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


def _is_analytical_table(name: str) -> bool:
    """只暴露星型模型分析表（dim_*/fact_*），与 ReportAgent check_sql_safety 白名单一致。"""
    return name.startswith(("dim_", "fact_"))


def _build_ddl(table_name: str, columns: list[dict]) -> str:
    lines = [f"CREATE TABLE {table_name} ("]
    lines.append(",\n".join(f"  {c['name']} {c['type']}" for c in columns))
    lines.append(");")
    return "\n".join(lines)


def _parse_table_doc(text: str) -> dict | None:
    """解析字典表结构文档 chunk → {table_name, description, columns}；失败返回 None。

    兼容两种标题/字段形态（2026-11-05 与 backend/app/tools/rag_schema.py 对齐）：
    - 旧格式：`# 表 \\`public.fact_orders\\`` + `## 字段` + `字段 order_id 类型 integer 含义 …`
    - 新格式（ragent-py `render_table_doc` 改版）：`【表 public.fact_orders / 字段详解】` /
      `# 表 public.fact_orders`（无 backtick）+ `## 字段清单` + Markdown 表格
      （`| \\`order_id\\` | integer | 订单主键 | … |`）
    """
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
        elif line.startswith("# 表 ") or line.startswith("【表 "):
            prefix_len = len("# 表 ") if line.startswith("# 表 ") else len("【表 ")
            inner = line[prefix_len:]
            if "`" in inner:
                inner = inner.split("`")[1] if inner.startswith("`") else inner.split("`")[0]
            # chunk 标题可能带节名：`【表 public.fact_orders / 字段】` → 只取表名
            table_name = inner.split("/")[0].split(".")[-1].strip()
        elif line.startswith("## 字段"):
            in_fields = True
        elif in_fields:
            m = re.match(r"^字段 (\S+) 类型 (.*?) 含义", line)
            if m:
                columns.append({"name": m.group(1), "type": m.group(2).strip()})
            elif line.startswith("|"):
                cells = [c.strip().strip("`").strip() for c in line.strip("|").split("|")]
                if (
                    len(cells) >= 2
                    and cells[0].replace("-", "").strip()
                    and not cells[0].startswith(("字段名", "--"))
                ):
                    columns.append({"name": cells[0], "type": cells[1]})
        elif not in_fields and table_name and line and not line.startswith(("#", "【", "|", "-")):
            # 「表 `public.dim_store` 用途:门店维度表…」也算描述（新格式的说明就在这一行）
            note = line
            if note.startswith("表 "):
                note = note.split("用途:", 1)[1] if "用途:" in note else ""
            note = note.strip().rstrip("。")
            if note:
                description_lines.append(note)
    if not table_name or not columns:
        return None
    return {
        "table_name": table_name,
        "description": " ".join(description_lines)[:200],
        "columns": columns,
    }


class SchemaRegistry:
    def __init__(self):
        pass

    def build_index(self) -> int:
        """本地不再建表索引——schema 从 ragent-py 字典 KB 实时取。返回 0 表示无本地索引。"""
        return 0

    def search_tables(self, query: str, top_k: int = 3) -> list[dict]:
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

    def get_table_ddl(self, table_name: str) -> Optional[str]:
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

    def list_tables(self) -> list[dict]:
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
            table = base.split("_", 1)[1] if "_" in base else base
            if not _is_analytical_table(table):
                continue
            # 2026-11-05：`chunk_count` 是**文档切片数**，不是字段数——之前直接当
            # column_count 返回（7 张表里出现 1/2 这种明显错误）。这里改为解析该表的
            # 字典文档，如实给出字段数与描述。
            description = ""
            column_count = 0
            try:
                items = _retrieve_dict(table, top_k=1)
            except Exception as exc:  # noqa: BLE001 - 单表失败不拖垮整张清单
                logger.warning("list_tables: retrieve %s failed: %s", table, exc)
                items = []
            for item in items:
                parsed = _parse_table_doc(item.get("text") or "")
                if parsed and parsed["table_name"] == table:
                    description = parsed.get("description") or ""
                    column_count = len(parsed.get("columns") or [])
                    break
            results.append({
                "table_name": table,
                "description": description[:120] or (d.get("title") or "")[:120],
                "column_count": column_count,
            })
        return results

    def search_faq(self, query: str, top_k: int = 3) -> list[dict]:
        """检索 FAQ 知识库（常见问题 + SQL 模板 + 业务口径要点）。空/无命中返回 []。"""
        entries = _load_faq()
        if not query or not query.strip() or not entries:
            return []
        qlower = query.lower()
        scored: list[tuple[float, dict]] = []
        for e in entries:
            score = 0.0
            for kw in (e.get("keywords", []) or []):
                if isinstance(kw, str) and kw and kw.lower() in qlower:
                    score += 3.0
            q_terms = set(str(e.get("question", "")).lower().replace(",", " ").split())
            for term in q_terms:
                if term and len(term) > 1 and term in qlower:
                    score += 1.0
            if score > 0:
                scored.append((score, e))
        scored.sort(key=lambda x: -x[0])
        return [
            {
                "question": e.get("question", ""),
                "sql": e.get("sql", ""),
                "note": e.get("note", ""),
                "tables": e.get("tables", []),
                "score": round(score, 2),
            }
            for score, e in scored[:top_k]
        ]


registry = SchemaRegistry()