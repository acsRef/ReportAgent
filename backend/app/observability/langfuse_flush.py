"""tracer → Langfuse 转换器。

把 in-memory spans / llm_calls / prompt_versions / decisions 转成 Langfuse
observation 调用（langfuse SDK v4：OTel-based `start_as_current_observation`，
自定义 trace_id 走 `trace_context`，与 PG sink 同 id 可双向关联）。
PII 在入参前过 redact()；Langfuse SDK 失败 best-effort 不抛。

P13 Review 后的拓扑：
  report_agent_run (root)
  ├── llm_call (无 span_id 兜底挂 root)
  ├── <traced_node span>            # nested via OTel context
  │   ├── llm_call (matched by span_id)  ← generation
  │   └── metadata: decisions / prompt_version / execution_duration_ms / original_start_time / original_end_time
  └── llm_call (匹配的 span 不存在的兜底)

Timing 语义分层（plan: docs/plans/2026-09-01-p13-langfuse-execution-timing.md）：
- Span observation.metadata.execution_duration_ms = 节点业务总耗时（含 LLM 往返）
- LLM observation.metadata.latency_ms             = LLM 单独往返耗时
- Langfuse native duration                        = observation 进出 flush 上下文的时间（不可信，仅参考）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any

from app.observability.langfuse_client import get_langfuse_client
from app.observability.langfuse_config import LangfuseConfig
from app.observability.redaction import redact

if TYPE_CHECKING:
    from app.infra.trace.sdk import Tracer

logger = logging.getLogger(__name__)

# 根 observation 名：Langfuse UI 里一次请求一条 trace，trace_id == tracer.trace_id。
# 2026-11-08（plan trace-inspector）：默认名只在拿不到用户问题时用——UI 里一屏十几条
# `report_agent_run` 谁分得清哪条是哪轮？能拿到原话就用原话。
_ROOT_NAME = "report_agent_run"
_ROOT_QUESTION_CHARS = 40
# observation 的 input/output 上限（2026-11-08 真机）：一轮 trace 有几十个 observation，
# 每个都带完整结果集时批次会撑爆云端 5s 上报超时 → 整批丢弃、UI 空白。
_OBS_PAYLOAD_CHARS = 4000


def _bounded(value: Any) -> Any:
    """redact + 截断 observation 的 input/output（大 payload 只留头部并标注原文长度）。"""
    redacted = redact(value)
    if redacted is None:
        return None
    text = (
        redacted if isinstance(redacted, str)
        else json.dumps(redacted, ensure_ascii=False, default=str)
    )
    if len(text) <= _OBS_PAYLOAD_CHARS:
        return redacted
    return f"{text[:_OBS_PAYLOAD_CHARS]}…（截断，原 {len(text)} 字符）"


# 2026-11-08（plan trace-inspector）：Langfuse 按 observation 计费，而一轮完整流程会产生
# 二十几个 traced_node（再叠 DATA span 能到五十几）。全部上报既烧额度，又让上报包变大
# 撞满云端 5s 超时。所以分两层：
#   - 本地 `observability.*` 三张表：**全量**（自建、免费、排查要细节）；
#   - Langfuse：**只发人能看懂的阶段节点 + 失败节点 + LLM 调用**。
# env `LANGFUSE_TRACE_LEVEL=essential|full` 可切回全量。
_LANGFUSE_LEVEL_ENV = "LANGFUSE_TRACE_LEVEL"
_ESSENTIAL_NODES = frozenset({
    # 需求阶段
    "turn_understand", "requirement_parse", "requirement_project",
    # 执行阶段（容器 + 具体步骤）
    "confirmed_sql_agent", "sql_plan", "sql_generate", "sql_validate",
    "sql_execute", "sql_evaluate", "sql_diagnose",
    # 报告阶段
    "confirmed_report_agent", "report_build_output",
})


def _should_report(span_name: str, status: str, level: str) -> bool:
    """这个节点要不要发到 Langfuse。

    失败节点**无条件发**——排查时最有价值的就是第一个红掉的环节，
    哪怕它平时只是个不起眼的内部步骤。
    """
    if str(status or "").upper() == "FAILED":
        return True
    if level == "full":
        return True
    return span_name in _ESSENTIAL_NODES


def _root_name(tracer: "Tracer") -> str:
    """根 observation / trace 的人话名字：用户这一轮问的是什么。"""
    question = " ".join(str(getattr(tracer, "user_query", "") or "").split())
    if not question:
        return _ROOT_NAME
    if len(question) <= _ROOT_QUESTION_CHARS:
        return question
    return f"{question[:_ROOT_QUESTION_CHARS]}…"


def _node_human(name: str) -> tuple[str, str]:
    """技术节点名 → (中文标签, kind)；查不到就原样返回（不猜）。

    复用 `infra/execution/progress.PROGRESS_NODES`（前端进度卡用的同一张表），
    这样 Langfuse 里看到的「生成 SQL / 执行查询」和界面上完全一致。
    """
    from app.infra.execution.progress import PROGRESS_NODES

    for candidate in (name, name.removeprefix("confirmed_")):
        hit = PROGRESS_NODES.get(candidate)
        if hit:
            return hit[1], hit[0]
    return name, ""


def _langfuse_trace_id(trace_id: str) -> str:
    """把本仓库 UUID trace_id 转成 Langfuse 接受的 32 位小写 hex。

    Langfuse v4（OTel 内核）要求 trace_id 形如 `[0-9a-f]{32}`，带连字符的
    UUID 会触发 "invalid literal for int() with base 16" 并被 SDK 拒收。
    仅当是 36 位带连字符 UUID 时去连字符 + 小写（仍 32 hex，与 PG 一一可逆）；
    其它 id 原样透传。
    """
    if "-" in trace_id and len(trace_id) == 36:
        return trace_id.replace("-", "").lower()
    return trace_id


def _llm_call_obs(langfuse: Any, llm: Any):
    """包一层 LLM call generation（model + usage + latency_ms + input/output 进 observation）。"""
    return langfuse.start_as_current_observation(
        name="llm_call",
        as_type="generation",
        model=getattr(llm, "model", None),
        usage_details={
            "input": getattr(llm, "prompt_tokens", 0),
            "output": getattr(llm, "completion_tokens", 0),
        },
        # adapter 端已 redact 过一次，此处再走一遍防 span 之外的字典/列表深字段；
        # 同时截断（prompt 可能很长，会把云端批次撑爆）
        input=_bounded(getattr(llm, "input", None)),
        output=_bounded(getattr(llm, "output", None)),
        metadata={"latency_ms": getattr(llm, "latency_ms", 0)},
    )


def _timing_fields(
    duration_ms: Any,
    start_time: Any,
    end_time: Any,
    duration_key: str,
) -> dict[str, Any]:
    """把业务真实耗时字段回放到 metadata；None 字段过滤不入（防污染 Langfuse UI）。

    Span observation 用 duration_key="execution_duration_ms"（节点业务总耗时），
    root observation 用 duration_key="total_duration_ms"（整 trace 总耗时），
    ISO 时间戳字段名一致（与 PG 字段名对齐便于对账）。
    """
    out: dict[str, Any] = {}
    if duration_ms is not None:
        out[duration_key] = duration_ms
    if start_time is not None:
        out["original_start_time"] = start_time.isoformat()
    if end_time is not None:
        out["original_end_time"] = end_time.isoformat()
    return out


async def flush_to_langfuse(
    tracer: "Tracer",
    langfuse_config: LangfuseConfig | None = None,
) -> None:
    """把 tracer 的 spans/llm_calls/prompt_versions/decisions 转 Langfuse。Best-effort。

    按 span_id 建 parent-child 树（Agent → LLM causal attribution）：
    - LLM call 的 span_id 命中 _spans → generation 嵌套在对应 span observation 下
    - 决策的 span_id 命中 → 聚合到 span metadata 的 decisions 列表（防覆盖 + redact）
    - prompt version 同上
    - 未命中 / 空 span_id → 兜底挂 root（aggregated）

    业务真实耗时回放（plan 2026-09-01-p13-langfuse-execution-timing.md）：
    - Span observation metadata.execution_duration_ms / original_start_time / original_end_time
    - root observation metadata.total_duration_ms / original_start_time / original_end_time
    - LLM observation metadata.latency_ms（既有路径）
    - Langfuse native duration 仍按 SDK 进出 flush 上下文计——不可信，但 metadata 是真值
    """
    cfg = langfuse_config or LangfuseConfig()
    if not cfg.enabled:
        return

    langfuse = get_langfuse_client()
    if langfuse is None:
        return

    # 函数内 import：SDK 版本较老时 `propagate_attributes` 可能不存在，
    # 让 ImportError 只影响这一条 trace 的元数据，而不是整个模块导入失败。
    from contextlib import nullcontext

    from langfuse import propagate_attributes

    try:
        # 按 span_id 分组（空串表示无归属，兜底挂 root）
        llm_by_span: dict[str, list] = {}
        dec_by_span: dict[str, list] = {}
        pv_by_span: dict[str, list] = {}
        for llm in tracer._llm_calls:
            sid = getattr(llm, "span_id", None) or ""
            llm_by_span.setdefault(sid, []).append(llm)
        for d in tracer._decisions:
            sid = d.get("span_id") or ""
            dec_by_span.setdefault(sid, []).append(d)
        for pv in tracer._prompt_versions:
            sid = pv.get("span_id") or ""
            pv_by_span.setdefault(sid, []).append(pv)

        # 2026-11-08（plan trace-inspector）：「人性化」+ 减包两件事一起做。
        # `*_output` 这类 DATA span 是内部状态摘要：名字让人看不懂、数量把 observation
        # 翻了一倍（实测一轮 57 个，一半是它们），还撑着云端 5s 上报超时。
        # 这里把它的内容并进**父节点**的 output，自己不再单独上报。
        data_output: dict[str, Any] = {}
        node_spans = []
        for span in tracer._spans:
            if getattr(span, "span_type", "") == "DATA":
                parent_id = getattr(span, "parent_span_id", None)
                if parent_id:
                    data_output[parent_id] = getattr(span, "input", None)
                continue
            node_spans.append(span)

        # 2026-11-08：只把「人能看懂的阶段 + 失败节点」发给 Langfuse（本地表仍全量）。
        trace_level = (os.getenv(_LANGFUSE_LEVEL_ENV) or "essential").strip().lower()
        reported_spans = [
            span for span in node_spans
            if _should_report(span.span_name, getattr(span, "status", ""), trace_level)
        ]
        # 把「本地采了多少 / 云端发了多少」写进 trace metadata：排查时别以为节点丢了。
        # （下面 root_md 建好之后再落这两项——2026-11-08 第一版写在这里，直接 NameError。）
        root_md_extra = {
            "reported_nodes": len(reported_spans),
            "skip_nodes": len(getattr(tracer, "_spans", ()) or ()) - len(reported_spans),
            "trace_level": trace_level,
        }

        # 根 observation：trace_context 钉住 trace_id，metadata 保留原始 UUID 反查
        # + 整 trace 业务总耗时（与 PG traces.total_duration_ms 对账）
        trace_obj = getattr(tracer, "_trace", None)
        # 2026-11-08（plan trace-inspector）：metadata 补上「这是谁、哪一轮」——
        # 排查时第一眼要能确认「是不是这个用户这一轮」，而不是靠 trace_id 反查 PG。
        root_md: dict[str, Any] = {
            "pg_trace_id": tracer.trace_id,
            "session_id": getattr(tracer, "session_id", None),
            "user_id": getattr(tracer, "user_id", None),
            "question": getattr(tracer, "user_query", None),
            "node_count": len(getattr(tracer, "_spans", ()) or ()),
        }
        root_md = {k: v for k, v in root_md.items() if v not in (None, "")}
        root_md.update(root_md_extra)
        root_md.update(_timing_fields(
            getattr(trace_obj, "total_duration_ms", None),
            getattr(trace_obj, "start_time", None),
            getattr(trace_obj, "end_time", None),
            duration_key="total_duration_ms",
        ))
        # 2026-11-08（plan trace-inspector）：session_id / user_id / trace_name 走 SDK v4 的
        # `propagate_attributes`（`trace_context` 只认 trace_id，塞不进去）。设了它，
        # UI 里 trace 右上角才有可点的 `Session:` 链接——「这个会话的每一轮」点进去就能翻。
        attrs: dict[str, Any] = {}
        if getattr(tracer, "session_id", None):
            attrs["session_id"] = str(tracer.session_id)
        if getattr(tracer, "user_id", None) is not None:
            attrs["user_id"] = str(tracer.user_id)
        if getattr(tracer, "user_query", None):
            attrs["trace_name"] = _root_name(tracer)
        with (
            propagate_attributes(**attrs) if attrs else nullcontext(),
            langfuse.start_as_current_observation(
                name=_root_name(tracer),
                trace_context={"trace_id": _langfuse_trace_id(tracer.trace_id)},
                input=_bounded({"user_query": tracer.user_query}) if tracer.user_query else None,
                metadata=root_md or None,
            ),
        ):
            # 无匹配 span 的 LLM calls → 兜底挂 root
            for llm in llm_by_span.pop("", []):
                with _llm_call_obs(langfuse, llm):
                    pass

            # 每个 traced_node span → observation；同 span 的 decisions / pv 聚合到 metadata
            for span in reported_spans:
                span_md: dict[str, Any] = {}
                # 2026-11-08（plan trace-inspector）：UI 上直接给中文节点名（「生成 SQL」），
                # 技术名留在 metadata.node 里，方便和 PG spans.span_name 对账。
                human, kind = _node_human(span.span_name)
                if human != span.span_name:
                    span_md["node"] = span.span_name
                if kind:
                    span_md["kind"] = kind
                span_decisions = dec_by_span.pop(span.span_id, [])
                if span_decisions:
                    span_md["decisions"] = [redact(d) for d in span_decisions]
                span_pvs = pv_by_span.pop(span.span_id, [])
                if span_pvs:
                    span_md["prompt_version"] = {
                        "name": span_pvs[0].get("name"),
                        "version": span_pvs[0].get("version"),
                    }
                # 业务真实耗时：与 PG spans.duration_ms / start_time / end_time 一对一
                span_md.update(_timing_fields(
                    getattr(span, "duration_ms", None),
                    getattr(span, "start_time", None),
                    getattr(span, "end_time", None),
                    duration_key="execution_duration_ms",
                ))
                with langfuse.start_as_current_observation(
                    name=human,
                    # 2026-11-08：节点输出也进 observation（UI 里「这个节点算出了什么」）。
                    # 优先用 DATA span 记下的状态摘要（就是那个节点的产出），
                    # 没有再退回 span 自身的 output。
                    input=_bounded(span.input) if getattr(span, "input", None) else None,
                    output=_bounded(
                        data_output.get(span.span_id) or getattr(span, "output", None)
                    ),
                    metadata=span_md or None,
                ):
                    # 嵌套 LLM generations：沿 OTel context 自动挂到当前 span 下
                    for llm in llm_by_span.pop(span.span_id, []):
                        with _llm_call_obs(langfuse, llm):
                            pass

            # 兜底：未命中 span 的 decision / prompt version / llm_call → root
            extra_md: dict[str, Any] = {}
            remain_dec: list = []
            for ds in dec_by_span.values():
                remain_dec.extend(ds)
            if remain_dec:
                extra_md["decisions"] = [redact(d) for d in remain_dec]
            remain_pv: list = []
            for pvs in pv_by_span.values():
                remain_pv.extend(pvs)
            if remain_pv:
                extra_md["prompt_versions"] = [
                    {"name": pv.get("name"), "version": pv.get("version")}
                    for pv in remain_pv
                ]
            # 兜底 llm：对应 span 不存在的 LLM 调用作为 root generation
            remain_llm: list = []
            for lms in llm_by_span.values():
                remain_llm.extend(lms)
            for llm in remain_llm:
                with _llm_call_obs(langfuse, llm):
                    pass
            if extra_md:
                langfuse.update_current_span(metadata=extra_md)

        # SDK 是 sync HTTP，经 to_thread 不阻塞事件循环；wait_for 兜底超时
        try:
            await asyncio.wait_for(
                asyncio.to_thread(langfuse.flush),
                timeout=cfg.flush_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "langfuse flush timed out after %ss for trace_id=%s",
                cfg.flush_timeout, tracer.trace_id,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort：observability 失败不阻塞主流程
        logger.warning(
            "langfuse flush failed for trace_id=%s: %s", tracer.trace_id, exc,
        )