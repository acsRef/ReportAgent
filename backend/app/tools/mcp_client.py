"""通用 stdio MCP client：吸收 mcp_faq_client 生命周期，服务所有 RAG 工具调用。

本模块是 backend/tools 层唯一 MCP 进程/session 持有者。

实现契约（docs/plans/2026-08-26-p2-rag-mcp-boundary.md）：
  - 子进程参数：RAGENT_MCP_PYTHON / RAGENT_MCP_CWD / RAGENT_MCP_MODULE / RAGENT_MCP_TIMEOUT
  - 透传给子进程的 env：RAGENT_URL / RAGENT_USER / RAGENT_PASSWORD /
    DICT_KB_NAME / FAQ_KB_NAME（Q7：必须含 DICT_KB_NAME，漏则 MCP/HTTP 检索不同 KB）
  - flag：PHASE2_MCP_ONLY（Q5 优先级：REPORTAGENT_E2E > 显式 > APP_ENV 推断）
  - 失败分类：MCP_TIMEOUT / MCP_UNAVAILABLE / MCP_INVALID_RESPONSE（Q2 决议）
  - 重试：仅 MCP_TIMEOUT，max_attempts=2（Q4 决议）
  - 字段稳定集：tool 层契约，mcp_client 不感知（Q8 澄清）

设计边界：
  - 不 import ragent_token_cache / interface_dict_tools._* / rag_schema._*
  - 不反向 import mcp_faq_client（防循环依赖，P0 钉子）
  - 业务 schema 校验在 tool 层；本模块只校验协议形态（顶层必须 JSON object）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any, Optional

from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

logger = logging.getLogger(__name__)


# ── 响应分类器已知前缀表（Q2 决议）──

# 合法空命中：ragent-py MCP server 把零匹配编码为中文纯文本
EMPTY_PREFIXES = ("字典库无匹配", "FAQ 无匹配")

# 协议错误（我方 bug）：缺少必填参数 → INVALID_RESPONSE
INVALID_PREFIXES = ("缺少必填参数",)

# 运行时不可达：ragent-py RagentClientError 中文文案（连接/认证/服务端错误）
UNAVAILABLE_PREFIXES = ("登录失败", "无权", "检索失败", "客户端异常")


# ── 子进程配置 ──


class _MCPConfig:
    """子进程启动参数 + 超时。默认贴合用户环境，均可被 env 覆盖。"""

    def __init__(self) -> None:
        self.python = os.getenv("RAGENT_MCP_PYTHON", "D:/miniConda/envs/rag/python.exe")
        self.module = os.getenv("RAGENT_MCP_MODULE", "mcp_server.server")
        self.cwd = os.getenv("RAGENT_MCP_CWD", "D:/PyProject/ragent-py")
        self.timeout = float(os.getenv("RAGENT_MCP_TIMEOUT", "15"))

    def validate(self) -> None:
        if not self.python or not self.cwd:
            raise MCPBoundaryError(
                MCPErrorCode.MCP_UNAVAILABLE,
                "MCP client 未配置（RAGENT_MCP_PYTHON / RAGENT_MCP_CWD 为空）",
            )


# ── flag 解析（Q5 决议：REPORTAGENT_E2E > 显式 > APP_ENV 推断）──


def _resolve_phase2_flag() -> bool:
    """PHASE2_MCP_ONLY 解析（P5 起默认 ON，停止本地 fallback）。

    优先级：REPORTAGENT_E2E=1 > PHASE2_MCP_ONLY 显式值 > 默认 ON。
    返回 True 表示锁定（不允许 fallback），False 表示放行。
    P5 后默认 True；显式 PHASE2_MCP_ONLY=false 仍可放行以便离线测试。
    """
    if os.getenv("REPORTAGENT_E2E") == "1":
        return True
    explicit = os.getenv("PHASE2_MCP_ONLY")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    return True


def _fallback_allowed() -> bool:
    """fallback 是否允许（== _resolve_phase2_flag() == False）。"""
    return not _resolve_phase2_flag()


# ── Client ──


class RagMCPClient:
    """进程级单例：后台事件循环线程 + 一个 stdio 会话服务所有工具调用。"""

    def __init__(self, config: Optional[_MCPConfig] = None) -> None:
        self._config = config or _MCPConfig()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session: Any = None
        self._read_cm: Any = None
        # asyncio.Lock 自 3.10 起不再绑定 event loop，可在 __init__ 安全创建
        self._call_lock: Optional[asyncio.Lock] = asyncio.Lock()

    # ── 后台事件循环 ──

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop_runner, name="rag-mcp", daemon=True
            )
            self._thread.start()
        return self._loop

    def _loop_runner(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # ── 会话建立 ──

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
        """透传父进程 env + 必要 RAG env。

        Q7 决议：必须包含 DICT_KB_NAME（MCP server 内部按 env 解析 KB 名）。
        漏掉会导致 MCP 通道与 HTTP fallback 通道检索不同知识库。
        """
        env = dict(os.environ)
        for key in (
            "RAGENT_URL",
            "RAGENT_USER",
            "RAGENT_PASSWORD",
            "DICT_KB_NAME",
            "FAQ_KB_NAME",
        ):
            val = os.getenv(key)
            if val is not None:
                env[key] = val
        return env

    async def _reset_async(self) -> None:
        """真正的 async cleanup：await session + read_cm 的 __aexit__。

        必须在 event loop 内被 await。同步入口见 _reset()。

        session 依赖 read/write streams；先关 session 再关 read_cm。
        cleanup 失败不影响状态清空（重入安全：先清状态再 cleanup，下次调用
        不会拿到坏 session）。
        """
        session = self._session
        read_cm = self._read_cm
        # 先清状态（防重入），再 cleanup
        self._session = None
        self._read_cm = None

        if session is not None:
            try:
                await session.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning("session __aexit__ failed during reset: %s", exc)
        if read_cm is not None:
            try:
                await read_cm.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning("read_cm __aexit__ failed during reset: %s", exc)

    def _reset(self) -> None:
        """同步入口：从主线程调用时把 async cleanup 调度到 event loop 上执行。

        用于 transport 异常路径（_do_call 同步上下文），timeout = config.timeout
        防止 cleanup 卡死时永久阻塞主线程。
        """
        loop = self._loop
        if loop is None:
            # 早期失败或 close 后再 reset：loop 已无，状态本应已清
            self._session = None
            self._read_cm = None
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._reset_async(), loop)
            fut.result(timeout=self._config.timeout)
        except Exception as exc:
            logger.warning("_reset async cleanup failed: %s", exc)
            # 兜底：状态必须清，否则下次调用会拿到坏 session
            self._session = None
            self._read_cm = None

    # ── 公开 API ──

    def call_tool(self, name: str, args: dict) -> dict:
        """同步入口：MCP call → 归一 → dict。

        Returns:
            dict: 解析后的 MCP 响应（ragent-py 工具通常为 {matches: [...], degraded: bool}）

        Raises:
            MCPBoundaryError: code 显式分类（MCP_TIMEOUT / MCP_UNAVAILABLE / MCP_INVALID_RESPONSE）
        """
        raw_text = self._call_with_retry(name, args)
        return self._classify_response(raw_text)

    # ── 调用链内部函数 ──

    def _call_with_retry(self, name: str, args: dict) -> str:
        """仅 MCP_TIMEOUT 触发 retry，max_attempts=2。

        不再调用 self._reset()：timeout 路径的 cleanup 已由 _do_call 内
        _cancel_and_drain 触发 coroutine 自清完成（per-invocation isolation）。
        """
        last_timeout: Optional[MCPBoundaryError] = None
        for attempt in (1, 2):
            try:
                return self._do_call(name, args)
            except MCPBoundaryError as exc:
                if exc.code is not MCPErrorCode.MCP_TIMEOUT:
                    raise
                last_timeout = exc
                if attempt == 2:
                    raise
                # 不再 self._reset() — _do_call 内的 _cancel_and_drain
                # 已让 coroutine 跑完 cleanup，state 干净可重试
        # unreachable
        if last_timeout is not None:
            raise last_timeout
        raise MCPBoundaryError(
            MCPErrorCode.MCP_TIMEOUT, "retry loop exited unexpectedly"
        )

    def _drain_coroutine(
        self, future: "asyncio.Future", done_event: asyncio.Event, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Drain coroutine to completion via done_event signal（review 修订第 4 轮）。

        关键不变量：coroutine 完整跑完（含 cleanup）才允许 retry。

        实现：等待 done_event.set()（在 _call_async finally 块，try/except
        的 awaits 之后）。**不**依赖 concurrent.futures.Future.result() 的
        隐式等待行为——显式信号更稳，不受 asyncio 内部 future 取消语义变更影响。

        future.result() 也调一次作为双保险（如果 cleanup 已完成则立刻返回）。
        """
        try:
            future.result(timeout=self._config.timeout)
        except (Exception, asyncio.CancelledError):
            # 正常路径：coroutine 完成（cancelled state 或原异常）
            pass
        # 等待 done_event（authoritative signal）
        try:
            done_future = asyncio.run_coroutine_threadsafe(done_event.wait(), loop)
            done_future.result(timeout=self._config.timeout)
        except (Exception, asyncio.CancelledError):
            # 兜底：coroutine 永远会跑到 finally set done_event；除非 cleanup
            # 卡死超 config.timeout。log warning 不抛。
            logger.warning(
                "coroutine drain timeout: cleanup may not have finished; "
                "retry will proceed but state might be unclean"
            )

    def _do_call(self, name: str, args: dict) -> str:
        """执行一次 MCP 调用并提取 text。Transport 异常归一为 MCPBoundaryError。

        Timeout 路径关键流程（review 修订）：
          1. future.result(timeout) 抛 TimeoutError
          2. future.cancel() —— 让 coroutine 进入 CancelledError 路径
          3. _drain_coroutine —— 等 done_event（authoritative 完成信号）
          4. 此时 coroutine 已完整跑完 cleanup，state 干净
          5. 抛 MCP_TIMEOUT → retry 路径拿到干净 state

        注意：try/except 范围只包 future.result()；_extract_text 在外。
        """
        self._config.validate()
        loop = self._ensure_loop()
        done_event = asyncio.Event()
        future = asyncio.run_coroutine_threadsafe(
            self._call_async(name, args, done_event), loop
        )
        try:
            result = future.result(timeout=self._config.timeout)
        except asyncio.TimeoutError as exc:
            # future.cancel() 返回值：如果 False 表示 future 已进入完成态
            # （通常因为 coroutine 在 cancel() 之前就完成了）；不影响逻辑。
            future.cancel()
            self._drain_coroutine(future, done_event, loop)
            raise MCPBoundaryError(
                MCPErrorCode.MCP_TIMEOUT,
                f"MCP call timeout after {self._config.timeout}s",
            ) from exc
        except MCPBoundaryError:
            # Coroutine 自己 raise MCPBoundaryError（其 except 已清 session）
            future.cancel()  # 保险：确保 future done
            self._drain_coroutine(future, done_event, loop)
            raise
        except (
            ConnectionError,
            BrokenPipeError,
            ProcessLookupError,
            OSError,
        ) as exc:
            future.cancel()
            self._drain_coroutine(future, done_event, loop)
            raise MCPBoundaryError(
                MCPErrorCode.MCP_UNAVAILABLE,
                f"MCP connection failure: {type(exc).__name__}: {exc}",
            ) from exc
        except Exception as exc:  # 兜底：subprocess spawn fail / SDK 未知异常
            future.cancel()
            self._drain_coroutine(future, done_event, loop)
            raise MCPBoundaryError(
                MCPErrorCode.MCP_UNAVAILABLE,
                f"MCP call failed: {type(exc).__name__}: {exc}",
            ) from exc
        return _extract_text(result)

    async def _call_async(
        self, name: str, args: dict, done_event: asyncio.Event
    ) -> Any:
        """后台循环内执行：建立/复用会话 + call_tool + 自清理 + 完成信号。

        Per-invocation session 所有权（review 修订）：
        本 coroutine **拥有**自己建立的 session，cleanup 用 local capture 不用
        全局 self._session——避免与并发 retry 的新 session 身份混淆（参见
        _do_call timeout 路径注释）。Lock 仍然保证同时只有一个 _call_async
        在修改 self._session，但 cleanup 走 local ref 更显式。

        完成信号（review 修订第 4 轮）：
        done_event 在 finally 中 set()——**永远**在 except 块的 awaits 之后
        执行（Python 语言保证：try/except/finally 中 finally 必跑）。主线程
        等待 done_event 是"coroutine 完整跑完（含 cleanup）"的权威信号，
        不依赖 concurrent.futures.Future 的 result() 取消语义。
        """
        try:
            async with self._call_lock:
                session_local: Optional[Any] = None
                read_cm_local: Optional[Any] = None
                try:
                    session = await self._ensure_session()
                    session_local = session
                    read_cm_local = self._read_cm
                    return await session.call_tool(name, args)
                except (Exception, asyncio.CancelledError):
                    # 用 local capture 关闭 THIS invocation 的 session；
                    # 同时清全局状态以便 retry 拿干净视图。
                    if session_local is not None:
                        try:
                            await session_local.__aexit__(None, None, None)
                        except Exception as exc:
                            logger.warning("session __aexit__ failed: %s", exc)
                    if read_cm_local is not None:
                        try:
                            await read_cm_local.__aexit__(None, None, None)
                        except Exception as exc:
                            logger.warning("read_cm __aexit__ failed: %s", exc)
                    self._session = None
                    self._read_cm = None
                    raise
        finally:
            # 完成信号：set 永远在 except 块的 awaits 之后执行。
            # 主线程 _do_call 的 _drain_coroutine 等待此事件。
            done_event.set()

    # ── 响应分类器（Q2 决议）──

    def _classify_response(self, raw_text: str) -> dict:
        """响应分类器（Q2 决议）。

        规则（5 桶）：
          1. 顶层 JSON object → 直接返回（协议形态合法；业务字段由 tool 层校验）
          2. 已知空命中前缀 → {"matches": [], "_note": raw_text}（runtime annotation）
          3. 协议错误前缀 → MCP_INVALID_RESPONSE（我方 bug）
          4. 运行时不可达前缀 → MCP_UNAVAILABLE
          5. 未知非 JSON / JSON 数组 / 标量 / 空 → MCP_INVALID_RESPONSE（fail loud）

        `_note` / `degraded` 是 mcp_client 内部 runtime annotation，
        必须在 tool 层构造稳定输出时被过滤，不进 §A Agent-facing schema（Q8 ⑧）。
        """
        if not raw_text:
            raise MCPBoundaryError(
                MCPErrorCode.MCP_INVALID_RESPONSE, "empty response"
            )

        # 路径 1: 顶层必须是 JSON object
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, dict):
            return parsed
        # JSON 数组 / 标量 / 非法 JSON → 走非 JSON 路径按前缀分类

        # 路径 2: 已知空命中
        if raw_text.startswith(EMPTY_PREFIXES):
            return {"matches": [], "_note": raw_text}

        # 路径 3: 协议错误
        if raw_text.startswith(INVALID_PREFIXES):
            raise MCPBoundaryError(
                MCPErrorCode.MCP_INVALID_RESPONSE, raw_text
            )

        # 路径 4: 运行时不可达
        if raw_text.startswith(UNAVAILABLE_PREFIXES):
            raise MCPBoundaryError(
                MCPErrorCode.MCP_UNAVAILABLE, raw_text
            )

        # 路径 5: 未知
        raise MCPBoundaryError(
            MCPErrorCode.MCP_INVALID_RESPONSE,
            f"unrecognized: {raw_text[:80]}",
        )

    # ── 清理 ──

    def close(self) -> None:
        """全量清理：退出 MCP 会话（杀子进程）+ 停后台循环线程。幂等。

        进程退出/测试结束必须调用——否则 ragent-py mcp_server 子进程在 Windows
        上不会随父进程自动终止，会孤儿化。
        """
        loop = self._loop
        if loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._close_async(), loop)
            fut.result(timeout=self._config.timeout)
        except Exception as exc:
            logger.warning("RagMCPClient close error: %s", exc)
        try:
            loop.call_soon_threadsafe(loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
        except Exception:
            pass
        self._session = None
        self._read_cm = None
        self._call_lock = None
        self._loop = None
        self._thread = None

    async def _close_async(self) -> None:
        """close 时的 async cleanup。委托 _reset_async 复用 cleanup 路径。"""
        await self._reset_async()


# ── 模块级单例 ──


_client: Optional[RagMCPClient] = None


def get_rag_mcp_client() -> RagMCPClient:
    """获取进程级 RagMCPClient 单例。"""
    global _client
    if _client is None:
        _client = RagMCPClient()
    return _client


def close_rag_mcp_client() -> None:
    """关闭进程级单例（若已初始化）。幂等——无 _client 时为 no-op。"""
    global _client
    if _client is not None:
        _client.close()
        _client = None


# ── 协议层独立函数 ──


def _validate_matches_contract(result: Any) -> list[dict]:
    """校验 + normalize MCP result.matches（review 第 3 轮 P1 修订）。

    行为：
      - result 不是 dict → MCP_INVALID_RESPONSE
      - result 缺 matches 字段 → MCP_INVALID_RESPONSE（协议错；review 第 3 轮修订）
      - matches 不是 list → MCP_INVALID_RESPONSE
      - 每个 item 必须含 text(str) + score(numeric)，否则 → MCP_INVALID_RESPONSE
      - 每个 item 的内部字段（chunk_id / document_id / embedding / rerank_score /
        kb_id）在 boundary strip，tool 层只见稳定契约（plan 决策 3）

    Empty matches（[]）是合法 EMPTY_RESULT——通过校验返回 []，与 mcp_client
    classifier 的 EMPTY_PREFIXES 一致。
    """
    if not isinstance(result, dict):
        raise MCPBoundaryError(
            MCPErrorCode.MCP_INVALID_RESPONSE,
            f"result must be dict, got {type(result).__name__}",
        )
    if "matches" not in result:
        # 缺 matches 字段：协议错（review 第 3 轮修订）。
        # 区别于 EMPTY_RESULT（matches=[] 合法）；不允许把 schema drift
        # （如 {"results": [...]} 或 {}）当成「合法检索只是没命中」。
        raise MCPBoundaryError(
            MCPErrorCode.MCP_INVALID_RESPONSE,
            "missing required field 'matches'",
        )
    matches = result["matches"]
    if not isinstance(matches, list):
        raise MCPBoundaryError(
            MCPErrorCode.MCP_INVALID_RESPONSE,
            f"matches must be list, got {type(matches).__name__}",
        )
    normalized: list[dict] = []
    for i, it in enumerate(matches):
        if not isinstance(it, dict):
            raise MCPBoundaryError(
                MCPErrorCode.MCP_INVALID_RESPONSE,
                f"matches[{i}] must be dict, got {type(it).__name__}",
            )
        text = it.get("text")
        if not isinstance(text, str):
            raise MCPBoundaryError(
                MCPErrorCode.MCP_INVALID_RESPONSE,
                f"matches[{i}].text missing or not str (got {type(text).__name__ if text is not None else 'None'})",
            )
        score = it.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise MCPBoundaryError(
                MCPErrorCode.MCP_INVALID_RESPONSE,
                f"matches[{i}].score missing or not numeric (got {type(score).__name__ if score is not None else 'None'})",
            )
        normalized.append(_strip_internal_fields(it))
    return normalized


# ragent-py 内部字段（review 第 3 轮 P1 修订）：boundary 处 strip，tool 层不见。
# 稳定契约（plan 决策 3）：items[] = {text, score, title?, section_path?}
_INTERNAL_RESULT_FIELDS = frozenset({
    "chunk_id", "document_id", "embedding", "rerank_score", "kb_id",
})


def _strip_internal_fields(item: dict) -> dict:
    """去掉 ragent-py 内部字段；保留稳定契约字段（text/score/title/section_path）。"""
    return {k: v for k, v in item.items() if k not in _INTERNAL_RESULT_FIELDS}


def _extract_text(result: Any) -> str:
    """从 CallToolResult 提取纯 text payload。空内容/非 text → INVALID_RESPONSE。

    ragent-py MCP server 当前仅用 text content（isError 永为 False）；
    保留对未来 server 形态的兼容性。

    Raises:
        MCPBoundaryError(MCP_INVALID_RESPONSE, ...): content 为空 / 含非 text 内容
    """
    contents = getattr(result, "content", None) or []
    if not contents:
        raise MCPBoundaryError(
            MCPErrorCode.MCP_INVALID_RESPONSE, "empty CallToolResult content"
        )
    text_parts = [c.text for c in contents if getattr(c, "type", "") == "text"]
    non_text = [c for c in contents if getattr(c, "type", "") != "text"]
    if non_text:
        kinds = sorted({type(c).__name__ for c in non_text})
        raise MCPBoundaryError(
            MCPErrorCode.MCP_INVALID_RESPONSE, f"non-text content present: {kinds}"
        )
    return "\n".join(text_parts)
