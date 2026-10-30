"""Explicit, previewed writes from a PingCRM contact to one linked Google person."""
from __future__ import annotations

import hashlib
import asyncio
import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from calendar import monthrange
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis
from app.integrations.google_auth import refresh_access_token
from app.models.contact import Contact
from app.models.google_account import GoogleAccount
from app.models.google_contact_link import GoogleContactLink

logger = logging.getLogger(__name__)

PREVIEW_TTL = 600
WRITE_SCOPE = "https://www.googleapis.com/auth/contacts"
READ_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"
PEOPLE_FIELDS = "names,emailAddresses,phoneNumbers,organizations,birthdays,urls,biographies,addresses,userDefined,metadata"
ALLOWED_FIELDS = {"emails", "phones", "full_name", "company", "title", "birthday", "urls"}
FIELD_LABELS = {
    "emails": "Email addresses", "phones": "Phone numbers", "full_name": "Name",
    "company": "Company", "title": "Job title", "birthday": "Birthday", "urls": "Links",
}


class GoogleExportError(Exception):
    """Safe error raised by the export service and mapped to an API response."""

    def __init__(self, message: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__(message)


def _people_service(access_token: str) -> Any:
    credentials = Credentials(
        token=access_token,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("people", "v1", credentials=credentials, cache_discovery=False)


def _read_person_sync(account: GoogleAccount, resource_name: str) -> dict[str, Any]:
    token = refresh_access_token(account.refresh_token)
    service = _people_service(token)
    return service.people().get(resourceName=resource_name, personFields=PEOPLE_FIELDS).execute()


def _update_person_sync(account: GoogleAccount, resource_name: str, body: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    token = refresh_access_token(account.refresh_token)
    service = _people_service(token)
    return service.people().updateContact(
        resourceName=resource_name, body=body, updatePersonFields=",".join(fields),
    ).execute()


def _crm_version(contact: Contact) -> str:
    watched = {
        "updated_at": contact.updated_at.isoformat() if contact.updated_at else None,
        "full_name": contact.full_name, "given_name": contact.given_name,
        "family_name": contact.family_name, "emails": contact.emails or [],
        "phones": contact.phones or [], "company": contact.company,
        "title": contact.title, "birthday": contact.birthday,
        "linkedin_url": contact.linkedin_url,
    }
    raw = json.dumps(watched, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _contact_items(person: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Return entries owned by this writable CONTACT source, excluding profile aggregation."""
    return [item for item in person.get(key, [])
            if item.get("metadata", {}).get("source", {}).get("type") == "CONTACT"]


def _values(person: dict[str, Any], field: str) -> list[str]:
    if field == "emails":
        return [item["value"] for item in _contact_items(person, "emailAddresses") if item.get("value")]
    if field == "phones":
        return [item["value"] for item in _contact_items(person, "phoneNumbers") if item.get("value")]
    if field == "urls":
        return [item["value"] for item in _contact_items(person, "urls") if item.get("value")]
    if field == "full_name":
        return [" ".join(part for part in (item.get("givenName"), item.get("familyName")) if part).strip()
                for item in _contact_items(person, "names")
                if item.get("givenName") or item.get("familyName")]
    if field == "company":
        return [item["name"] for item in _contact_items(person, "organizations") if item.get("name")]
    if field == "title":
        return [item["title"] for item in _contact_items(person, "organizations") if item.get("title")]
    if field == "birthday":
        out = []
        for item in _contact_items(person, "birthdays"):
            date = item.get("date") or {}
            if date.get("month") and date.get("day"):
                out.append(f"{date.get('year', ''):04}-{date['month']:02}-{date['day']:02}" if date.get("year") else f"{date['month']:02}-{date['day']:02}")
        return out
    return []


def _contact_source(person: dict[str, Any]) -> dict[str, Any] | None:
    for source in person.get("metadata", {}).get("sources", []):
        if source.get("type") == "CONTACT" and source.get("etag") and source.get("id"):
            return json.loads(json.dumps(source))
    return None


def _contact_source_etag(person: dict[str, Any]) -> str | None:
    source = _contact_source(person)
    return source.get("etag") if source else None


def _proposed(contact: Contact, field: str) -> list[str]:
    if field == "emails": return list(contact.emails or [])
    if field == "phones": return list(contact.phones or [])
    if field == "urls": return [contact.linkedin_url] if contact.linkedin_url else []
    if field == "full_name": return [contact.full_name] if contact.full_name else []
    if field == "company": return [contact.company] if contact.company else []
    if field == "title": return [contact.title] if contact.title else []
    if field == "birthday": return [contact.birthday] if contact.birthday else []
    return []


def _changes(contact: Contact, person: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for field in ("emails", "phones", "full_name", "company", "title", "birthday", "urls"):
        current = _values(person, field)
        proposed = _proposed(contact, field)
        if field in {"emails", "phones", "urls"}:
            delta = [value for value in proposed if value not in current]
            if not delta:
                continue
            display_proposed: Any = delta
        else:
            if not proposed or proposed[0] in current:
                continue
            display_proposed = proposed[0]
        result.append({"field": field, "label": FIELD_LABELS[field], "current": current, "proposed": display_proposed, "selected_by_default": False})
    return result


def _append_values(person: dict[str, Any], key: str, values: list[str]) -> None:
    items = person.setdefault(key, [])
    existing = {item.get("value") for item in items}
    for value in values:
        if value not in existing:
            items.append({"value": value})
            existing.add(value)


def _birthday_date(value: str) -> dict[str, int] | None:
    parts = value.split("-")
    try:
        if len(parts) == 3:
            year, month, day = map(int, parts)
            if not 1 <= year <= 9999 or not 1 <= month <= 12 or not 1 <= day <= monthrange(year, month)[1]:
                return None
            return {"year": year, "month": month, "day": day}
        if len(parts) == 2:
            month, day = map(int, parts)
            if not 1 <= month <= 12 or not 1 <= day <= monthrange(2000, month)[1]:
                return None
            return {"month": month, "day": day}
    except ValueError:
        return None
    return None


def _apply_fields(person: dict[str, Any], contact: Contact, fields: list[str]) -> dict[str, Any]:
    """Construct a People PATCH body from the latest full person, preserving metadata."""
    body: dict[str, Any] = {"etag": person.get("etag")}
    for field in fields:
        proposed = _proposed(contact, field)
        if not proposed:
            continue
        if field == "emails":
            body["emailAddresses"] = json.loads(json.dumps(_contact_items(person, "emailAddresses")))
            _append_values(body, "emailAddresses", proposed)
        elif field == "phones":
            body["phoneNumbers"] = json.loads(json.dumps(_contact_items(person, "phoneNumbers")))
            _append_values(body, "phoneNumbers", proposed)
        elif field == "urls":
            body["urls"] = json.loads(json.dumps(_contact_items(person, "urls")))
            _append_values(body, "urls", proposed)
        elif field == "full_name":
            names = body["names"] = json.loads(json.dumps(_contact_items(person, "names")))
            if len(names) > 1:
                raise GoogleExportError("Google contact has multiple CONTACT names. Resolve it in Google and create a new preview.", 409)
            if names:
                names[0].pop("displayName", None)
                split_name = " ".join(part for part in (contact.given_name, contact.family_name) if part).strip()
                if split_name == contact.full_name.strip():
                    names[0]["givenName"] = contact.given_name or ""
                    names[0]["familyName"] = contact.family_name or ""
                else:
                    names[0]["givenName"] = contact.full_name
                    names[0]["familyName"] = ""
            else:
                split_name = " ".join(part for part in (contact.given_name, contact.family_name) if part).strip()
                if split_name == contact.full_name.strip():
                    names.append({"givenName": contact.given_name or "", "familyName": contact.family_name or ""})
                else:
                    names.append({"givenName": contact.full_name, "familyName": ""})
        elif field in {"company", "title"}:
            if "organizations" not in body:
                body["organizations"] = json.loads(json.dumps(_contact_items(person, "organizations")))
            orgs = body["organizations"]
            if not orgs: orgs.append({})
            orgs[0]["name" if field == "company" else "title"] = proposed[0]
        elif field == "birthday":
            date = _birthday_date(proposed[0])
            if date is None:
                raise GoogleExportError("Birthday must use MM-DD or YYYY-MM-DD format")
            birthdays = body["birthdays"] = json.loads(json.dumps(_contact_items(person, "birthdays")))
            if len(birthdays) > 1:
                raise GoogleExportError("Google contact has multiple CONTACT birthdays. Resolve it in Google and create a new preview.", 409)
            if birthdays:
                birthdays[0]["date"] = date
            else:
                birthdays.append({"date": date})
    source = _contact_source(person)
    if source is None:
        raise GoogleExportError("The linked Google contact has no writable CONTACT source. Re-import it before writing.", 409)
    body["metadata"] = {"sources": [source]}
    return body


async def _owned(
    db: AsyncSession, user_id: uuid.UUID, contact_id: uuid.UUID, link_id: uuid.UUID,
    *, lock_for_update: bool = False,
):
    statement = (
        select(Contact, GoogleContactLink, GoogleAccount)
        .join(GoogleContactLink, GoogleContactLink.contact_id == Contact.id)
        .join(GoogleAccount, GoogleAccount.id == GoogleContactLink.google_account_id)
        .where(Contact.id == contact_id, Contact.user_id == user_id,
               GoogleContactLink.id == link_id, GoogleAccount.user_id == user_id)
    )
    if lock_for_update:
        # Hold the rows stable through the remote PATCH so the CRM version and
        # linked resource cannot change after their conflict checks.
        statement = statement.with_for_update()
    result = await db.execute(statement)
    row = result.first()
    if not row:
        raise GoogleExportError("Google contact link not found", 404)
    return row[0], row[1], row[2]


async def _get_person(account: GoogleAccount, resource_name: str) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_read_person_sync, account, resource_name)
    except Exception as exc:
        logger.warning("Google People read failed for linked person", extra={"account_id": str(account.id)}, exc_info=True)
        raise GoogleExportError("Could not read the linked Google contact. Reconnect the account and try again.", 502) from exc


def google_contact_url(snapshot: dict[str, Any], resource_name: str) -> str | None:
    """Link canonical contact resources only, never profile IDs or guessed source conversions."""
    contacts = [source for source in snapshot.get("metadata", {}).get("sources", []) if source.get("type") == "CONTACT"]
    if len(contacts) != 1:
        return None
    match = re.fullmatch(r"people/(c[1-9][0-9]*)", resource_name)
    if not match:
        return None
    return f"https://contacts.google.com/person/{match.group(1)}"


async def read_contact(db: AsyncSession, user_id: uuid.UUID, contact_id: uuid.UUID) -> dict[str, Any]:
    result = await db.execute(
        select(GoogleContactLink, GoogleAccount).join(GoogleAccount, GoogleContactLink.google_account_id == GoogleAccount.id)
        .join(Contact, GoogleContactLink.contact_id == Contact.id)
        .where(Contact.id == contact_id, Contact.user_id == user_id, GoogleAccount.user_id == user_id)
    )
    links, details = [], []
    for link, account in result.all():
        scopes = set(account.granted_scopes or [])
        can_write = WRITE_SCOPE in scopes and bool(link.etag)
        person = link.snapshot or {}
        links.append({"id": str(link.id), "account_email": account.email, "resource_name": link.resource_name,
                      "labels": link.labels or [], "google_url": google_contact_url(person, link.resource_name), "can_write": can_write})
        details.append({"link_id": str(link.id), "birthdays": person.get("birthdays", []),
                        "biographies": person.get("biographies", []), "addresses": person.get("addresses", []),
                        "urls": person.get("urls", []), "user_defined": person.get("userDefined", [])})
    return {"gmail_enabled": settings.GOOGLE_GMAIL_ENABLED, "links": links, "details": details}


async def create_preview(db: AsyncSession, user_id: uuid.UUID, contact_id: uuid.UUID, link_id: uuid.UUID) -> dict[str, Any]:
    contact, link, account = await _owned(db, user_id, contact_id, link_id)
    scopes = set(account.granted_scopes or [])
    if WRITE_SCOPE not in scopes:
        raise GoogleExportError("This Google account has read-only Contacts access. Reconnect with Contacts write access.", 403)
    person = await _get_person(account, link.resource_name)
    source_etag = _contact_source_etag(person)
    if not source_etag or not person.get("etag"):
        raise GoogleExportError("The linked Google contact has no verifiable CONTACT version. Re-import it before writing.", 409)
    changes = _changes(contact, person)
    preview_id = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + timedelta(seconds=PREVIEW_TTL)
    payload = {"user_id": str(user_id), "contact_id": str(contact_id), "link_id": str(link.id),
               "account_id": str(account.id), "resource_name": link.resource_name,
               "crm_version": _crm_version(contact), "etag": person.get("etag"), "source_etag": source_etag,
               "changes": changes, "proposed": {field: _proposed(contact, field) for field in ALLOWED_FIELDS},
               "created_at": datetime.now(UTC).isoformat()}
    try:
        await get_redis().setex(f"google_export:preview:{preview_id}", PREVIEW_TTL, json.dumps(payload))
    except Exception as exc:
        logger.error("Unable to store Google export preview", exc_info=True)
        raise GoogleExportError("Google export preview is temporarily unavailable", 503) from exc
    return {"preview_id": preview_id, "account_email": account.email, "expires_at": expires_at,
            "changes": changes, "can_write": True}


async def apply_preview(db: AsyncSession, user_id: uuid.UUID, contact_id: uuid.UUID, preview_id: str, fields: list[str]) -> dict[str, Any]:
    if not fields or any(field not in ALLOWED_FIELDS for field in fields) or len(fields) != len(set(fields)):
        raise GoogleExportError("Select one or more valid preview fields")
    redis = get_redis()
    preview_key = f"google_export:preview:{preview_id}"
    result_key = f"google_export:result:{preview_id}"
    try:
        prior = await redis.get(result_key)
        if prior:
            record = json.loads(prior)
            if record.get("user_id") != str(user_id) or record.get("contact_id") != str(contact_id):
                raise GoogleExportError("Preview does not belong to this contact", 404)
            if record.get("error"):
                raise GoogleExportError(record["error"], record.get("status_code", 409))
            return record["outcome"]
        raw = await redis.get(preview_key)
    except GoogleExportError:
        raise
    except Exception as exc:
        logger.error("Unable to retrieve Google export preview", exc_info=True)
        raise GoogleExportError("Google export preview is temporarily unavailable", 503) from exc
    if not raw:
        raise GoogleExportError("Preview expired or not found. Create a new preview.", 409)
    payload = json.loads(raw)
    if payload["user_id"] != str(user_id) or payload["contact_id"] != str(contact_id):
        raise GoogleExportError("Preview does not belong to this contact", 404)
    allowed_in_preview = {change["field"] for change in payload["changes"]}
    if not set(fields).issubset(allowed_in_preview):
        raise GoogleExportError("Selected fields must come from this preview")
    contact, link, account = await _owned(
        db, user_id, contact_id, uuid.UUID(payload["link_id"]), lock_for_update=True
    )
    if str(account.id) != payload["account_id"] or link.resource_name != payload["resource_name"]:
        raise GoogleExportError("Google link changed. Create a new preview.", 409)
    if _crm_version(contact) != payload["crm_version"]:
        raise GoogleExportError("Contact changed after preview. Create a new preview.", 409)
    if WRITE_SCOPE not in set(account.granted_scopes or []):
        raise GoogleExportError("This Google account no longer has Contacts write access. Reconnect before writing.", 403)
    lock_key = f"google_export:account_lock:{account.id}"
    lock_token = str(uuid.uuid4())
    try:
        locked = await redis.set(lock_key, lock_token, nx=True, ex=300)
    except Exception as exc:
        logger.error("Google export lock unavailable", exc_info=True)
        raise GoogleExportError("Google export is temporarily unavailable", 503) from exc
    if not locked:
        raise GoogleExportError("Another Google contact update is in progress. Try again shortly.", 409)
    try:
        # Claim before the external side effect. A repeated request after a crash or timeout is unknown,
        # never an implicit retry.
        claimed = await redis.set(f"google_export:started:{preview_id}", "1", nx=True, ex=3600)
        if not claimed:
            prior = await redis.get(result_key)
            if prior:
                record = json.loads(prior)
                if record.get("user_id") != str(user_id) or record.get("contact_id") != str(contact_id):
                    raise GoogleExportError("Preview does not belong to this contact", 404)
                if record.get("error"):
                    raise GoogleExportError(record["error"], record.get("status_code", 409))
                return record["outcome"]
            raise GoogleExportError("Update outcome is unknown. Re-read the Google contact before retrying.", 409)
        latest = await _get_person(account, link.resource_name)
        if latest.get("etag") != payload.get("etag") or _contact_source_etag(latest) != payload.get("source_etag"):
            await redis.setex(result_key, 86400, json.dumps({
                "user_id": str(user_id), "contact_id": str(contact_id),
                "error": "Google contact changed after preview. Create a new preview.", "status_code": 409,
            }))
            raise GoogleExportError("Google contact changed after preview. Create a new preview.", 409)
        body = _apply_fields(latest, contact, fields)
        update_fields = []
        for field in fields:
            update_fields.extend({"emails": ["emailAddresses"], "phones": ["phoneNumbers"],
                                  "full_name": ["names"], "company": ["organizations"],
                                  "title": ["organizations"], "birthday": ["birthdays"],
                                  "urls": ["urls"]}[field])
        # De-duplicate organization when both company/title were selected.
        update_fields = sorted(set(update_fields))
        try:
            updated = await asyncio.to_thread(_update_person_sync, account, link.resource_name, body, update_fields)
        except Exception as exc:
            logger.warning("Google contact update did not complete normally", extra={"account_id": str(account.id)})
            if isinstance(exc, HttpError):
                status = getattr(exc.resp, "status", 0)
                detail = str(getattr(exc, "content", b"")).lower()
                if status in {400, 404, 412} and (status != 400 or "failedprecondition" in detail or "etag" in detail):
                    message = "Google contact changed after preview. Create a new preview."
                    await redis.setex(result_key, 86400, json.dumps({
                        "user_id": str(user_id), "contact_id": str(contact_id), "error": message, "status_code": 409,
                    }))
                    logger.warning("Google rejected contact update due to stale etag", extra={"account_id": str(account.id), "status": status})
                    raise GoogleExportError(message, 409) from exc
                if status in {401, 403}:
                    logger.warning("Google denied contact update", extra={"account_id": str(account.id), "status": status})
                    raise GoogleExportError("Google denied Contacts write access. Reconnect the account and try again.", 403) from exc
            logger.error("Google People update outcome unknown", extra={"account_id": str(account.id)}, exc_info=True)
            raise GoogleExportError("Google update outcome is unknown. Re-read the Google contact before retrying.", 502) from exc
        link.etag = _contact_source_etag(updated) or _contact_source_etag(latest)
        # Google may return only the changed projection. Merge it over the full
        # imported snapshot so labels and unrelated source fields remain visible.
        link.snapshot = {**(link.snapshot or {}), **updated}
        outcome = {"status": "updated", "updated_fields": fields}
        await redis.setex(result_key, 86400, json.dumps({
            "user_id": str(user_id), "contact_id": str(contact_id), "outcome": outcome,
        }))
        logger.info(
            "Google contact manually updated",
            extra={"operation": "google_contact_export", "user_id": str(user_id),
                   "contact_id": str(contact_id), "google_account_id": str(account.id),
                   "google_contact_link_id": str(link.id), "fields": fields,
                   "status": "updated"},
        )
        return outcome
    finally:
        try:
            await redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1, lock_key, lock_token,
            )
        except Exception:
            logger.warning("Could not release Google export lock", extra={"account_id": str(account.id)}, exc_info=True)
