"""Persistence tests for Google provenance and safe organization recovery."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.organization import Organization
from app.models.user import User
from app.services.google_contact_import import apply_company_backfill, preview_company_backfill


@pytest.mark.asyncio
async def test_company_backfill_preview_groups_exact_normalized_names_and_apply_is_review_scoped(
    db: AsyncSession, test_user: User,
):
    first = Contact(user_id=test_user.id, full_name="A", company="Acme   Group")
    second = Contact(user_id=test_user.id, full_name="B", company=" acme group ")
    unreviewed = Contact(user_id=test_user.id, full_name="C", company="Another Company")
    manual_org = Organization(user_id=test_user.id, name="Manual Org")
    db.add(manual_org)
    await db.flush()
    manually_assigned = Contact(
        user_id=test_user.id, full_name="D", company="Acme Group", organization_id=manual_org.id,
    )
    db.add_all([first, second, unreviewed, manually_assigned])
    await db.flush()

    preview = await preview_company_backfill(db, test_user.id)
    acme_group = next(group for group in preview if group["name"].casefold() == "acme group")
    assert set(acme_group["contact_ids"]) == {str(first.id), str(second.id)}
    assert all(group["name"] != "Manual Org" for group in preview)

    first.company = "Changed after preview"
    result = await apply_company_backfill(db, test_user.id, [acme_group])
    await db.flush()
    assert result == {"contacts_linked": 1}
    assert first.organization_id is None
    assert second.organization_id is not None
    assert unreviewed.organization_id is None
    assert manually_assigned.organization_id == manual_org.id


@pytest.mark.asyncio
async def test_company_import_reuses_existing_user_organization_without_duplicate(
    db: AsyncSession, test_user: User,
):
    from app.services.google_contact_import import link_company_organization

    existing = Organization(user_id=test_user.id, name="  Northwind  ")
    contact = Contact(user_id=test_user.id, company="northwind")
    db.add_all([existing, contact])
    await db.flush()
    await link_company_organization(db, contact, test_user.id)
    organizations = await db.execute(select(Organization).where(Organization.user_id == test_user.id))
    assert len(list(organizations.scalars().all())) == 1
    assert contact.organization_id == existing.id


@pytest.mark.asyncio
async def test_merge_preserves_each_google_source_and_manual_fields(db, test_user):
    from app.models.google_account import GoogleAccount
    from app.models.google_contact_link import GoogleContactLink
    from app.services.identity_resolution import merge_contacts

    first = Contact(user_id=test_user.id, full_name="First", emails=["same@example.com"])
    second = Contact(user_id=test_user.id, full_name="Second", emails=["same@example.com"],
                     birthday="02-29", user_edited_fields=["birthday"])
    account = GoogleAccount(user_id=test_user.id, email="source@example.com", refresh_token="fake")
    db.add_all([first, second, account])
    await db.flush()
    for contact, resource in ((first, "people/first"), (second, "people/second")):
        db.add(GoogleContactLink(contact_id=contact.id, google_account_id=account.id,
                                resource_name=resource, snapshot={"resourceName": resource},
                                labels=[{"resource_name": "contactGroups/friends", "name": "Друзья"}]))
    await db.flush()
    await merge_contacts(first.id, second.id, db)
    survivors = list((await db.execute(select(Contact).where(Contact.user_id == test_user.id))).scalars())
    assert len(survivors) == 1
    survivor = survivors[0]
    assert survivor.birthday == "02-29"
    assert "birthday" in survivor.user_edited_fields
    links = list((await db.execute(select(GoogleContactLink))).scalars())
    assert {link.resource_name for link in links} == {"people/first", "people/second"}
    assert all(link.contact_id == survivor.id and link.labels[0]["name"] == "Друзья" for link in links)
