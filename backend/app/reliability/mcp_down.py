"""E2E MCP-unavailable 注入 seam（P15 e2e 扩充，fail-closed）。

让正式 e2e 用例在同一 backend 上**逐请求**确定性模拟 ragent 字典 MCP 不可用
（schema 检索 down），不依赖杀 MCP 进程 / 断网络：

- `parse_header`：main 在 chat / adjust / confirm 请求读 `X-E2E-McpDown: on`。
- `scoped`：把「本请求 MCP down」写进 request-scoped contextvar（contextvar 由
  `asyncio.create_task` 自动继承 → confirm/adjust 后台任务内同样生效）。
- `active`：`rag_schema._retrieve_dict_via_mcp` 调 MCP 前 consult。

注入形态（关键设计）：不是给 rag_schema 加一条假的空返回绕过，而是在调 MCP 前
raise `MCPBoundaryError(MCP_UNAVAILABLE)`——与真实 MCP 中断**同一错误分类**，
于是 `search_tables_from_rag → []` / `get_table_ddl_from_rag → None` 等既有
graceful 降级路径原样跑一遍。测的是「MCP down 时整条主链如何正常回滚」，而不是
测一条专门为测试开的旁路。

激活条件双 gate fail-closed：backend env `REPORTAGENT_E2E=1` 且请求带合法 header，
否则恒 inactive——生产零行为变化。
"""
from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from typing import Iterator

_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "e2e_mcp_down", default=False
)

_HEADER = "X-E2E-McpDown"
_TRUTHY = {"1", "on", "true", "yes"}


def _enabled() -> bool:
    """fail-closed：REPORTAGENT_E2E=1 才可能激活（与 e2e 测试同一 gate）。"""
    return os.getenv("REPORTAGENT_E2E") == "1"


def parse_header(value: str | None) -> bool:
    """请求头 → 是否注入 MCP down；gate 不满足 / 值非法 → False。"""
    if not _enabled() or not value:
        return False
    return value.strip().lower() in _TRUTHY


def active() -> bool:
    """当前请求是否应注入 MCP down（rag_schema 调 MCP 前 consult）。"""
    return _enabled() and _active.get()


@contextmanager
def scoped(down: bool) -> Iterator[None]:
    """把 down 状态钉进当前 context；退出时恢复旧值（不泄漏到其它请求/任务）。"""
    if not _enabled() or not down:
        yield
        return
    token = _active.set(True)
    try:
        yield
    finally:
        _active.reset(token)
