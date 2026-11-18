"""evaluation 套件的运行环境（不是业务代码）。

为什么需要：
1. `evaluation/` 下的 live 测试不在 `testpaths`（`backend/tests`）里，跑它们时不会走到
   `backend/tests/conftest.py`，于是仓库根 `.env` 不会被加载 —— 真 LLM 用例会以
   `Illegal header value b'Bearer '` 这种「看起来像代码坏了」的方式失败（实测踩到）；
2. live 测试需要 `app.*` 可导入，所以把 `backend/` 放进 `sys.path`。

只做这两件事，不注入任何 fixture、不改业务行为。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
for _p in (str(_ROOT), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:  # python-dotenv 是 backend 依赖；缺失时静默跳过（CI 用 env 注入）
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except Exception:  # noqa: BLE001 - 环境准备失败不该让整个套件 import 失败
    pass
