"""按前缀 / 用户批量硬删会话（plan 2026-11-11-session-hard-delete）。

用途：评测基线把每条用例都跑成一条真实会话（`eval-*`），跑 20 例就多 20 条，
和用户自己的对话混在同一个列表里。这就是清理它们的工具。

级联逻辑**不在本文件**——直接调 `app.services.session_deletion.delete_session`，
与 `DELETE /api/v1/sessions/{sid}` 端点共用同一份顺序，避免两份实现漂移。

用法：

    # 先看会删掉什么（默认就是 dry-run，真删必须显式 --yes）
    python -m app.jobs.purge_sessions --prefix eval-

    # 确认后真删
    python -m app.jobs.purge_sessions --prefix eval- --yes

    # 只清某个用户的所有会话
    python -m app.jobs.purge_sessions --user 1 --yes

    # 精确删一条
    python -m app.jobs.purge_sessions --session-id 3f2a... --yes

为什么默认 dry-run：这是**不可恢复**的物理删除，误敲一次就没了。
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.infra.db.postgres import close_pool, get_pool, init_pool
from app.services import session_deletion
from app.services.session_deletion import SessionNotFound


async def _preview(session_ids: list[str]) -> None:
    """dry-run：把将删的会话连同体量一起打出来，让人看清代价。"""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.thread_id,
                   COALESCE(NULLIF(s.title, ''), (
                       SELECT LEFT(c.content, 40) FROM app.conversations c
                       WHERE c.session_id = s.thread_id AND c.role = 'user'
                       ORDER BY c.id LIMIT 1
                   ), '') AS title,
                   (SELECT COUNT(*) FROM app.conversations c
                    WHERE c.session_id = s.thread_id) AS msgs,
                   (SELECT COUNT(*) FROM agent.report_version v
                    WHERE v.session_id = s.thread_id) AS versions
            FROM agent.session s
            WHERE s.thread_id = ANY($1::text[])
            ORDER BY s.updated_at DESC
            """,
            session_ids,
        )
    print(f"将删除 {len(rows)} 条会话（DRY RUN，未改动任何数据）：")
    for row in rows[:50]:
        print(
            f"  {row['thread_id'][:44]:<44} msgs={row['msgs']:<3} "
            f"versions={row['versions']:<2} {row['title']}"
        )
    if len(rows) > 50:
        print(f"  … 另有 {len(rows) - 50} 条")
    print("\n确认无误后加 --yes 真删。")


async def _owners(session_ids: list[str]) -> dict[str, int]:
    """CLI 不伪造 user_id：用库里真实的 owner 去调删除。"""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT thread_id, user_id FROM agent.session WHERE thread_id = ANY($1::text[])",
            session_ids,
        )
    return {row["thread_id"]: row["user_id"] for row in rows}


async def _purge(session_ids: list[str], owners: dict[str, int]) -> int:
    """逐条硬删；单条失败不影响其余（打印后继续），返回失败条数。"""
    failed = 0
    totals: dict[str, int] = {}
    for session_id in session_ids:
        owner = owners.get(session_id)
        if owner is None:
            print(f"  skip {session_id}: 不存在")
            failed += 1
            continue
        try:
            counts = await session_deletion.delete_session(session_id, user_id=owner)
        except SessionNotFound:
            print(f"  skip {session_id}: 不存在或不属于该用户")
            failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 - CLI 里失败继续，最后汇总
            print(f"  fail {session_id}: {exc}")
            failed += 1
            continue
        for key, value in counts.items():
            totals[key] = totals.get(key, 0) + value
        print(f"  deleted {session_id}")
    print("\n各表删除行数合计：")
    for key, value in totals.items():
        print(f"  {key:<34} {value}")
    return failed


async def _main(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.jobs.purge_sessions",
        description="批量硬删会话（默认 dry-run，真删需 --yes）",
    )
    parser.add_argument("--prefix", default="", help="thread_id 前缀，如 eval-")
    parser.add_argument("--user", type=int, default=None, help="只处理该 user_id 的会话")
    parser.add_argument("--session-id", default=None, help="只处理这一条")
    parser.add_argument("--yes", action="store_true", help="真的删（不加则是 dry-run）")
    args = parser.parse_args(argv)

    await init_pool()
    try:
        if args.session_id:
            session_ids = [args.session_id]
        else:
            session_ids = await session_deletion.list_session_ids(
                prefix=args.prefix, user_id=args.user,
            )
        if not session_ids:
            print("没有匹配的会话。")
            return 0
        if not args.yes:
            await _preview(session_ids)
            return 0
        failed = await _purge(session_ids, await _owners(session_ids))
        print(f"\n完成：成功 {len(session_ids) - failed} 条，失败 {failed} 条。")
        return 0 if failed == 0 else 1
    finally:
        await close_pool()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    sys.exit(main())
