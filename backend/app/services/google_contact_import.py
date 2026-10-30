"""Persistence helpers for Google Contact provenance and safe scalar imports."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.google_account import GoogleAccount
from app.models.google_contact_link import GoogleContactLink
from app.models.organization import Organization


async def save_contact_link(
    db: AsyncSession, *, contact: Contact, account_id: uuid.UUID | None,
    fields: dict[str, Any],
) -> None:
    resource_name = fields.get("resource_name")
    if not account_id or not resource_name:
        return
    account_result = await db.execute(select(GoogleAccount).where(
        GoogleAccount.id == account_id,
        GoogleAccount.user_id == contact.user_id,
    ))
    if account_result.scalar_one_or_none() is None:
        raise ValueError("Google account does not belong to the contact owner")
    result = await db.execute(select(GoogleContactLink).where(
        GoogleContactLink.google_account_id == account_id,
        GoogleContactLink.resource_name == resource_name,
    ))
    link = result.scalar_one_or_none()
    if link is None:
        link = GoogleContactLink(contact_id=contact.id, google_account_id=account_id, resource_name=resource_name)
        db.add(link)
    link.contact_id = contact.id
    link.etag = fields.get("etag")
    link.snapshot = fields.get("snapshot") or {}
    link.labels = fields.get("labels") or []


def snapshot_scalar(snapshot: dict[str, Any], field: str) -> str | None:
    """Read a CRM scalar's prior value from its full Google Person snapshot."""
    if field in {"full_name", "given_name", "family_name"}:
        names = snapshot.get("names") or []
        if not names:
            return None
        name = next((item for item in names if item.get("metadata", {}).get("primary")), names[0])
        return name.get({"full_name": "displayName", "given_name": "givenName", "family_name": "familyName"}[field])
    if field in {"company", "title"}:
        organizations = snapshot.get("organizations") or []
        if not organizations:
            return None
        organization = next((item for item in organizations if item.get("metadata", {}).get("primary")), organizations[0])
        return organization.get("name" if field == "company" else "title")
    if field == "birthday":
        birthdays = snapshot.get("birthdays") or []
        if not birthdays:
            return None
        birthday = next((item for item in birthdays if item.get("metadata", {}).get("primary")), birthdays[0]).get("date") or {}
        if not birthday.get("month") or not birthday.get("day"):
            return None
        value = f"{birthday['month']:02d}-{birthday['day']:02d}"
        return f"{birthday['year']:04d}-{value}" if birthday.get("year") else value
    if field == "avatar_url":
        photos = snapshot.get("photos") or []
        if not photos:
            return None
        return next((item.get("url") for item in photos if item.get("metadata", {}).get("primary")), photos[0].get("url"))
    return None


async def is_primary_google_account(
    db: AsyncSession, contact_id: uuid.UUID, current_account_id: uuid.UUID | None,
) -> bool:
    """The earliest linked Google account owns scalar refreshes for a CRM contact."""
    if current_account_id is None:
        return True
    current_result = await db.execute(select(GoogleAccount.created_at, GoogleAccount.id).where(
        GoogleAccount.id == current_account_id,
    ))
    current = current_result.first()
    if current is None:
        return False
    links_result = await db.execute(
        select(GoogleAccount.created_at, GoogleAccount.id)
        .join(GoogleContactLink, GoogleContactLink.google_account_id == GoogleAccount.id)
        .where(GoogleContactLink.contact_id == contact_id)
    )
    linked = links_result.all()
    owner = min([*linked, current], key=lambda row: (row[0], str(row[1])))
    return owner[1] == current_account_id


async def link_company_organization(
    db: AsyncSession, contact: Contact, user_id: uuid.UUID,
    *, expected_organization_id: uuid.UUID | None = None,
) -> None:
    """Create/link an organization by exact normalized company name, preserving manual links."""
    company = " ".join((contact.company or "").split())
    if not company or contact.organization_id:
        return
    normalized = company.casefold()
    if expected_organization_id is not None:
        reviewed_result = await db.execute(select(Organization).where(
            Organization.id == expected_organization_id,
            Organization.user_id == user_id,
            func.regexp_replace(func.lower(func.trim(Organization.name)), r"\s+", " ", "g") == normalized,
        ))
        reviewed_organization = reviewed_result.scalar_one_or_none()
        if reviewed_organization is None:
            return
        contact.organization_id = reviewed_organization.id
        return
    await db.execute(select(func.pg_advisory_xact_lock(
        func.hashtext(str(user_id)), func.hashtext(normalized),
    )))
    result = await db.execute(select(Organization).where(
        Organization.user_id == user_id,
        func.regexp_replace(func.lower(func.trim(Organization.name)), r"\s+", " ", "g") == normalized,
    ).limit(1))
    organization = result.scalar_one_or_none()
    if organization is None:
        organization = Organization(user_id=user_id, name=company)
        db.add(organization)
        await db.flush()
    contact.organization_id = organization.id


async def preview_company_backfill(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Preview exact-name organization assignments for unlinked contacts."""
    result = await db.execute(select(Contact).where(
        Contact.user_id == user_id,
        Contact.organization_id.is_(None),
        Contact.company.isnot(None),
        Contact.priority_level != "archived",
    ).order_by(Contact.company, Contact.id))
    grouped: dict[str, dict[str, Any]] = {}
    for contact in result.scalars().all():
        name = " ".join((contact.company or "").split())
        if not name:
            continue
        key = name.casefold()
        group = grouped.setdefault(key, {
            "name": name, "contact_ids": [], "organization_id": None,
            "organization_name": None,
        })
        group["contact_ids"].append(str(contact.id))
    for group in grouped.values():
        match = await db.execute(select(Organization).where(
            Organization.user_id == user_id,
            func.regexp_replace(func.lower(func.trim(Organization.name)), r"\s+", " ", "g") == group["name"].casefold(),
        ).limit(1))
        organization = match.scalar_one_or_none()
        if organization:
            group["organization_id"] = str(organization.id)
            group["organization_name"] = organization.name
    return list(grouped.values())


async def apply_company_backfill(
    db: AsyncSession, user_id: uuid.UUID, reviewed_groups: list[dict[str, Any]],
) -> dict[str, int]:
    """Apply only contact/company pairs explicitly returned by preview."""
    linked = 0
    for group in reviewed_groups:
        expected_name = " ".join((group.get("name") or "").split())
        expected_normalized = expected_name.casefold()
        if not expected_normalized:
            continue
        for raw_contact_id in group.get("contact_ids", []):
            try:
                contact_id = uuid.UUID(raw_contact_id)
            except (ValueError, TypeError, AttributeError):
                continue
            result = await db.execute(select(Contact).where(
                Contact.id == contact_id,
                Contact.user_id == user_id,
                Contact.organization_id.is_(None),
                Contact.priority_level != "archived",
            ))
            contact = result.scalar_one_or_none()
            if not contact or " ".join((contact.company or "").split()).casefold() != expected_normalized:
                continue
            expected_org = group.get("organization_id")
            try:
                expected_org_id = uuid.UUID(expected_org) if expected_org else None
            except (ValueError, TypeError):
                continue
            await link_company_organization(
                db, contact, user_id, expected_organization_id=expected_org_id,
            )
            if contact.organization_id is not None:
                linked += 1
    await db.flush()
    return {"contacts_linked": linked}
