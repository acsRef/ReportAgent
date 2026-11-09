"""记忆准入 + 离线固化的**手动验证脚本**（plan memory-admission-and-consolidation）。

为什么要这个脚本：固化是「一天跑一次」的批处理，正常使用中很难凑齐跨会话证据来观察效果，
单元测试又只能看到断言。这个脚本把一段**多天多会话的真实对话序列**灌进一个临时用户，
然后跑一遍 dry-run 和真跑，最后打印「原话 → 准入判定 → 落库 → 证据 → 固化后」的完整对照表。

用法（repo root）：

    # 默认：跑完即删临时用户
    D:/miniConda/envs/agent/python.exe evaluation/memory_consolidation_scenario.py

    # 保留数据（想看库里的行、或去前端点）
    ... --keep

    # 只演不写（连热路径写入都跳过，只看准入判定）
    ... --plan-only

env：DATABASE_URL（缺省 localhost ragent/ragent）+ SILICONFLOW_API_KEY（写库要 embedding；
缺失时用确定性假向量，不影响判定逻辑）。

退出码：0 正常；2 环境缺 DATABASE_URL。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_root_env() -> None:
    dotenv_path = _ROOT / ".env"
    if not dotenv_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path, override=False)
    except ImportError:  # pragma: no cover
        pass


_load_root_env()

# 一段「三天、三个会话」的对话序列。每一行都对应一个真实产品场景：
# (会话 id, 原话, 走哪条入口)
SCENARIO: list[tuple[str, str, str]] = [
    ("sess-day1-a", "以后都用柱状图显示", "explicit"),          # 长期标记 → 当轮 active(user)
    ("sess-day1-a", "看下 2024 年的销售额", "explicit"),        # 一次性任务参数 → 闸 1 拒绝
    ("sess-day1-b", "这次先按周看", "explicit"),                # 会话标记 → active(session, 24h)
    ("sess-day2-a", "净销售额是扣退款后的口径", "inferred"),     # 推断类，第 1 次证据
    ("sess-day2-a", "柱状图挺好的", "inferred"),                # 推断类，只出现 1 次
    ("sess-day3-a", "净销售额是扣退款后的口径", "inferred"),     # 第 2 次证据
    ("sess-day3-b", "净销售额是扣退款后的口径", "inferred"),     # 第 3 次证据 → 够格升级
    ("sess-day3-b", "为什么华东掉得这么快", "explicit"),        # 提问句 → 闸 1 拒绝
]


class _FakeEmbedder:
    """没有 embedding key 时的确定性替身（不影响准入/固化判定）。"""

    async def embed_or_none(self, text: str):
        dim = int(os.getenv("EMBEDDING_DIM", "1536"))
        v = [0.01] * dim
        v[0] = 0.5
        return v


def _make_user() -> int:
    import psycopg2

    conn = psycopg2.connect(
        os.getenv("DATABASE_URL", "postgresql://ragent:ragent@localhost:5432/ragent")
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app.users (username, password_hash) VALUES (%s, 'x') RETURNING id",
        (f"memdemo-{uuid.uuid4().hex[:8]}",),
    )
    uid = cur.fetchone()[0]
    conn.close()
    return uid


def _rows(uid: int) -> list[tuple]:
    import psycopg2

    conn = psycopg2.connect(
        os.getenv("DATABASE_URL", "postgresql://ragent:ragent@localhost:5432/ragent")
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT content, memory_type, status, scope, "
        "COALESCE(evidence_count,1), (expires_at IS NOT NULL), confidence "
        "FROM memory.semantic_entry WHERE user_id=%s ORDER BY id",
        (uid,),
    )
    out = cur.fetchall()
    conn.close()
    return out


def _cleanup(uid: int) -> None:
    import psycopg2

    conn = psycopg2.connect(
        os.getenv("DATABASE_URL", "postgresql://ragent:ragent@localhost:5432/ragent")
    )
    conn.autocommit = True
    cur = conn.cursor()
    for table in ("memory.semantic_entry", "memory.consolidation_cursor", "memory.consolidation_run"):
        cur.execute(f"DELETE FROM {table} WHERE user_id=%s", (uid,))
    cur.execute("DELETE FROM app.users WHERE id=%s", (uid,))
    conn.close()


async def _seed(uid: int) -> list[dict]:
    """按入口把 SCENARIO 灌进库，返回每个原话的「准入判定」。"""
    from app.memory.admission import classify_scope, is_admissible
    from app.memory.manager import remember_explicit_preference, remember_inferred_facts

    verdicts: list[dict] = []
    for session_id, text, route in SCENARIO:
        ok, why = is_admissible(text)
        adm = classify_scope(text)
        entry = {"session": session_id, "text": text, "route": route,
                 "admitted": ok, "reason": why,
                 "scope": adm.scope, "status": adm.status, "ttl": adm.ttl_hours}
        if route == "inferred":
            await remember_inferred_facts(uid, [text], session_id=session_id)
        elif ok:
            await remember_explicit_preference(uid, text)
        verdicts.append(entry)
    return verdicts


def _print_verdicts(verdicts: list[dict]) -> None:
    print("\n== 一句话的准入判定 ==")
    print(f"  {'原话':<24} {'入口':<9} {'准入':<6} {'理由':<16} {'落库形态'}")
    for v in verdicts:
        admitted = "记" if v["admitted"] else "不记"
        form = (
            f"{v['status']}/{v['scope']}"
            + (f"/{v['ttl']}h" if v["ttl"] else "")
            if v["admitted"] or v["route"] == "inferred"
            else "—"
        )
        print(f"  {v['text']:<24} {v['route']:<9} {admitted:<6} {v['reason']:<16} {form}")


def _print_rows(rows: list[tuple], title: str) -> None:
    print(f"\n== {title} ==")
    print(f"  {'content':<26} {'type':<22} {'status':<11} {'scope':<8} {'证据':<4} {'TTL':<4} {'conf'}")
    for content, mtype, status, scope, evidence, has_ttl, conf in rows:
        print(
            f"  {content[:24]:<26} {mtype:<22} {status:<11} {scope:<8} "
            f"{evidence:<4} {'yes' if has_ttl else 'no':<4} {conf}"
        )


async def _run(*, plan_only: bool, keep: bool, min_evidence: int) -> int:
    if not os.getenv("SILICONFLOW_API_KEY"):
        # 没有 key 时用假向量：准入/固化判定与向量无关，只是语义召回不可用
        from app.infra.memory import query_memory as qm_mod
        from app.infra.memory import user_memory as um_mod

        um_mod.get_embedder = lambda: _FakeEmbedder()  # type: ignore[assignment]
        qm_mod.get_embedder = lambda: _FakeEmbedder()  # type: ignore[assignment]
        print("[warn] SILICONFLOW_API_KEY 未设：使用确定性假向量（不影响准入/固化判定）")

    uid = _make_user()
    print(f"临时用户 user_id={uid}" + ("（保留）" if keep else "（跑完即删）"))
    from app.infra.db.postgres import close_pool, init_pool

    await init_pool()
    try:
        from app.memory.admission import classify_scope, is_admissible

        if plan_only:
            verdicts = []
            for session_id, text, route in SCENARIO:
                ok, why = is_admissible(text)
                adm = classify_scope(text)
                verdicts.append({"session": session_id, "text": text, "route": route,
                                 "admitted": ok, "reason": why, "scope": adm.scope,
                                 "status": adm.status, "ttl": adm.ttl_hours})
        else:
            verdicts = await _seed(uid)
        _print_verdicts(verdicts)
        if plan_only:
            return 0

        _print_rows(_rows(uid), "热路径写完（注意 session 级那条有 TTL）")

        from app.memory.consolidation import consolidate

        dry = await consolidate(uid, dry_run=True, min_evidence=min_evidence)
        print(
            f"\n== dry-run：considered={dry['considered']} promoted={dry['promoted']} "
            f"skipped={dry['skipped']} superseded={dry['superseded']} =="
        )
        for action in dry["actions"]:
            print(f"   · {action}")

        real = await consolidate(uid, min_evidence=min_evidence)
        print(
            f"\n== 真跑：promoted={real['promoted']} expired={real['expired']} "
            f"superseded={real['superseded']} skipped={real['skipped']} errors={real['errors']} =="
        )
        _print_rows(_rows(uid), "固化后（证据够的已晋级，无证据的仍在队列）")
        print(f"\n[min_evidence={min_evidence}] 证据阈值可用 --min-evidence 调整")
        return 0
    finally:
        await close_pool()
        if not keep and not plan_only:
            _cleanup(uid)
            print(f"[cleanup] 已删除临时用户 {uid}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="记忆准入 + 固化 的手动验证脚本")
    ap.add_argument("--plan-only", action="store_true", help="只打印准入判定，不写库")
    ap.add_argument("--keep", action="store_true", help="保留临时用户与数据（便于去前端看）")
    ap.add_argument("--min-evidence", type=int, default=3, help="证据阈值（默认 3）")
    args = ap.parse_args(argv)
    if not os.getenv("DATABASE_URL") and not (_ROOT / ".env").exists():
        print("[error] 需要 DATABASE_URL（或 repo 根 .env）")
        return 2
    return asyncio.run(_run(plan_only=args.plan_only, keep=args.keep,
                            min_evidence=args.min_evidence))


if __name__ == "__main__":
    sys.exit(main())
