from __future__ import annotations

import asyncio
import functools
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable, Generator, Optional

from app.infra.trace.models import LLMCall, Span, Trace
from app.infra.trace.repository import TraceRepository

_local: dict[str, "Tracer"] = {}


class Tracer:
    """Accumulates trace/spans in memory; caller must await flush() at the end."""

    def __init__(self, trace_id: str, session_id: Optional[str] = None, user_query: Optional[str] = None):
        self.trace_id = trace_id
        self.session_id = session_id
        self.user_query = user_query
        self._start_time = datetime.now()
        self._trace = Trace(
            trace_id=trace_id,
            session_id=session_id,
            user_query=user_query,
            status="RUNNING",
            start_time=self._start_time,
        )
        self._spans: list[Span] = []
        self._llm_calls: list[LLMCall] = []
        self._stack: list[Span] = []

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

    def _current_span_id(self) -> Optional[str]:
        return self._stack[-1].span_id if self._stack else None

    async def flush(self):
        repo = TraceRepository()
        try:
            await repo.save_trace(self._trace)
        except Exception:
            pass
        for llm_call in self._llm_calls:
            try:
                await repo.save_llm_call(llm_call)
            except Exception:
                pass
        for span in self._spans:
            try:
                await repo.save_span(span)
            except Exception:
                pass
        _local.pop(self.trace_id, None)


def get_tracer(trace_id: str, session_id: Optional[str] = None,
               user_query: Optional[str] = None) -> Tracer:
    if trace_id not in _local:
        _local[trace_id] = Tracer(trace_id, session_id, user_query)
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
                with tracer.span(name, span_type=span_type, input=_summarize_state(state)):
                    result = await func(state, **kwargs)
                _handle_output_span(tracer, result)
                return result
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(state: dict, **kwargs):
                trace_id = state.get("trace_id", "")
                tracer = get_tracer(trace_id)
                with tracer.span(name, span_type=span_type, input=_summarize_state(state)):
                    result = func(state, **kwargs)
                _handle_output_span(tracer, result)
                return result
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
                     span_type="DATA", input=r):
        pass


def _summarize_state(state: dict) -> dict:
    summary: dict[str, Any] = {}
    for k, v in state.items():
        if k == "user_query":
            summary[k] = v
        elif isinstance(v, (str, int, float, bool, type(None))):
            summary[k] = v
        elif isinstance(v, dict) and len(str(v)) > 100:
            summary[k] = f"<dict: {len(str(v))} chars>"
        elif isinstance(v, list):
            summary[k] = f"<list: {len(v)} items>"
    return summary