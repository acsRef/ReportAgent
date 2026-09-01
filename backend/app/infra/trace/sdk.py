from __future__ import annotations

import asyncio
import functools
import logging
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable, Generator, Optional

from app.infra.trace.models import LLMCall, Span, Trace
from app.infra.trace.repository import TraceRepository
from app.observability.langfuse_config import LangfuseConfig
from app.observability.redaction import redact, redact_user_query

logger = logging.getLogger(__name__)

# flush 总超时：DB 卡住时不能无限挂起 SSE 响应，超时降级为告警并放行主流程。
_FLUSH_TIMEOUT = float(os.getenv("TRACE_FLUSH_TIMEOUT", "10.0"))

_local: dict[str, "Tracer"] = {}

# C-4: 当前请求的 tracer。此前 llm.py 用 `for _t in _local.values()` 把一次
# LLM 调用记到所有在途 tracer 上（span attribution 错位）；`_local` 还只在
# flush 成功时 pop，异常路径永久泄露。改成 ContextVar：traced_node 进入节点时
# set、退出时 reset，llm.py 只往「当前」tracer 记。sync 节点虽跑在执行器线程，
# 但 set 发生在该线程内的 wrapper 入口，故同一线程深处的 call_llm 仍可见。
_current_tracer: ContextVar[Optional["Tracer"]] = ContextVar("current_tracer", default=None)


def current_tracer() -> Optional["Tracer"]:
    """返回当前执行上下文里的 tracer（无则 None）。供 llm.py 精确归属 LLM 调用。"""
    return _current_tracer.get()


class Tracer:
    """Accumulates trace/spans in memory; caller must await flush() at the end."""

    def __init__(self, trace_id: str, session_id: Optional[str] = None,
                 user_query: Optional[str] = None, user_id: Optional[int] = None):
        self.trace_id = trace_id
        self.session_id = session_id
        # P13: user_query 入口统一 PII mask（PG / Langfuse 落库的都是脱敏值）。
        # 注意: None 必须保持 None——A-3 无主创建后再 backfill 的身份补齐依赖
        # `self.user_query is None` 判定，不能把 None 当作 "" 短路掉回填。
        self.user_query = redact_user_query(user_query) if user_query is not None else None
        self.user_id = user_id
        self._start_time = datetime.now()
        self._trace = Trace(
            trace_id=trace_id,
            session_id=session_id,
            user_id=user_id,
            user_query=self.user_query,  # 已 mask
            status="RUNNING",
            start_time=self._start_time,
        )
        self._spans: list[Span] = []
        self._llm_calls: list[LLMCall] = []
        self._prompt_versions: list[dict] = []  # P7 D3: 本地 prompt 版本追踪
        self._decisions: list[dict] = []  # P8 D5: Diagnose 决策本地记录
        self._stack: list[Span] = []

    def backfill_identity(self, session_id: Optional[str] = None,
                          user_query: Optional[str] = None,
                          user_id: Optional[int] = None) -> None:
        """A-3：tracer 可能先被 traced_node 无主创建（只有 trace_id），
        main.py 的四个 trace 起点随后 priming 时把身份补齐。只补不覆盖。"""
        if session_id is not None and self.session_id is None:
            self.session_id = session_id
            self._trace.session_id = session_id
        if user_query is not None and self.user_query is None:
            self.user_query = redact_user_query(user_query)
            self._trace.user_query = self.user_query
        if user_id is not None and self.user_id is None:
            self.user_id = user_id
            self._trace.user_id = user_id

    def start(self):
        pass

    def end(self, status: str = "SUCCESS"):
        end_time = datetime.now()
        self._trace.status = status
        self._trace.end_time = end_time
        self._trace.total_duration_ms = int((end_time - self._start_time).total_seconds() * 1000)

    @contextmanager
    def span(self, name: str, span_type: str = "NODE",
             input: Any = None) -> Generator[Span, None, None]:
        span = Span(
            trace_id=self.trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=self._current_span_id(),
            span_name=name,
            span_type=span_type,
            start_time=datetime.now(),
            input=input,
        )
        self._stack.append(span)
        try:
            yield span
            span.status = "SUCCESS"
            span.end_time = datetime.now()
            span.duration_ms = int((span.end_time - span.start_time).total_seconds() * 1000)
        except Exception as e:
            span.status = "FAILED"
            span.end_time = datetime.now()
            span.duration_ms = int((span.end_time - span.start_time).total_seconds() * 1000)
            span.error = str(e)
            raise
        finally:
            self._stack.pop()
            self._spans.append(span)

    def add_llm_call(self, model: str, prompt_tokens: int, completion_tokens: int, latency_ms: int) -> None:
        """Record an LLM call associated with the current span."""
        span_id = self._current_span_id() or ''
        self._llm_calls.append(LLMCall(
            span_id=span_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        ))

    def add_prompt_version(self, name: str, version: int | str) -> None:
        """P7 D3: 记录当前请求使用的 prompt 版本。

        每次 build_xxx_prompt(...) 调用前先调用此方法,关联到当前 span。
        本地记录至 `_prompt_versions` list,Langfuse 实际接入留 P13。

        调用时机约定: caller 在节点内调 LLM 前调用; 不强制要求在 span 内
        (有则记 span_id,无则 span_id="" 兜底)。
        """
        span_id = self._current_span_id() or ''
        self._prompt_versions.append({
            "span_id": span_id,
            "name": name,
            "version": version,
        })

    def add_decision(self, name: str, **fields: Any) -> None:
        """P8 D5: 记录 Diagnose 决策。

        本地记录至 `_decisions` list, 关联当前 span, P13 Langfuse 接入时落库。
        """
        span_id = self._current_span_id() or ''
        self._decisions.append({
            "span_id": span_id,
            "name": name,
            **fields,
        })

    def _current_span_id(self) -> Optional[str]:
        return self._stack[-1].span_id if self._stack else None

    async def flush(self):
        """双 sink（PG + Langfuse）落库 trace/spans，但绝不让 DB/网络卡住挂死调用方。

        整体包一层 `asyncio.wait_for` 总超时：超时或任何异常都只告警、不重抛，
        让 SSE 主流程继续；`finally` 兜底释放 _local 桶（C-4）。
        Langfuse 是否启用由 _flush 内 env 探测决定（P13，未设 LANGFUSE_* 仅 PG）。
        """
        try:
            try:
                await asyncio.wait_for(self._flush(), timeout=_FLUSH_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(
                    "trace flush timed out after %ss for trace_id=%s",
                    _FLUSH_TIMEOUT, self.trace_id,
                )
            except Exception as exc:
                logger.warning("trace flush failed for trace_id=%s: %s", self.trace_id, exc)
        finally:
            # C-4: 无论落库成败都要释放桶，否则异常路径下 tracer 永驻 _local → 内存泄露。
            _local.pop(self.trace_id, None)

    async def _flush(self):
        """双 sink 内部入口：PG 与 Langfuse 独立 best-effort，互不阻塞。"""
        try:
            await _flush_pg(self)
        except Exception as exc:
            # _flush_pg 对单操作已 catch，这里的兜底防 repo 构造等意外错误。
            logger.warning("trace pg sink failed for trace_id=%s: %s", self.trace_id, exc)
        if LangfuseConfig().enabled:
            await _flush_langfuse(self)


async def _flush_pg(tracer: "Tracer") -> None:
    """PG sink：原 _flush_db 逻辑（module-level 便于测试 patch）。单点失败不拖垮其余。"""
    repo = TraceRepository()
    try:
        await repo.save_trace(tracer._trace)
    except Exception as exc:
        logger.warning("save_trace failed for trace_id=%s: %s", tracer.trace_id, exc)
    for llm_call in tracer._llm_calls:
        try:
            await repo.save_llm_call(llm_call)
        except Exception as exc:
            logger.warning("save_llm_call failed for trace_id=%s: %s", tracer.trace_id, exc)
    for span in tracer._spans:
        try:
            await repo.save_span(span)
        except Exception as exc:
            logger.warning("save_span failed for trace_id=%s: %s", tracer.trace_id, exc)


async def _flush_langfuse(tracer: "Tracer") -> None:
    """Langfuse sink：env-gated（_flush 已 gate，此处再防御直接调用）。失败 best-effort。"""
    cfg = LangfuseConfig()
    if not cfg.enabled:
        return
    from app.observability.langfuse_flush import flush_to_langfuse
    await flush_to_langfuse(tracer, langfuse_config=cfg)


def get_tracer(trace_id: str, session_id: Optional[str] = None,
               user_query: Optional[str] = None,
               user_id: Optional[int] = None) -> Tracer:
    if trace_id not in _local:
        _local[trace_id] = Tracer(trace_id, session_id, user_query, user_id=user_id)
    else:
        # A-3：已存在则回填——traced_node 先建的无主 tracer 在 priming 时补齐身份。
        _local[trace_id].backfill_identity(session_id, user_query, user_id)
    return _local[trace_id]


def traced_node(name: str, span_type: str = "NODE"):
    """Decorator that records a span in-memory during node execution.

    Supports both sync and async node functions.
    Spans are flushed to PostgreSQL by calling tracer.flush() after the run.
    """
    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(state: dict, **kwargs):
                trace_id = state.get("trace_id", "")
                tracer = get_tracer(trace_id)
                token = _current_tracer.set(tracer)
                try:
                    with tracer.span(name, span_type=span_type, input=_summarize_state(state)):
                        result = await func(state, **kwargs)
                    _handle_output_span(tracer, result)
                    return result
                finally:
                    _current_tracer.reset(token)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(state: dict, **kwargs):
                trace_id = state.get("trace_id", "")
                tracer = get_tracer(trace_id)
                token = _current_tracer.set(tracer)
                try:
                    with tracer.span(name, span_type=span_type, input=_summarize_state(state)):
                        result = func(state, **kwargs)
                    _handle_output_span(tracer, result)
                    return result
                finally:
                    _current_tracer.reset(token)
            return sync_wrapper
    return decorator


def _handle_output_span(tracer: Tracer, result: Any):
    if not result:
        return
    r = deepcopy(result)
    for k in list(r.keys()):
        if isinstance(r[k], (list, dict)) and len(str(r[k])) > 200:
            r[k] = f"<{type(r[k]).__name__}: {len(str(r[k]))} chars>"
    with tracer.span(f"{tracer._stack[-1].span_name if tracer._stack else 'node'}_output",
                     span_type="DATA", input=redact(r)):  # P13: PII mask
        pass


def _summarize_state(state: dict) -> dict:
    raw: dict[str, Any] = {}
    for k, v in state.items():
        if k == "user_query":
            raw[k] = v
        elif isinstance(v, (str, int, float, bool, type(None))):
            raw[k] = v
        elif isinstance(v, dict) and len(str(v)) > 100:
            raw[k] = f"<dict: {len(str(v))} chars>"
        elif isinstance(v, list):
            raw[k] = f"<list: {len(v)} items>"
    return redact(raw)  # P13: user_query 等字符串统一 PII mask