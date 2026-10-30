"""Persistence-backed tests for previewed Google Contact writes."""
import copy
import uuid
from unittest.mock import patch

import fakeredis.aioredis
import pytest

from app.integrations.google_auth import CONTACTS_READ_SCOPE, CONTACTS_WRITE_SCOPE
from app.models.google_account import GoogleAccount
from app.models.google_contact_link import GoogleContactLink
from app.services import google_contact_export as export


class _Request:
    def __init__(self, operation):
        self.operation = operation

    def execute(self):
        return self.operation()


class _People:
    def __init__(self, state):
        self.state = state

    def get(self, **kwargs):
        assert kwargs["resourceName"] == "people/c-linked"
        return _Request(lambda: copy.deepcopy(self.state["person"]))

    def updateContact(self, **kwargs):
        self.state["update_calls"].append(kwargs)

        def update():
            if self.state.get("update_error"):
                raise TimeoutError("simulated write timeout")
            updated = copy.deepcopy(kwargs["body"])
            updated["etag"] = "person-v2"
            metadata = copy.deepcopy(self.state["person"].get("metadata", {}))
            for source in metadata.get("sources", []):
                if source.get("type") == "CONTACT":
                    source["etag"] = "contact-v2"
            updated["metadata"] = metadata
            self.state["person"] = updated
            return copy.deepcopy(updated)

        return _Request(update)


class _Service:
    def __init__(self, state):
        self.state = state

    def people(self):
        return _People(self.state)


def _person():
    return {
        "resourceName": "people/c-linked",
        "etag": "person-v1",
        "metadata": {"sources": [{"type": "PROFILE", "id": "profile-source", "etag": "profile-v1"},
                      {"type": "CONTACT", "id": "contact-source-id", "etag": "contact-v1"}]},
        "names": [
            {"displayName": "Profile Name", "givenName": "Profile", "familyName": "Name",
             "metadata": {"source": {"type": "PROFILE", "id": "profile-source"}}},
            {"displayName": "John Doe", "givenName": "John", "familyName": "Doe",
             "metadata": {"primary": True, "source": {"type": "CONTACT", "id": "contact-source-id"}}},
        ],
        "emailAddresses": [{"value": "john@example.com", "type": "work", "metadata": {"primary": True, "source": {"type": "CONTACT", "id": "contact-source-id"}}}],
        "phoneNumbers": [{"value": "+1111111111", "type": "mobile", "metadata": {"primary": True, "source": {"type": "CONTACT", "id": "contact-source-id"}}}],
        "organizations": [{"name": "Old Co", "title": "Principal", "department": "Research", "metadata": {"primary": True, "source": {"type": "CONTACT", "id": "contact-source-id"}}}],
        "birthdays": [{"date": {"month": 4, "day": 5}, "metadata": {"primary": True, "source": {"type": "CONTACT", "id": "contact-source-id"}}}],
        "urls": [{"value": "https://old.example", "type": "homePage", "metadata": {"primary": True, "source": {"type": "CONTACT", "id": "contact-source-id"}}}],
        "biographies": [{"value": "keep this note"}],
        "addresses": [{"streetAddress": "10 Main St"}],
        "userDefined": [{"key": "preserved", "value": "yes"}],
    }


async def _create_link(db, contact, user, scopes=None):
    account = GoogleAccount(
        user_id=user.id,
        email="google@example.com",
        refresh_token="encrypted-refresh-token",
        granted_scopes=scopes if scopes is not None else [CONTACTS_READ_SCOPE, CONTACTS_WRITE_SCOPE],
    )
    db.add(account)
    await db.flush()
    link = GoogleContactLink(
        contact_id=contact.id,
        google_account_id=account.id,
        resource_name="people/c-linked",
        etag="contact-v1",
        snapshot=_person(),
        labels=[{"resource_name": "contactGroups/work", "name": "Work"}],
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return account, link


@pytest.fixture
def google_people(monkeypatch):
    state = {"person": _person(), "update_calls": []}
    monkeypatch.setattr(export, "refresh_access_token", lambda token: "access-token")
    monkeypatch.setattr(export, "_people_service", lambda access: _Service(state))
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(export, "get_redis", lambda: redis)
    return state, redis


@pytest.mark.asyncio
async def test_preview_apply_is_idempotent_and_preserves_google_metadata(db, test_user, test_contact, google_people):
    state, _ = google_people
    test_contact.emails = ["john@example.com", "new@example.com"]
    test_contact.phones = ["+2222222222"]
    test_contact.company = "Acme Inc"
    test_contact.title = "CEO"
    await db.flush()
    _, link = await _create_link(db, test_contact, test_user)

    preview = await export.create_preview(db, test_user.id, test_contact.id, link.id)
    assert len(state["update_calls"]) == 0
    by_field = {change["field"]: change for change in preview["changes"]}
    assert by_field["emails"]["proposed"] == ["new@example.com"]
    assert by_field["emails"]["selected_by_default"] is False
    assert {"company", "title"}.issubset(by_field)

    result = await export.apply_preview(
        db, test_user.id, test_contact.id, preview["preview_id"], ["emails", "company", "title"]
    )
    assert result == {"status": "updated", "updated_fields": ["emails", "company", "title"]}
    assert len(state["update_calls"]) == 1
    request = state["update_calls"][0]
    body = request["body"]
    assert request["updatePersonFields"] == "emailAddresses,organizations"
    assert body["emailAddresses"] == [
        {"value": "john@example.com", "type": "work", "metadata": {"primary": True, "source": {"type": "CONTACT", "id": "contact-source-id"}}},
        {"value": "new@example.com"},
    ]
    assert body["organizations"] == [{
        "name": "Acme Inc", "title": "CEO", "department": "Research", "metadata": {"primary": True, "source": {"type": "CONTACT", "id": "contact-source-id"}}
    }]
    assert body["metadata"]["sources"] == [{"type": "CONTACT", "id": "contact-source-id", "etag": "contact-v1"}]
    assert "phoneNumbers" not in body and "birthdays" not in body
    assert "biographies" not in body and "addresses" not in body and "userDefined" not in body

    replay = await export.apply_preview(
        db, test_user.id, test_contact.id, preview["preview_id"], ["emails", "company", "title"]
    )
    assert replay == result
    assert len(state["update_calls"]) == 1


@pytest.mark.asyncio
async def test_contact_google_api_returns_typed_details_and_applies_selected_change(
    client, auth_headers, db, test_user, test_contact, google_people
):
    _, link = await _create_link(db, test_contact, test_user)
    response = await client.get(f"/api/v1/contacts/{test_contact.id}/google", headers=auth_headers)
    assert response.status_code == 200
    detail = response.json()["data"]
    assert detail["gmail_enabled"] is False
    assert detail["links"][0]["id"] == str(link.id)
    assert detail["details"][0]["userDefined"] == [{"key": "preserved", "value": "yes"}]

    preview_response = await client.post(
        f"/api/v1/contacts/{test_contact.id}/google/preview",
        json={"link_id": str(link.id)}, headers=auth_headers,
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()["data"]
    assert preview["can_write"] is True
    apply_response = await client.post(
        f"/api/v1/contacts/{test_contact.id}/google/apply",
        json={"preview_id": preview["preview_id"], "fields": ["company"]}, headers=auth_headers,
    )
    assert apply_response.status_code == 200
    assert apply_response.json()["data"] == {"status": "updated", "updated_fields": ["company"]}
@pytest.mark.asyncio
async def test_apply_fails_closed_when_google_etag_changes_after_preview(db, test_user, test_contact, google_people):
    state, _ = google_people
    _, link = await _create_link(db, test_contact, test_user)
    preview = await export.create_preview(db, test_user.id, test_contact.id, link.id)
    state["person"]["etag"] = "person-changed"
    state["person"]["metadata"]["sources"][0]["etag"] = "contact-changed"

    with pytest.raises(export.GoogleExportError, match="Google contact changed") as exc:
        await export.apply_preview(db, test_user.id, test_contact.id, preview["preview_id"], ["company"])
    assert exc.value.status_code == 409
    assert state["update_calls"] == []


@pytest.mark.asyncio
async def test_preview_rejects_foreign_link_and_read_only_account(db, test_user, test_contact, google_people):
    _, link = await _create_link(db, test_contact, test_user)
    with pytest.raises(export.GoogleExportError) as exc:
        await export.create_preview(db, uuid.uuid4(), test_contact.id, link.id)
    assert exc.value.status_code == 404

    link.etag = None
    account = await db.get(GoogleAccount, link.google_account_id)
    account.granted_scopes = [CONTACTS_READ_SCOPE]
    await db.commit()
    with pytest.raises(export.GoogleExportError, match="read-only") as exc:
        await export.create_preview(db, test_user.id, test_contact.id, link.id)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_preview_detects_crm_version_change(db, test_user, test_contact, google_people):
    _, link = await _create_link(db, test_contact, test_user)
    preview = await export.create_preview(db, test_user.id, test_contact.id, link.id)
    test_contact.company = "Changed after preview"
    await db.flush()

    with pytest.raises(export.GoogleExportError, match="Contact changed") as exc:
        await export.apply_preview(db, test_user.id, test_contact.id, preview["preview_id"], ["title"])
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_timeout_is_unknown_and_never_retried_blindly(db, test_user, test_contact, google_people):
    state, _ = google_people
    state["update_error"] = True
    _, link = await _create_link(db, test_contact, test_user)
    preview = await export.create_preview(db, test_user.id, test_contact.id, link.id)

    with pytest.raises(export.GoogleExportError, match="outcome is unknown") as exc:
        await export.apply_preview(db, test_user.id, test_contact.id, preview["preview_id"], ["company"])
    assert exc.value.status_code == 502
    with pytest.raises(export.GoogleExportError, match="outcome is unknown") as retry:
        await export.apply_preview(db, test_user.id, test_contact.id, preview["preview_id"], ["company"])
    assert retry.value.status_code == 409
    assert len(state["update_calls"]) == 1


def test_birthday_validation_uses_leap_year_for_yearless_dates():
    assert export._birthday_date("02-29") == {"month": 2, "day": 29}
    assert export._birthday_date("02-30") is None
    assert export._birthday_date("2024-02-29") == {"year": 2024, "month": 2, "day": 29}
    assert export._birthday_date("2023-02-29") is None


def test_selected_payload_keeps_contact_source_and_excludes_profile_aggregates(db, test_contact):
    test_contact.full_name = "Jane Doe"
    test_contact.given_name = "Jane"
    test_contact.family_name = "Doe"
    body = export._apply_fields(_person(), test_contact, ["emails", "full_name"])
    assert body["metadata"]["sources"] == [{"type": "CONTACT", "id": "contact-source-id", "etag": "contact-v1"}]
    assert all(item["metadata"]["source"]["type"] == "CONTACT" for item in body["names"])
    assert body["names"] == [{"givenName": "Jane", "familyName": "Doe",
                              "metadata": {"primary": True, "source": {"type": "CONTACT", "id": "contact-source-id"}}}]
    assert all(item.get("value") != "profile@example.com" for item in body["emailAddresses"])


def test_name_payload_matches_reviewed_full_name_when_crm_split_is_stale(test_contact):
    test_contact.full_name = "New Complete Name"
    test_contact.given_name = "Old"
    test_contact.family_name = "Name"
    body = export._apply_fields(_person(), test_contact, ["full_name"])
    assert body["names"][0]["givenName"] == "New Complete Name"
    assert body["names"][0]["familyName"] == ""
    assert "displayName" not in body["names"][0]


def test_direct_link_uses_contact_resource_id_and_never_profile_resource():
    snapshot = {"metadata": {"sources": [{"type": "CONTACT", "id": "64857a579572e01d"}]}}
    assert export.google_contact_url(snapshot, "people/c7243330092283387933") == "https://contacts.google.com/person/c7243330092283387933"
    assert export.google_contact_url(snapshot, "people/7243330092283387933") is None
    assert export.google_contact_url({"metadata": {"sources": [{"type": "PROFILE", "id": "123"}]}}, "people/c123") is None
    assert export.google_contact_url(snapshot, "people/c123/../../evil") is None
