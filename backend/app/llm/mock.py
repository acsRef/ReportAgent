from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from app.llm.adapter import StructuredParseError, _validate_against_schema

logger = logging.getLogger(__name__)

# kind 小写蛇形；seq ≥ 1。如 "sql_generate:1"、"requirement_parse:2"。
# Fix 4：手写 fixture 时漏冒号 / 大小写错 / seq=0 都会静默成「永远 miss」——
# 在加载时显式拒绝，让 fixture 作者立刻看到 typo。
_FIXTURE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*:[1-9][0-9]*$")

# 语义 kind → prompt 内的固定标识（各 prompt 的 system_contract 首句，P7 常量）。
# 顺序无依赖（marker 互不为子串）；匹配用「包含」，因此 prompt 前置注入的
# assembled_context / 动态日期 / schema_text 漂移都不影响分类——这是 Contract
# fixture 在 CI 逐日稳定的关键（纯 SHA-256(prompt) 会因「当前日期」每日失效）。
KIND_MARKERS: list[tuple[str, str]] = [
    ("intent_classify", "你是 ReportAgent 的意图分类器。"),
    ("requirement_parse", "你是 ReportAgent 需求解析器。"),
    ("sql_intent_analyze", "你是 ReportAgent 意图分析器。"),
    ("sql_plan", "你是 ReportAgent SQL 规划器。"),
    ("sql_generate", "你是 ReportAgent SQL 生成专家。"),
    ("report_plan", "你是 ReportAgent 报告规划师。"),
]


class MockLLMMiss(Exception):
    """mock fixture 未命中（或 LLM_MOCK_* env 缺失 / prompt 无法归类）时明确失败。

    Contract E2E 需要在缺失 fixture 时立刻暴露，而不是让 mock 返回兜底文案污染断言。
    """


def prompt_kind(prompt: str | list) -> str:
    """prompt → 语义 kind（固定 marker 匹配，支持 list 形态的 chat messages）。"""
    text = prompt if isinstance(prompt, str) else json.dumps(prompt, ensure_ascii=False)
    for kind, marker in KIND_MARKERS:
        if marker in text:
            return kind
    raise MockLLMMiss(
        f"无法从 prompt 识别语义 kind（no marker matched）。KIND_MARKERS: "
        f"{[k for k, _ in KIND_MARKERS]}"
    )


def _load_case(fixtures_dir: Path, case_id: str) -> dict[str, Any]:
    path = fixtures_dir / f"{case_id}.json"
    if not path.exists():
        raise MockLLMMiss(f"no fixture file for case {case_id}: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise MockLLMMiss(f"fixture {path} must be a dict[`kind:seq`, response]")
    # Fix 4：key 格式校验（手写 fixture typo 早暴露，避免静默 miss）
    for k in data:
        if not _FIXTURE_KEY_RE.match(k):
            raise MockLLMMiss(
                f"fixture {path}: key {k!r} 不符合 kind:N 格式（kind 需小写蛇形如 'sql_generate'，"
                f"seq 为 ≥1 的正整数，如 'sql_generate:1'）"
            )
    return data


class MockLLMAdapter:
    """Fixture 驱动的假 LLM，接口对齐 LLMAdapter（generate / generate_structured）。

    供 Contract E2E 在无真实 provider key 时使用：读 `{fixtures_dir}/{case_id}.json`
    （dict[`kind:seq`, response]）。key 由 prompt 的语义 kind + 调用序构成：
      - 语义 kind 来自 prompt 固定 marker（不受日期/schema 漂移影响）
      - seq 是同一 kind 在本 backend 进程内被调用的次数（repair：sql_generate:1 → :2）
    未命中一律抛 MockLLMMiss。fixture 值为 dict 时原样返回（caller 均 safe_json_parse
    兜底 dict）；为 str 时返回 str（sql_generate 的 SQL 文本）。
    """

    def __init__(self, fixtures_dir: Path, case_id: str, delay_ms: int = 0) -> None:
        self._case_id = case_id
        self._responses = _load_case(fixtures_dir, case_id)
        self._counters: dict[str, int] = {}
        # 仅 mock 模式用：人为延迟 generate / generate_structured，扩展 LLM 调用窗口。
        # 用途：Contract spec 07 background-execution 需要 generating 窗口足够长
        # 让停止按钮可点击。LLM_MOCK_DELAY_MS=3000 → 每次 mock LLM 调用先 sleep 3s。
        # 注意：仅 mock；不污染真 LLM 路径。
        self._delay_ms = delay_ms

    @classmethod
    def from_env(cls) -> "MockLLMAdapter":
        fixtures_dir = os.getenv("LLM_MOCK_DIR")
        case_id = os.getenv("LLM_MOCK_CASE")
        if not fixtures_dir or not case_id:
            raise MockLLMMiss(
                "LLM_PROVIDER=mock 需要 LLM_MOCK_DIR 与 LLM_MOCK_CASE env（plan D3）"
            )
        delay_ms = int(os.getenv("LLM_MOCK_DELAY_MS", "0") or "0")
        return cls(Path(fixtures_dir), case_id, delay_ms=delay_ms)

    def generate(self, prompt: str | list, **kwargs: Any) -> Any:
        # 返回 fixture 原值：dict 由 caller 的 `isinstance(raw, str) else raw` 直接吃，
        # str（SQL 文本）也会原样返回。mock 不需要 json.dumps 序列化。
        return self._lookup(prompt)

    def generate_structured(
        self,
        prompt: str | list,
        schema: Any | None = None,
        **kwargs: Any,
    ) -> dict:
        resp = self._lookup(prompt)
        if not isinstance(resp, dict):
            raise StructuredParseError(
                f"case {self._case_id}: fixture for key {prompt_kind(prompt)} "
                "must be dict for generate_structured"
            ) from None
        if schema is None:
            return resp
        # 与真实 generate_structured 同一 schema 校验入口：model_validate + model_dump
        return _validate_against_schema(resp, schema)

    def generate_structured_safe(
        self,
        prompt: str | list,
        parser: Any | None = None,
        schema: Any | None = None,
        **kwargs: Any,
    ) -> dict:
        # mock 无真实 parse 失败需要兜底；直接走 generate_structured，错误自然上抛
        return self.generate_structured(prompt, schema=schema, **kwargs)

    def _lookup(self, prompt: str | list) -> Any:
        kind = prompt_kind(prompt)
        seq = self._counters.get(kind, 0) + 1
        self._counters[kind] = seq
        key = f"{kind}:{seq}"
        if key not in self._responses:
            logger.warning("MockLLMMiss case=%s key=%s", self._case_id, key)
            raise MockLLMMiss(
                f"case {self._case_id}: no fixture for `{key}`（kind={kind}）"
            )
        if self._delay_ms > 0:
            import time
            time.sleep(self._delay_ms / 1000)
        return self._responses[key]