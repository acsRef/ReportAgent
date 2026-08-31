from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.llm.adapter import StructuredParseError, _validate_against_schema


class MockLLMMiss(Exception):
    """mock fixture 未命中（或 LLM_MOCK_* env 缺失）时明确失败——mock 不静默兜底。

    Contract E2E 需要在缺失 fixture 时立刻暴露，而不是让 mock 返回兜底文案污染断言。
    """


def _prompt_key(prompt: str | list) -> str:
    """prompt → 稳定 key（语义哈希）。

    v1 取 prompt 自身的 SHA-256（fixture 文件即按此 key 匹配）。同一 case 内多次相同
    prompt 需要不同响应时，由 T3 fixtures 阶段再叠加调用序后缀——T1 不提前做该机制。
    """
    text = prompt if isinstance(prompt, str) else json.dumps(prompt, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_case(fixtures_dir: Path, case_id: str) -> dict[str, Any]:
    path = fixtures_dir / f"{case_id}.json"
    if not path.exists():
        raise MockLLMMiss(f"no fixture file for case {case_id}: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise MockLLMMiss(f"fixture {path} must be a dict[prompt_key, response]")
    return data


class MockLLMAdapter:
    """Fixture 驱动的假 LLM，接口对齐 LLMAdapter（generate / generate_structured）。

    供 Contract E2E 在无真实 provider key 时使用：读 `{fixtures_dir}/{case_id}.json`
    （dict[prompt_key, response]）。未命中一律抛 MockLLMMiss。
    """

    def __init__(self, fixtures_dir: Path, case_id: str) -> None:
        self._case_id = case_id
        self._responses = _load_case(fixtures_dir, case_id)

    @classmethod
    def from_env(cls) -> "MockLLMAdapter":
        fixtures_dir = os.getenv("LLM_MOCK_DIR")
        case_id = os.getenv("LLM_MOCK_CASE")
        if not fixtures_dir or not case_id:
            raise MockLLMMiss(
                "LLM_PROVIDER=mock 需要 LLM_MOCK_DIR 与 LLM_MOCK_CASE env（plan D3）"
            )
        return cls(Path(fixtures_dir), case_id)

    def generate(self, prompt: str | list, **kwargs: Any) -> str:
        resp = self._lookup(prompt)
        if isinstance(resp, str):
            return resp
        # fixture 允许以 dict 表达 generate 的文本响应之外形态，序列化为字符串文本
        return json.dumps(resp, ensure_ascii=False)

    def generate_structured(
        self,
        prompt: str | list,
        schema: Any | None = None,
        **kwargs: Any,
    ) -> dict:
        resp = self._lookup(prompt)
        if not isinstance(resp, dict):
            raise StructuredParseError(
                f"case {self._case_id}: fixture for key {_prompt_key(prompt)} "
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
        key = _prompt_key(prompt)
        if key not in self._responses:
            raise MockLLMMiss(f"case {self._case_id}: no fixture for prompt key {key}")
        return self._responses[key]