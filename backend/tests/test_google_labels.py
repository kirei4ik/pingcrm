"""Source labels retain Google spelling and filter contacts before pagination."""
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient

from app.models.contact import Contact
from app.models.google_account import GoogleAccount
from app.models.google_contact_link import GoogleContactLink
from app.models.user import User


@pytest.mark.asyncio
async def test_labels_preserve_case_and_filter_all_ids(
    client: AsyncClient, db: AsyncSession, test_user: User, auth_headers: dict,
):
    account = GoogleAccount(user_id=test_user.id, email="labels@example.invalid", refresh_token="synthetic")
    db.add(account)
    await db.flush()
    matched = []
    for number in range(3):
        contact = Contact(user_id=test_user.id, full_name=f"Label {number}")
        db.add(contact)
        await db.flush()
        matched.append(str(contact.id))
        db.add(GoogleContactLink(
            contact_id=contact.id, google_account_id=account.id, resource_name=f"people/test{number}",
            snapshot={}, labels=[{"resource_name": "contactGroups/friends", "name": "02_Друзья"}],
        ))
    db.add(Contact(user_id=test_user.id, full_name="No Google label", tags=["02_друзья"]))
    await db.commit()
    response = await client.get("/api/v1/contacts/google-labels", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["data"] == [{
        "id": f"{account.id}:contactGroups/friends", "name": "02_Друзья", "account_email": account.email,
    }]
    label_id = f"{account.id}:contactGroups/friends"
    response = await client.get("/api/v1/contacts", params={"google_label": label_id, "page_size": 1}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["meta"]["total"] == 3
    assert len(response.json()["data"]) == 1
    response = await client.get("/api/v1/contacts/ids", params={"google_label": label_id}, headers=auth_headers)
    assert sorted(response.json()["data"]) == sorted(matched)


@pytest.mark.asyncio
async def test_other_accounts_labels_do_not_leak(
    client: AsyncClient, db: AsyncSession, test_user: User, auth_headers: dict,
):
    other = User(id=uuid.uuid4(), email="other-label-owner@example.invalid", hashed_password="unused")
    db.add(other)
    await db.flush()
    account = GoogleAccount(user_id=other.id, email="private@example.invalid", refresh_token="synthetic")
    contact = Contact(user_id=other.id, full_name="Other contact")
    db.add_all([account, contact])
    await db.flush()
    db.add(GoogleContactLink(
        contact_id=contact.id, google_account_id=account.id, resource_name="people/private", snapshot={},
        labels=[{"resource_name": "contactGroups/private", "name": "Private"}],
    ))
    await db.commit()
    response = await client.get("/api/v1/contacts/google-labels", headers=auth_headers)
    assert response.json()["data"] == []
    response = await client.get("/api/v1/contacts", params={"google_label": f"{account.id}:contactGroups/private"}, headers=auth_headers)
    assert response.json()["meta"]["total"] == 0
