"""Google Contacts one-way sync using the People API."""
from __future__ import annotations

from datetime import date as calendar_date
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.core.config import settings


def _build_people_service(access_token: str) -> Any:
    credentials = Credentials(
        token=access_token,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("people", "v1", credentials=credentials, cache_discovery=False)


def _name_from_email(email: str) -> tuple[str, str] | None:
    """Try to extract (given_name, family_name) from an email local part.

    Handles patterns like:
      david.rodriguez@company.co  → ("David", "Rodriguez")
      john_smith@gmail.com        → ("John", "Smith")
      jane-doe@example.com        → ("Jane", "Doe")
      jdoe@example.com            → None (too ambiguous)
    """
    local = email.split("@")[0].lower()
    # Remove trailing digits (e.g. john.smith01)
    import re
    local = re.sub(r"\d+$", "", local)
    # Split on common separators
    parts = re.split(r"[._\-]", local)
    # Filter out empty or single-char fragments
    parts = [p for p in parts if len(p) > 1]
    if len(parts) < 2:
        return None
    # Take first two parts as given/family
    given = parts[0].capitalize()
    family = parts[1].capitalize()
    return given, family


def _extract_contact_fields(person: dict[str, Any]) -> dict[str, Any]:
    """Map a Google People API person resource to our Contact model fields."""
    names = person.get("names", [])
    given_name: str | None = None
    family_name: str | None = None
    full_name: str | None = None
    if names:
        primary = next((n for n in names if n.get("metadata", {}).get("primary")), names[0])
        given_name = primary.get("givenName")
        family_name = primary.get("familyName")
        full_name = primary.get("displayName")

    email_addresses = person.get("emailAddresses", [])
    emails: list[str] = [
        e["value"] for e in email_addresses if e.get("value")
    ]

    phone_numbers = person.get("phoneNumbers", [])
    phones: list[str] = [
        p["value"] for p in phone_numbers if p.get("value")
    ]

    organizations = person.get("organizations", [])
    company: str | None = None
    title: str | None = None
    if organizations:
        org = next((o for o in organizations if o.get("metadata", {}).get("primary")), organizations[0])
        company = org.get("name")
        title = org.get("title")

    # Infer names from email when Google doesn't provide them
    if not given_name and not family_name and not full_name and emails:
        inferred = _name_from_email(emails[0])
        if inferred:
            given_name, family_name = inferred
            full_name = f"{given_name} {family_name}".strip() or None

    birthdays = person.get("birthdays", [])
    birthday = None
    if birthdays:
        date = next((b.get("date") for b in birthdays if b.get("metadata", {}).get("primary")), birthdays[0].get("date"))
        if date and date.get("month") and date.get("day"):
            # People API uses year=0 when the year is unknown.
            try:
                month, day_value = int(date["month"]), int(date["day"])
                year = int(date.get("year") or 0)
                calendar_date(year or 2000, month, day_value)
                birthday = f"{month:02d}-{day_value:02d}"
                if year:
                    birthday = f"{year:04d}-{month:02d}-{day_value:02d}"
            except (ValueError, TypeError, KeyError):
                birthday = None
    group_names = person.get("_contact_group_names", {})
    contact_source = next(
        (source for source in person.get("metadata", {}).get("sources", []) if source.get("type") == "CONTACT"),
        {},
    )
    labels = []
    for membership in person.get("memberships", []):
        member = membership.get("contactGroupMembership", {})
        group = member.get("contactGroupResourceName")
        name = group_names.get(group)
        group_id = member.get("contactGroupId", "")
        if group and name and group_id not in {"myContacts", "starred", "friends", "family", "coworkers"}:
            labels.append({"resource_name": group, "name": name})

    snapshot = {key: value for key, value in person.items() if key != "_contact_group_names"}
    return {
        "full_name": full_name,
        "given_name": given_name,
        "family_name": family_name,
        "emails": emails or [],
        "phones": phones or [],
        "company": company,
        "title": title,
        "birthday": birthday,
        "avatar_url": next((p.get("url") for p in person.get("photos", []) if p.get("metadata", {}).get("primary")), (person.get("photos") or [{}])[0].get("url")),
        "labels": labels,
        "etag": contact_source.get("etag"),
        "snapshot": snapshot,
        "source": "google",
        "resource_name": person.get("resourceName"),
    }


def fetch_google_contacts(access_token: str) -> list[dict[str, Any]]:
    """Fetch all connections from the authenticated user's Google account.

    Returns a list of mapped contact field dicts ready to be merged into the DB.
    """
    service = _build_people_service(access_token)
    contacts: list[dict[str, Any]] = []
    page_token: str | None = None

    groups: dict[str, str] = {}
    group_token = None
    while True:
        # resourceName is returned automatically; Google rejects it in groupFields.
        group_kwargs: dict[str, Any] = {"pageSize": 1000, "groupFields": "name,groupType"}
        if group_token:
            group_kwargs["pageToken"] = group_token
        group_response = service.contactGroups().list(**group_kwargs).execute()
        for group in group_response.get("contactGroups", []):
            resource_name = group.get("resourceName", "")
            system_ids = {"myContacts", "starred", "friends", "family", "coworkers", "otherContacts", "blocked"}
            resource_id = resource_name.rsplit("/", 1)[-1]
            if (group.get("resourceName") and group.get("name")
                    and group.get("groupType") != "SYSTEM_CONTACT_GROUP"
                    and resource_id not in system_ids):
                groups[resource_name] = group["name"]
        group_token = group_response.get("nextPageToken")
        if not isinstance(group_token, str) or not group_token:
            break

    while True:
        kwargs: dict[str, Any] = {
            "resourceName": "people/me",
            "pageSize": 1000,
            "personFields": "names,emailAddresses,phoneNumbers,organizations,birthdays,biographies,addresses,urls,userDefined,photos,memberships,metadata",
        }
        if page_token:
            kwargs["pageToken"] = page_token

        response = service.people().connections().list(**kwargs).execute()
        connections: list[dict[str, Any]] = response.get("connections", [])
        for person in connections:
            person["_contact_group_names"] = groups
            fields = _extract_contact_fields(person)
            # Skip contacts without any identifying information.
            if fields["full_name"] or fields["emails"] or fields["phones"]:
                contacts.append(fields)

        page_token = response.get("nextPageToken")
        if not isinstance(page_token, str) or not page_token:
            break

    return contacts
