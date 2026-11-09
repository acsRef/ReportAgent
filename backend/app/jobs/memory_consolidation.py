"""离线固化 CLI（生产 cron / 本地手动门的唯一入口）。

长期记忆的判定不在请求里做（plan `2026-11-09-memory-admission-and-consolidation`）：
热路径只落证据与「一眼可判」的写，其余交给这个每日批处理。

用法（repo root；`DATABASE_URL` 缺省 localhost ragent/ragent）：

    # 先看会发生什么（不写库）
    python -m app.jobs.memory_consolidation --user-id 1 --dry-run

    # 真跑
    python -m app.jobs.memory_consolidation --user-id 1

    # 跑所有有候选/临时期限的用户（生产 cron 用）
    python -m app.jobs.memory_consolidation --all-users

    # 小步走：每用户最多处理 50 条
    python -m app.jobs.memory_consolidation --all-users --max-items 50

退出码：0 正常（即使个别用户报错，`errors` 会计入 run 表）；2 参数/环境错误。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))


def _load_root_env() -> None:
    """手动跑时补载 repo 根 .env（不覆盖已设环境变量）。"""
    dotenv_path = _ROOT / ".env"
    if not dotenv_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path, override=False)
    except ImportError:  # pragma: no cover - dotenv 随 requirements 安装
        pass


async def _users_with_work() -> list[int]:
    """有活干的用户：候选队列非空，或存在已到期的临时期限条目。"""
    from app.infra.db.postgres import get_pool, init_pool

    await init_pool()
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT user_id FROM memory.semantic_entry
               WHERE (status='candidate' AND promoted_at IS NULL)
                  OR (status='active' AND expires_at IS NOT NULL AND expires_at <= NOW())"""
        )
    return [r["user_id"] for r in rows]


async def _run(user_ids: list[int], *, dry_run: bool, max_items: int) -> list[dict]:
    from app.infra.db.postgres import close_pool, init_pool
    from app.memory.consolidation import consolidate

    await init_pool()
    try:
        out: list[dict] = []
        for uid in user_ids:
            summary = await consolidate(uid, dry_run=dry_run, max_items=max_items)
            out.append(summary)
            print(
                f"  user={uid} considered={summary['considered']} "
                f"promoted={summary['promoted']} expired={summary['expired']} "
                f"superseded={summary['superseded']} skipped={summary['skipped']} "
                f"errors={summary['errors']}"
                + ("  [dry-run]" if dry_run else "")
            )
            for action in summary["actions"][:10]:
                print(f"    · {action}")
            if len(summary["actions"]) > 10:
                print(f"    · … 另有 {len(summary['actions']) - 10} 条动作")
        return out
    finally:
        await close_pool()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="记忆离线固化（准入 + 晋级 + 过期 + 冲突收敛）")
    ap.add_argument("--user-id", type=int, default=None, help="只处理这个用户")
    ap.add_argument("--all-users", action="store_true", help="处理所有有活干的用户")
    ap.add_argument("--dry-run", action="store_true", help="只算不写（晋级/过期/收敛都不落库）")
    ap.add_argument("--max-items", type=int, default=200, help="每用户最多处理多少条候选")
    args = ap.parse_args(argv)

    if args.user_id is None and not args.all_users:
        ap.error("需要 --user-id N 或 --all-users")
    _load_root_env()

    async def _main() -> list[dict]:
        if args.user_id is not None:
            return await _run([args.user_id], dry_run=args.dry_run, max_items=args.max_items)
        users = await _users_with_work()
        if not users:
            print("  没有需要固化的用户")
            return []
        return await _run(users, dry_run=args.dry_run, max_items=args.max_items)

    summaries = asyncio.run(_main())
    total = {
        "users": len(summaries),
        "promoted": sum(s["promoted"] for s in summaries),
        "expired": sum(s["expired"] for s in summaries),
        "superseded": sum(s["superseded"] for s in summaries),
    }
    print(
        f"完成：users={total['users']} promoted={total['promoted']} "
        f"expired={total['expired']} superseded={total['superseded']}"
        + ("  [dry-run，未写库]" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
