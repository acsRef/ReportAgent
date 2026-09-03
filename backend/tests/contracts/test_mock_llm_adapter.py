from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.contracts

from app.llm.mock import MockLLMAdapter, MockLLMMiss, prompt_kind

# 语义 kind marker 取自各 prompt 的 system_contract 首句（app/agent/prompts/），
# 与 mock.py KIND_MARKERS 保持同步。测试用「含 marker 的最小 prompt」触发分类。
_MARKERS = {
    "intent_classify": "你是 ReportAgent 的意图分类器。",
    "requirement_parse": "你是 ReportAgent 需求解析器。",
    "sql_plan": "你是 ReportAgent SQL 规划器。",
    "sql_generate": "你是 ReportAgent SQL 生成专家。",
    "report_plan": "你是 ReportAgent 报告规划师。",
}

# 各 kind 的 fixture key 前缀（kind:seq；seq 从 1 起，按调用顺序递增）
_KEYS = {kind: f"{kind}:1" for kind in _MARKERS}


def _write_fixture(tmp_path: Path, case: str, mapping: dict) -> None:
    """写一份 `{kind:seq: response}` 的 mock fixture 文件。"""
    (tmp_path / f"{case}.json").write_text(json.dumps(mapping), encoding="utf-8")


def test_prompt_kind_maps_marker_to_kind():
    """prompt 按固定 marker 分类，不受前置动态内容影响。"""
    assert prompt_kind(_MARKERS["requirement_parse"] + " 动态 user_query/schema") == "requirement_parse"
    assert prompt_kind(_MARKERS["sql_generate"]) == "sql_generate"
    # list 形态（chat messages）也归类
    assert prompt_kind([{"role": "user", "content": _MARKERS["sql_generate"]}]) == "sql_generate"


def test_prompt_kind_unknown_marker_raises():
    """无法归类的 prompt 明确失败，不静默。"""
    with pytest.raises(MockLLMMiss):
        prompt_kind("随意文本，不属任何已知 prompt")


def test_mock_llm_adapter_loads_case(tmp_path):
    """fixture 命中 → generate 返回对应文案（不是真实 LLM）。"""
    prompt = _MARKERS["requirement_parse"] + " 2024 年各区域销售额排名"
    _write_fixture(tmp_path, "happy-path", {_KEYS["requirement_parse"]: {"summary": "mock 需求卡"}})

    adapter = MockLLMAdapter(tmp_path, "happy-path")

    assert adapter.generate(prompt) == {"summary": "mock 需求卡"}


def test_mock_llm_adapter_misses_raises(tmp_path):
    """fixture 未命中 → 抛 MockLLMMiss，mock 不允许静默兜底。"""
    _write_fixture(tmp_path, "empty", {})
    adapter = MockLLMAdapter(tmp_path, "empty")

    with pytest.raises(MockLLMMiss):
        adapter.generate(_MARKERS["requirement_parse"] + " x")


def test_mock_llm_adapter_seq_increments_per_kind(tmp_path):
    """同一 kind 逐次调用 seq 递增：repair 的 sql_generate:1 → :2 用不同响应。"""
    prompt = _MARKERS["sql_generate"] + " bad"
    _write_fixture(
        tmp_path,
        "repair",
        {
            "sql_generate:1": "SELECT bad_sql",   # 第 1 次坏
            "sql_generate:2": "SELECT 1 AS ok",   # repair 后第 2 次好
        },
    )
    adapter = MockLLMAdapter(tmp_path, "repair")

    assert adapter.generate(prompt) == "SELECT bad_sql"
    assert adapter.generate(prompt) == "SELECT 1 AS ok"
    # 第 3 次越界 → 明确失败
    with pytest.raises(MockLLMMiss):
        adapter.generate(prompt)


def test_mock_llm_adapter_structured_output(tmp_path):
    """传入 pydantic model → generate_structured 自动 model_validate。"""
    from pydantic import BaseModel

    class _ReportSchema(BaseModel):
        status: str
        summary: str

    prompt = _MARKERS["report_plan"]
    _write_fixture(tmp_path, "report", {_KEYS["report_plan"]: {"status": "ok", "summary": "s"}})
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


# —— Fix 1：get_chat_llm fail-closed（mock 模式禁用 ChatOpenAI 直构造） ——


def test_get_chat_llm_in_mock_mode_raises_not_implemented(monkeypatch):
    """LLM_PROVIDER=mock → get_chat_llm 必须 fail-closed 抛 NotImplementedError。

    防 Contract E2E 出现「主路径 mock + 旁路真 LLM」半 mock：任意 import get_chat_llm
    都会绕开 get_llm_adapter switch，让真 LLM 泄漏进 mock 测试。
    """
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MOCK_DIR", "/tmp")
    monkeypatch.setenv("LLM_MOCK_CASE", "x")

    from app.llm import get_chat_llm

    with pytest.raises(NotImplementedError, match="LLM_PROVIDER=mock 禁用"):
        get_chat_llm()


def test_get_chat_llm_in_real_mode_still_constructs(monkeypatch):
    """LLM_PROVIDER 非 mock → get_chat_llm 仍返回 ChatOpenAI 实例（real path 不被破坏）。"""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    # CI 无 LLM key 修复：ChatOpenAI 构造要求 api_key 存在（init 不联网、不校验
    # 格式）——本地 .env 有真 key 掩盖，全新 runner 需显式假 key 仅验证构造路径。
    monkeypatch.setenv("LLM_API_KEY", "sk-ci-fake")

    from langchain_openai import ChatOpenAI

    from app.llm import get_chat_llm

    chat = get_chat_llm()
    assert isinstance(chat, ChatOpenAI)


# —— Fix 4：fixture key 格式校验（防手写 typo 静默 miss） ——


def test_load_case_rejects_malformed_key(tmp_path):
    """fixture key 必须为 kind:N（kind 小写蛇形，seq ≥ 1），否则 MockLLMMiss 早暴露。"""
    from app.llm.mock import _load_case

    # 缺冒号 → 拒
    (tmp_path / "bad.json").write_text(json.dumps({"sqlplan1": {}}), encoding="utf-8")
    with pytest.raises(MockLLMMiss, match="key 'sqlplan1' 不符合"):
        _load_case(tmp_path, "bad")

    # 大写 → 拒
    (tmp_path / "upper.json").write_text(json.dumps({"SQL_Plan:1": {}}), encoding="utf-8")
    with pytest.raises(MockLLMMiss, match="key 'SQL_Plan:1' 不符合"):
        _load_case(tmp_path, "upper")

    # seq=0 → 拒（seq 必须 ≥ 1）
    (tmp_path / "zero.json").write_text(json.dumps({"sql_plan:0": {}}), encoding="utf-8")
    with pytest.raises(MockLLMMiss, match="key 'sql_plan:0' 不符合"):
        _load_case(tmp_path, "zero")

    # 合法 key 通过
    (tmp_path / "ok.json").write_text(json.dumps({"sql_plan:1": {}, "requirement_parse:12": {}}), encoding="utf-8")
    loaded = _load_case(tmp_path, "ok")
    assert set(loaded.keys()) == {"sql_plan:1", "requirement_parse:12"}