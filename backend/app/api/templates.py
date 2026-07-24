"""Routes for `/api/v1/templates` — backed by `app.report_template`."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.infra.auth.deps import get_current_user
from app.services import template_service

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


@router.post("")
async def create_template(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="INVALID_TEMPLATE_NAME")
    description = (payload.get("description") or "").strip()
    requirement_payload = payload.get("requirement_payload")
    if not isinstance(requirement_payload, dict):
        raise HTTPException(status_code=422, detail="INVALID_REQUIREMENT_PAYLOAD")
    try:
        row = await template_service.create_template(
            user_id=user["id"],
            name=name,
            description=description,
            requirement_payload=requirement_payload,
        )
    except Exception as exc:
        # asyncpg UniqueViolationError on (user_id, name)
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="TEMPLATE_NAME_TAKEN")
        raise HTTPException(status_code=500, detail=f"INTERNAL: {exc}")
    return {"template": row}


@router.get("")
async def list_templates(user: dict = Depends(get_current_user)):
    rows = await template_service.list_templates(user_id=user["id"])
    return {"templates": rows}


@router.delete("/{template_id}")
async def delete_template(
    template_id: int,
    user: dict = Depends(get_current_user),
):
    ok = await template_service.delete_template(
        user_id=user["id"], template_id=template_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="TEMPLATE_NOT_FOUND")
    return {"deleted": True}


@router.patch("/{template_id}")
async def rename_template(
    template_id: int,
    payload: dict,
    user: dict = Depends(get_current_user),
):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="INVALID_TEMPLATE_NAME")
    description = payload.get("description")
    row = await template_service.rename_template(
        user_id=user["id"],
        template_id=template_id,
        name=name,
        description=description,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="TEMPLATE_NOT_FOUND")
    return {"template": row}
