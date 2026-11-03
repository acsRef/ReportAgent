"""真实问法矩阵的「卫生检查」（离线，毫秒级）。

用户要求：测试用例要用**用户真实问法**，不能拿 prompt 里的示例句自证，
也不能靠堆数量。这个文件钉三件事：

1. 用例数量克制（14 条，7 类各 2 条）；
2. 问法不能与 prompt few-shot 的 user 字符串相同（否则测的是 prompt 自己）；
3. 问法之间不重复。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.knowledge import registry as knowledge

pytestmark = pytest.mark.contracts

DATASET = Path(__file__).resolve().parents[3] / "evaluation" / "real_user_questions.yaml"
HINTS = Path(__file__).resolve().parents[2] / "app" / "knowledge" / "schema_hints.yaml"


def _cases() -> list[dict]:
    return yaml.safe_load(DATASET.read_text(encoding="utf-8"))["cases"]


def test_case_count_is_curated_not_padded():
    cases = _cases()
    assert len(cases) == 14, "用户要求 14 条封顶：7 类 × 2 条，不靠数量堆"
    categories = [c["category"] for c in cases]
    for category in set(categories):
        assert categories.count(category) == 2, f"{category} 类应为 2 条"


def test_queries_are_unique():
    queries = [c["query"] for c in _cases()]
    assert len(queries) == len(set(queries)), "存在重复问法"


def test_queries_do_not_come_from_prompt_few_shot():
    """不能拿 prompt 的 few-shot 原句当测试输入。"""
    raw = yaml.safe_load(HINTS.read_text(encoding="utf-8"))
    few_shot = [str(item.get("user") or "") for item in raw.get("plan_few_shot") or []]
    few_shot = [q for q in few_shot if q]
    assert few_shot, "few-shot 为空，检查 schema_hints.yaml"
    for case in _cases():
        for example in few_shot:
            assert case["query"].strip() != example.strip(), (
                f"用例 {case['id']} 直接用了 prompt 示例句：{example!r}"
            )


def test_queries_are_colloquial_not_prompt_like():
    """至少一半用例是口语/省略式（短、含口语词），防止又写成书面语清单。"""
    oral_markers = ("咋样", "咋", "看下", "看看", "给我", "哪个", "卖得", "掉得", "列出来", "最近", "这周")
    colloquial = [c for c in _cases() if any(m in c["query"] for m in oral_markers)]
    assert len(colloquial) >= 7, f"口语化用例太少：{len(colloquial)}/14"


def test_must_clarify_cases_exist_for_vague_queries():
    vague = [c for c in _cases() if c["category"] == "模糊"]
    assert vague and all(c.get("must_clarify") for c in vague), (
        "模糊类用例必须断言「先澄清」，否则等于允许系统瞎猜"
    )
