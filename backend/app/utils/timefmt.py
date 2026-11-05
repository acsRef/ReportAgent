"""时间戳序列化：数据库里的 `timestamp without time zone` 一律按 **UTC** 输出。

2026-11-05（用户：「左侧的时间也是错的，怎么可能就是八小时了」）——

`agent.session.updated_at` / `app.conversations.created_at` 这类列由 `NOW()` 写入，
而 PostgreSQL 服务端时区是 `Etc/UTC`，所以库里存的是 **UTC 的 naive 值**。
之前 API 直接 `dt.isoformat()`（`2026-11-05T09:46:17`，不带时区），
浏览器 `Date.parse()` 把这种无时区字符串**当本地时间**解释 → 东八区整整差 8 小时，
于是刚生成的会话显示「8 小时前」。

修法：读出来时补上 `UTC` 时区再序列化（`+00:00`），前端解析后与本地时间比对就正确了。
写入侧另有一处坑：`SessionManager.create_session` 之前传的是 `datetime.now()`（本地 naive），
已改为数据库 `NOW()`，与 `updated_at` 同一时钟。
"""
from __future__ import annotations

from datetime import datetime, timezone


def utc_iso(value: datetime | None) -> str | None:
    """naive（库里存 UTC）或 aware datetime → 带时区的 ISO 8601 字符串。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()
