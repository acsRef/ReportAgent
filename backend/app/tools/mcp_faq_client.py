"""持久 stdio MCP client：连 ragent-py 的 mcp_server 调 search_faq。

ragent-py（D:\\PyProject\\ragent-py）的 mcp_server 是 stdio 传输、需 rag env 运行，
内部经 HTTP 连 ragent-py FastAPI（/api/v1/retrieve）。本模块在 ReportAgent 进程内
持一个后台事件循环线程 + 一个 ragent-py mcp_server 子进程会话，跨调用复用；
search_faq 为同步、线程安全入口，供 langchain @tool 直接调用。

任何失败抛 MCPFaqClientError（中文），由调用方降级（本地 JSON fallback）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MCPFaqClientError(RuntimeError):
    """MCP FAQ 服务不可用——面向降级逻辑的可读错误。"""


class _MCPFaqConfig:
    """子进程启动参数 + 超时。默认贴合用户环境，均可被 env 覆盖。"""

    def __init__(self):
        self.python = os.getenv("RAGENT_MCP_PYTHON", "D:/miniConda/envs/rag/python.exe")
        self.module = os.getenv("RAGENT_MCP_MODULE", "mcp_server.server")
        self.cwd = os.getenv("RAGENT_MCP_CWD", "D:/PyProject/ragent-py")
        self.timeout = float(os.getenv("RAGENT_MCP_TIMEOUT", "15"))

    def validate(self) -> None:
        if not self.python or not self.cwd:
            raise MCPFaqClientError("MCP FAQ 服务未配置（RAGENT_MCP_PYTHON / RAGENT_MCP_CWD 为空）")


class MCPFaqClient:
    """进程级单例；后台线程跑专用事件循环持一个 stdio 会话。"""

    def __init__(self, config: Optional[_MCPFaqConfig] = None):
        self._config = config or _MCPFaqConfig()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session: Any = None
        self._read_cm: Any = None
        self._call_lock: Optional[asyncio.Lock] = None

    # ── 后台事件循环 ──

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop_runner, name="mcp-faq", daemon=True)
            self._thread.start()
        return self._loop

    def _loop_runner(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # ── 会话建立（在后台循环上执行）──

    async def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(
            command=self._config.python,
            args=["-m", self._config.module],
            env=self._subprocess_env(),
            cwd=self._config.cwd,
        )
        self._read_cm = stdio_client(params)
        read, write = await self._read_cm.__aenter__()
        try:
            self._session = await ClientSession(read, write).__aenter__()
            await self._session.initialize()
        except BaseException:
            await self._read_cm.__aexit__(None, None, None)
            self._read_cm = None
            raise
        return self._session

    def _subprocess_env(self) -> dict[str, str]:
        """透传父进程 env + RAGENT_*/FAQ_KB_NAME（子进程 mcp_server 靠它们连 ragent-py）。"""
        env = dict(os.environ)
        for key in ("RAGENT_URL", "RAGENT_USER", "RAGENT_PASSWORD", "FAQ_KB_NAME"):
            val = os.getenv(key)
            if val is not None:
                env[key] = val
        return env

    def _reset(self) -> None:
        """会话异常后清理，让下次调用能重建。"""
        try:
            if self._read_cm is not None:
                self._read_cm.__aexit__(None, None, None)
        except Exception:
            pass
        self._session = None
        self._read_cm = None

    # ── 同步入口 ──

    def search_faq(self, query: str, top_k: int = 3) -> str:
        """阻塞调 ragent-py search_faq，返回 JSON 文本。失败抛 MCPFaqClientError。"""
        self._config.validate()
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(self._call(query, top_k), loop)
        try:
            return future.result(timeout=self._config.timeout)
        except asyncio.TimeoutError as exc:
            logger.warning("MCP FAQ search timeout after %ss", self._config.timeout)
            self._reset()
            raise MCPFaqClientError(f"MCP FAQ 检索超时（{self._config.timeout}s），请确认 ragent-py 已启动") from exc
        except MCPFaqClientError:
            self._reset()
            raise
        except Exception as exc:  # 连接/握手/调用错误
            self._reset()
            raise MCPFaqClientError(f"MCP FAQ 检索失败: {exc}") from exc

    async def _call(self, query: str, top_k: int) -> str:
        # 实例级、循环内建的锁：串行化会话建立 + 调用（MCP session 非并发安全）。
        lock = self._call_lock
        if lock is None:
            lock = self._call_lock = asyncio.Lock()
        async with lock:
            try:
                session = await self._ensure_session()
                result = await session.call_tool("search_faq", {"query": query, "top_k": top_k})
                parts = [c.text for c in (result.content or []) if getattr(c, "type", "") == "text"]
                return "\n".join(parts)
            except BaseException:
                self._reset()
                raise


_client: Optional[MCPFaqClient] = None


def get_mcp_faq_client() -> MCPFaqClient:
    global _client
    if _client is None:
        _client = MCPFaqClient()
    return _client