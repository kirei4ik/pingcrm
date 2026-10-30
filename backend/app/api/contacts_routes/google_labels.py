"""Google source labels, separate from user-managed CRM tags."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.contact import Contact
from app.models.google_account import GoogleAccount
from app.models.google_contact_link import GoogleContactLink
from app.models.user import User
from app.schemas.responses import Envelope

router = APIRouter(prefix="/api/v1/contacts", tags=["contacts"])


class GoogleLabelData(BaseModel):
    id: str
    name: str
    account_email: str


@router.get("/google-labels", response_model=Envelope[list[GoogleLabelData]])
async def list_google_labels(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    rows = await db.execute(
        select(GoogleContactLink, GoogleAccount)
        .join(GoogleAccount, GoogleAccount.id == GoogleContactLink.google_account_id)
        .join(Contact, Contact.id == GoogleContactLink.contact_id)
        .where(GoogleAccount.user_id == current_user.id, Contact.user_id == current_user.id)
    )
    labels: dict[str, GoogleLabelData] = {}
    for link, account in rows.all():
        for label in link.labels or []:
            resource_name = label.get("resource_name")
            name = label.get("name")
            if not resource_name or not name:
                continue
            key = f"{account.id}:{resource_name}"
            labels[key] = GoogleLabelData(id=key, name=name, account_email=account.email)
    return {"data": sorted(labels.values(), key=lambda item: (item.account_email, item.name)), "error": None}
