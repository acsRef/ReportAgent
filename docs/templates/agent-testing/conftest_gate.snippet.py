"""放进 `tests/conftest.py`（或按行拆到子目录 conftest）。

两件事：
1. **离线套件禁网络/真 LLM**：把 DB / LLM / 检索的凭据换成"必然连不上"的哨兵值，
   任何误触真实服务的用例会立刻失败，而不是悄悄跑真请求（既慢又花钱、还不可复现）。
2. **真链路 env-gate**：`E2E=1` 才跑 `e2e` marker；缺 key 时 skip 而不是 fail。
"""
from __future__ import annotations

import os

import pytest

# --- 1. 哨兵：离线套件永远连不到真服务 -------------------------------------
# 注意：只在本进程里设置，不影响外部服务；值本身要"语法合法但连不上"，
# 这样误触时报的是连接失败（容易看懂），而不是参数校验错误（容易误判成代码 bug）。
os.environ.setdefault("DATABASE_URL", "postgresql://offline:offline@127.0.0.1:1/offline_test")
os.environ.setdefault("LLM_API_KEY", "offline-no-key")
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:1/v1")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_INSECURE_DEFAULT_AUTH", "1")

# --- 2. 真链路 gate ----------------------------------------------------------
RUN_E2E = os.getenv("E2E") == "1"
RUN_DB = bool(os.getenv("DATABASE_URL_REAL") or os.getenv("DATABASE_URL"))

pytestmark_skip_e2e = pytest.mark.skipif(not RUN_E2E, reason="E2E != 1; skipping live end-to-end test")
pytestmark_skip_db = pytest.mark.skipif(not RUN_DB, reason="no DATABASE_URL; skipping persistence test")


def pytest_collection_modifyitems(config, items):
    """按 marker 自动打 skip，避免每个文件重复写 skipif。"""
    for item in items:
        if "e2e" in item.keywords and not RUN_E2E:
            item.add_marker(pytestmark_skip_e2e)
        if "persistence" in item.keywords and not RUN_DB:
            item.add_marker(pytestmark_skip_db)


# --- 3. 可选：断言贡献者确实在 repo root 跑（可复现性纪律） --------------------
# def pytest_configure(config):
#     root = config.rootpath
#     assert (root / "pytest.ini").exists(), f"请从 repo root 跑 pytest（当前 rootdir={root}）"
