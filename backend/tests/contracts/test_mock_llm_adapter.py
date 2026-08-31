from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.contracts

from app.llm.mock import MockLLMAdapter, MockLLMMiss, _prompt_key


def _write_fixture(tmp_path: Path, case: str, mapping: dict) -> None:
    """写一份 `{prompt_key: response}` 的 mock fixture 文件。"""
    (tmp_path / f"{case}.json").write_text(json.dumps(mapping), encoding="utf-8")


def test_mock_llm_adapter_loads_case(tmp_path):
    """fixture 命中 → generate 返回对应文案（不是真实 LLM）。"""
    prompt = "requirement: 2024 年各区域销售额排名"
    _write_fixture(tmp_path, "happy-path", {_prompt_key(prompt): "mock 需求卡片 JSON"})

    adapter = MockLLMAdapter(tmp_path, "happy-path")

    assert adapter.generate(prompt) == "mock 需求卡片 JSON"


def test_mock_llm_adapter_misses_raises(tmp_path):
    """fixture 未命中 → 抛 MockLLMMiss，mock 不允许静默兜底。"""
    _write_fixture(tmp_path, "empty", {})
    adapter = MockLLMAdapter(tmp_path, "empty")

    with pytest.raises(MockLLMMiss):
        adapter.generate("no such prompt")


def test_mock_llm_adapter_structured_output(tmp_path):
    """传入 pydantic model → generate_structured 自动 model_validate。"""
    from pydantic import BaseModel

    class _ReportSchema(BaseModel):
        status: str
        summary: str

    prompt = "report_v2 报告生成"
    _write_fixture(tmp_path, "report", {_prompt_key(prompt): {"status": "ok", "summary": "s"}})
    adapter = MockLLMAdapter(tmp_path, "report")

    out = adapter.generate_structured(prompt, schema=_ReportSchema)

    assert out == {"status": "ok", "summary": "s"}


def test_get_llm_adapter_env_switch(monkeypatch, tmp_path):
    """LLM_PROVIDER=mock → MockLLMAdapter；默认 → 真实 LLMAdapter。"""
    from app.llm import get_llm_adapter
    from app.llm.adapter import LLMAdapter

    # 默认（LLM_PROVIDER unset）→ 真实 LLMAdapter
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(get_llm_adapter(), LLMAdapter)

    # LLM_PROVIDER=mock 但 LLM_MOCK_* 未配 → 明确失败，不静默兜底
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("LLM_MOCK_DIR", raising=False)
    monkeypatch.delenv("LLM_MOCK_CASE", raising=False)
    with pytest.raises(MockLLMMiss):
        get_llm_adapter()

    # 配好 fixture env → MockLLMAdapter
    _write_fixture(tmp_path, "happy-path", {})
    monkeypatch.setenv("LLM_MOCK_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MOCK_CASE", "happy-path")
    assert isinstance(get_llm_adapter(), MockLLMAdapter)

    # 切回默认 → 缓存按 provider 重建，仍真实 LLMAdapter
    monkeypatch.delenv("LLM_PROVIDER")
    assert isinstance(get_llm_adapter(), LLMAdapter)