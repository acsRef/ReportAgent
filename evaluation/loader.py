"""baseline_cases.json 加载器。"""
from __future__ import annotations

import json
from pathlib import Path

from evaluation.schema import BaselineCase


def load_all(path: str | Path) -> list[BaselineCase]:
    """加载整个数据集；重复 id 视为数据缺陷直接抛错。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [BaselineCase(**item) for item in raw]
    ids = [c.id for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate case id(s): {sorted(dupes)}")
    return cases
