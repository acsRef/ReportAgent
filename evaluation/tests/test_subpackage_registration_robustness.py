"""DIM_REGISTRY 初始化健壮性测试。

P14 P2 闭环：不应依赖任何 test file 提前 import 9 子包。
任何入口（`python -c`、`python -m evaluation.runner`、CLI、pytest 任意子集），
仅 `import evaluation` 或 `from evaluation.checker import DIM_REGISTRY` 都应
让 DIM_REGISTRY 含完整 9 dim entries。

测试方法：subprocess 跑独立 Python 进程，避免当前 pytest session 的 import 副作用污染。
"""
from __future__ import annotations

import subprocess
import sys


def _run_clean_subprocess(snippet: str) -> subprocess.CompletedProcess:
    """跑独立 Python 进程，cwd=repo root，让 evaluation 包可 import。"""
    return subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        cwd=".",
    )


def test_dim_registry_full_after_only_checker_import():
    """仅 `from evaluation.checker import DIM_REGISTRY`（不 import 子包）→ 9 entries。"""
    snippet = (
        "from evaluation.checker import DIM_REGISTRY\n"
        "keys = sorted(DIM_REGISTRY.keys())\n"
        "expected = ['e2e', 'frontend', 'memory', 'repair', 'report', "
        "'requirement', 'retrieval', 'sql', 'tool_selection']\n"
        "assert keys == expected, 'got ' + str(keys)\n"
        "print('OK')\n"
    )
    result = _run_clean_subprocess(snippet)
    assert result.returncode == 0, (
        f"P14 P2 失守：subprocess 失败，stderr={result.stderr!r}, stdout={result.stdout!r}"
    )
    assert "OK" in result.stdout


def test_dim_registry_full_after_only_evaluation_package_import():
    """仅 `import evaluation`（不显式 import 子包）→ 9 entries。"""
    snippet = (
        "import evaluation\n"
        "from evaluation.checker import DIM_REGISTRY\n"
        "keys = sorted(DIM_REGISTRY.keys())\n"
        "expected = ['e2e', 'frontend', 'memory', 'repair', 'report', "
        "'requirement', 'retrieval', 'sql', 'tool_selection']\n"
        "assert keys == expected, 'got ' + str(keys)\n"
        "print('OK')\n"
    )
    result = _run_clean_subprocess(snippet)
    assert result.returncode == 0, (
        f"P14 P2 失守：subprocess 失败，stderr={result.stderr!r}"
    )
    assert "OK" in result.stdout


def test_dim_registry_full_after_only_loader_import():
    """仅 `from evaluation.loader import load_all` → 9 entries。"""
    snippet = (
        "from evaluation.loader import load_all\n"
        "from evaluation.checker import DIM_REGISTRY\n"
        "keys = sorted(DIM_REGISTRY.keys())\n"
        "expected = ['e2e', 'frontend', 'memory', 'repair', 'report', "
        "'requirement', 'retrieval', 'sql', 'tool_selection']\n"
        "assert keys == expected, 'got ' + str(keys)\n"
        "print('OK')\n"
    )
    result = _run_clean_subprocess(snippet)
    assert result.returncode == 0, (
        f"P14 P2 失守：loader import 链应触发 9 子包注册，stderr={result.stderr!r}"
    )
    assert "OK" in result.stdout
