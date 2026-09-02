"""E2E fault injection seam（P15 e2e T4，fail-closed）。

让正式 fail/repair e2e 用例在同一 backend 上**逐请求**确定性触发 execution 层真 fault，
不依赖 LLM 首猜拼错：

- `parse_header`：main 在 chat/confirm 请求读 `X-E2E-Fault: kind=<k>;mode=<once|persistent>`。
- `kind_override`：`sql_graph._evaluate` 顶部 consult，返回要注入的 kind（None=不注入）。

激活条件**双 gate fail-closed**：backend env `REPORTAGENT_E2E=1` 且 header 值合法，否则恒
None——生产零行为变化。

kind 白名单：
- `object_not_found`：repair 语义。按 validation-failed 注入 → DiagnosePolicy →
  retry_mcp_schema_retrieval（真 get_table_ddl + 换 schema）→ re-generate。
- `permission`：fail-fast 语义。按 execution-failed 注入 → DiagnosePolicy
  `not agent_recoverable → action=fail` → 图 FAILED（不伪造成功）。

mode：
- `once`：仅第 1 次 attempt 注入。判定 `retry_counters.sql_generation == 1`——increment 在
  `sql_graph.py:590` `_generate_sql` 内、evaluate 之前，故首次 evaluate 时 ==1（防 off-by-one）。
  修后第 2 次 evaluate ==2 → 走真路径。
- `persistent`：每次都注入（预算耗尽 / 永久失败）。
"""
from __future__ import annotations

import os
import re
from typing import Any

_ALLOWED_KINDS = ("object_not_found", "permission")
_HEADER = "X-E2E-Fault"
_PATTERN = re.compile(r"^kind=([a-z_]+);mode=(once|persistent)$")


def _enabled() -> bool:
    """fail-closed：REPORTAGENT_E2E=1 才可能激活（与 e2e 测试同一 gate）。"""
    return os.getenv("REPORTAGENT_E2E") == "1"


def parse_header(value: str | None) -> dict[str, str] | None:
    """解析请求头 → {"kind", "mode"}；gate 不满足 / 格式非法 / kind 不在白名单 → None。"""
    if not _enabled() or not value:
        return None
    m = _PATTERN.match(value.strip())
    if not m:
        return None
    kind, mode = m.group(1), m.group(2)
    if kind not in _ALLOWED_KINDS:
        return None
    return {"kind": kind, "mode": mode}


def kind_override(state: dict[str, Any]) -> str | None:
    """返回应注入的 kind；None = 不注入（正常路径）。state 须含 fault_override + retry_counters。"""
    if not _enabled():
        return None
    spec = state.get("fault_override") or None
    if not spec:
        return None
    kind = spec.get("kind")
    mode = spec.get("mode")
    if kind not in _ALLOWED_KINDS or mode not in ("once", "persistent"):
        return None
    if mode == "once":
        # 首次 attempt：sql_generation==1（increment 在 generate 内、evaluate 前）。
        # counter=0（未 generate）或 >=2（修后重试）都不注入 → 无 off-by-one。
        if int(state.get("retry_counters", {}).get("sql_generation", 0)) != 1:
            return None
    return kind
