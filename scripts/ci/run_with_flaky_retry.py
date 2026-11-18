"""CI 用：跑测试 → 失败时**只在定时任务上**重跑失败用例一次 → 记录 flaky 统计。

为什么需要（plan 2026-11-18-testing-playbook §二·flaky 治理，P1）：

- Agent 项目里有一类失败是**真 flaky**（时序、外部服务、模型采样），重跑一次就过；
- 另一类失败是**真缺陷**，重跑也过不去。
- 两种混在一起时，人看到红的反应变成"再点一次重跑"，最后所有红都被无视
  （本仓真实教训：夜间 e2e 连红 7 天没人发现）。

所以纪律是：

1. **push 门禁永不重跑**（重跑会掩盖真回归）；
2. 只允许**定时任务**重跑一次失败用例；
3. 重跑通过 → job 绿但必须留痕（`reports/flaky-<name>.json` + Job Summary + warning），
   同一用例反复 flaky 必须被修或被隔离；
4. 重跑仍失败 → job 红（这才是真缺陷），交给 notify-failure 开 Issue。

用法（在 workflow 里）：

    python scripts/ci/run_with_flaky_retry.py --name prompt-behavior -- \
        -m pytest evaluation/tests/test_prompt_behavior_live.py -q -s

也支持本地强制验证重跑逻辑：

    python scripts/ci/run_with_flaky_retry.py --name demo --allow-rerun -- -m pytest backend/tests/smoke

退出码：首跑通过 0；重跑通过 0（flaky）；两次都失败 1（真失败）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_FAILED_LINE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)


def _run(args: list[str], log_path: Path) -> tuple[int, str]:
    """跑一条命令，输出同时落盘与转发；返回 (exit code, 输出全文)。"""
    proc = subprocess.run(  # noqa: S603 - 参数由本仓 workflow 固定提供
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    log_path.write_text(output, encoding="utf-8")
    sys.stdout.write(output)
    sys.stdout.flush()
    return proc.returncode, output


def _failed_tests(output: str) -> list[str]:
    return sorted(set(_FAILED_LINE.findall(output)))


def _ready_for_rerun(allow_rerun: bool) -> bool:
    """只有定时任务（或显式 --allow-rerun）才允许重跑。"""
    if allow_rerun:
        return True
    return os.getenv("GITHUB_EVENT_NAME", "") == "schedule"


def _append_summary(text: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError:
        pass


def _emit_error_annotations(name: str, output: str) -> None:
    """把失败日志尾部转成 ::error 注解。

    为什么：GitHub Actions 的 job log 需要登录才能下载，而 **annotations 匿名可读**；
    没有注解时 CI 红了只能靠猜（2026-11-18 排 ragent 的两次红就是这么吃的亏）。
    """
    for line in output.strip().splitlines()[-60:]:
        print(f"::error file={name}::{line}")


def main() -> int:
    parser = argparse.ArgumentParser(description="跑测试并在定时任务上重跑一次失败用例")
    parser.add_argument("--name", required=True, help="用例组名字（写进报告与 artifact 名）")
    parser.add_argument("--report-dir", default="reports", help="报告输出目录")
    parser.add_argument("--allow-rerun", action="store_true", help="本地验证用：强制允许重跑")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="`--` 之后是完整命令")
    ns = parser.parse_args()

    command = [arg for arg in ns.command if arg != "--"]
    if not command:
        parser.error("`--` 之后需要给出命令")
    # 便利：给的是 pytest 参数（以 `-` 开头或直接给测试文件）时自动补 `python -m pytest`
    if command[0].startswith("-") or command[0].endswith(".py"):
        command = [sys.executable, "-m", "pytest", *command]

    report_dir = Path(ns.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    log_dir = report_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    first_rc, first_out = _run(command, log_dir / f"{ns.name}-first.log")
    first_failed = _failed_tests(first_out)

    record: dict[str, object] = {
        "name": ns.name,
        "event": os.getenv("GITHUB_EVENT_NAME", "local"),
        "run_id": os.getenv("GITHUB_RUN_ID", ""),
        "sha": os.getenv("GITHUB_SHA", ""),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "first_rc": first_rc,
        "first_failed_tests": first_failed,
        "rerun_attempted": False,
        "rerun_rc": None,
        "rerun_failed_tests": [],
        "flaky": False,
        "duration_seconds": 0,
    }

    if first_rc == 0:
        record["duration_seconds"] = round(time.time() - started, 1)
        (report_dir / f"flaky-{ns.name}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[{ns.name}] 首跑通过（{record['duration_seconds']}s）")
        return 0

    if not _ready_for_rerun(ns.allow_rerun):
        print(
            f"\n[{ns.name}] 首跑失败且当前不是定时任务 → **不重跑**（push 门禁禁止重跑，避免掩盖回归）"
        )
        record["duration_seconds"] = round(time.time() - started, 1)
        (report_dir / f"flaky-{ns.name}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _emit_error_annotations(ns.name, first_out)
        return first_rc

    print(f"\n[{ns.name}] 首跑失败（{len(first_failed)} 条），定时任务按纪律重跑失败用例一次…")
    rerun_command = command + ["--last-failed"]
    rerun_rc, rerun_out = _run(rerun_command, log_dir / f"{ns.name}-rerun.log")
    rerun_failed = _failed_tests(rerun_out)

    record.update(
        {
            "rerun_attempted": True,
            "rerun_rc": rerun_rc,
            "rerun_failed_tests": rerun_failed,
            "flaky": rerun_rc == 0,
            "duration_seconds": round(time.time() - started, 1),
        }
    )
    (report_dir / f"flaky-{ns.name}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if rerun_rc == 0:
        msg = f"[{ns.name}] **flaky**：首跑 {len(first_failed)} 条失败，重跑全部通过 —— 不算绿，要修或隔离"
        print("\n" + msg)
        print("  首跑失败用例：" + (", ".join(first_failed) or "(未解析到)"))
        print("::warning title=flaky tests::" + msg)
        _append_summary(
            "### ⚠️ flaky：" + ns.name + "\n"
            "- 首跑失败：" + (", ".join(first_failed) or "(未解析到)") + "\n"
            "- 重跑：全部通过（按纪律 job 保持绿色，但**必须**进隔离名单或修掉）\n"
        )
        return 0

    print(f"\n[{ns.name}] 重跑仍失败 → 判定为真失败（job 红）")
    print("  仍失败用例：" + (", ".join(rerun_failed) or "(未解析到)"))
    _emit_error_annotations(ns.name, rerun_out)
    _append_summary(
        "### ❌ 真失败：" + ns.name + "\n"
        "- 首跑失败：" + (", ".join(first_failed) or "?") + "\n"
        "- 重跑仍失败：" + (", ".join(rerun_failed) or "?") + "\n"
    )
    return rerun_rc or 1


if __name__ == "__main__":
    sys.exit(main())
