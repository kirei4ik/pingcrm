"""Google Contacts source details and explicit manual export routes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.google_contacts import (
    GoogleContactReadData,
    GoogleExportApplyData,
    GoogleExportApplyRequest,
    GoogleExportPreviewData,
    GoogleExportPreviewRequest,
)
from app.schemas.responses import Envelope
from app.services.google_contact_export import (
    GoogleExportError,
    apply_preview,
    create_preview,
    read_contact,
)

router = APIRouter(prefix="/api/v1/contacts", tags=["contacts"])


def _uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label}") from exc


def _http_error(exc: GoogleExportError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/{contact_id}/google", response_model=Envelope[GoogleContactReadData])
async def get_google_contact_details(
    contact_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        data = await read_contact(db, current_user.id, _uuid(contact_id, "contact ID"))
        return {"data": data, "error": None}
    except GoogleExportError as exc:
        raise _http_error(exc) from exc


@router.post("/{contact_id}/google/preview", response_model=Envelope[GoogleExportPreviewData])
async def preview_google_contact_export(
    contact_id: str,
    payload: GoogleExportPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        data = await create_preview(
            db, current_user.id, _uuid(contact_id, "contact ID"), _uuid(payload.link_id, "Google link ID")
        )
        return {"data": data, "error": None}
    except GoogleExportError as exc:
        raise _http_error(exc) from exc


@router.post("/{contact_id}/google/apply", response_model=Envelope[GoogleExportApplyData])
async def apply_google_contact_export(
    contact_id: str,
    payload: GoogleExportApplyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        data = await apply_preview(
            db, current_user.id, _uuid(contact_id, "contact ID"), payload.preview_id, payload.fields
        )
        return {"data": data, "error": None}
    except GoogleExportError as exc:
        raise _http_error(exc) from exc
