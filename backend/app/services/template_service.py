"""Service layer for `app.report_template` CRUD.

All functions scope by `user_id` from the JWT.
"""
from __future__ import annotations

import json
from typing import Any

from app.infra.db import template_repository
from app.infra.db.postgres import get_pool


async def create_template(
    *,
    user_id: int,
    name: str,
    description: str,
    requirement_payload: dict,
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await template_repository.create(
                conn,
                user_id=user_id,
                name=name,
                description=description,
                requirement_payload=requirement_payload,
            )
    return row


async def list_templates(*, user_id: int) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await template_repository.list_for_user(conn, user_id=user_id)
    return rows


async def get_template(*, user_id: int, template_id: int) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await template_repository.get(
            conn, template_id=template_id, user_id=user_id,
        )
    return row


async def rename_template(
    *,
    user_id: int,
    template_id: int,
    name: str,
    description: str | None = None,
) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await template_repository.rename(
                conn,
                template_id=template_id,
                user_id=user_id,
                name=name,
                description=description,
            )
    return row


async def delete_template(*, user_id: int, template_id: int) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            ok = await template_repository.delete(
                conn, template_id=template_id, user_id=user_id,
            )
    return ok
