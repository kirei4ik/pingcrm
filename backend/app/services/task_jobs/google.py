"""Google Contacts and Calendar sync Celery tasks.

The Celery entrypoints (``sync_google_contacts_for_user`` /
``sync_google_calendar_for_user``) are thin wrappers around the
``_sync_google_contacts`` / ``_sync_google_calendar`` coroutines so that the
sync logic is directly unit-testable against a real ``AsyncSession`` without
spinning up a Celery broker or the ``task_session`` machinery.
"""
from __future__ import annotations

import uuid

from celery import shared_task
from google.auth.exceptions import RefreshError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import task_session
from app.integrations.google_auth import refresh_access_token
from app.integrations.google_calendar import sync_calendar_events
from app.integrations.google_contacts import fetch_google_contacts
from app.models.contact import Contact
from app.models.google_account import GoogleAccount
from app.models.google_contact_link import GoogleContactLink
from app.models.notification import Notification
from app.models.user import User
from app.services.contact_resolver import normalize_email
from app.services.google_contact_import import (
    is_primary_google_account,
    link_company_organization,
    save_contact_link,
    snapshot_scalar,
)
from app.services.scoring import calculate_score
from app.services.task_jobs.common import _run, logger, notify_sync_failure

async def _sync_google_contacts(db: AsyncSession, uid: uuid.UUID) -> dict:
    """Pull contacts from every Google account belonging to ``uid``.

    Returns a status dict; never raises for per-account or per-contact errors —
    those are reported via the result counts plus a per-user notification.
    """
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if user is None:
        return {"status": "user_not_found"}

    ga_result = await db.execute(
        select(GoogleAccount).where(GoogleAccount.user_id == uid)
    )
    google_accounts = list(ga_result.scalars().all())
    refresh_tokens = [(ga.id, ga.email, ga.refresh_token) for ga in google_accounts]
    if not refresh_tokens and user.google_refresh_token:
        refresh_tokens = [(None, user.email, user.google_refresh_token)]
    if not refresh_tokens:
        return {"status": "not_connected"}

    created_count = 0
    updated_count = 0
    archived_count = 0
    errors: list[str] = []
    successful_accounts = 0

    for account_id, account_email, token in refresh_tokens:
        try:
            access_token = refresh_access_token(token)
            google_contacts = fetch_google_contacts(access_token)
        except RefreshError as exc:
            logger.warning(
                "sync_google_contacts: auth expired for %s",
                account_email,
                extra={"provider": "google", "email": account_email},
            )
            errors.append(f"{account_email}: {exc}")
            db.add(Notification(
                user_id=uid,
                notification_type="error",
                title="Google Contacts sync failed — re-authentication needed",
                body=f"The OAuth token for {account_email} has expired or been revoked. Please reconnect your Google account in Settings.",
                link="/settings",
            ))
            continue
        except Exception as exc:
            logger.warning(
                "sync_google_contacts: failed to fetch contacts for %s",
                account_email,
                exc_info=True,
            )
            errors.append(f"{account_email}: {exc}")
            continue

        successful_accounts += 1

        for fields in google_contacts:
            try:
                resource_name = fields.get("resource_name")
                raw_name = fields.get("full_name")

                emails = fields.get("emails") or []
                existing = None
                source_link = None
                prior_snapshot = None
                source_verified = False
                legacy_exact = False

                if resource_name and account_id:
                    r = await db.execute(
                        select(GoogleContactLink).where(
                            GoogleContactLink.google_account_id == account_id,
                            GoogleContactLink.resource_name == resource_name,
                        )
                    )
                    source_link = r.scalar_one_or_none()
                    if source_link:
                        existing = await db.get(Contact, source_link.contact_id)
                        if existing and existing.user_id == uid:
                            source_verified = True
                            prior_snapshot = source_link.snapshot or {}
                        else:
                            existing = None

                    # Legacy scalar links can be safely adopted only when this
                    # is the user's sole configured account. The returned
                    # resourceName then verifies the source before linking.
                    if not existing and len(google_accounts) == 1:
                        r = await db.execute(select(Contact).where(
                            Contact.user_id == uid,
                            Contact.google_resource_name == resource_name,
                        ).limit(1))
                        existing = r.scalar_one_or_none()
                        legacy_exact = existing is not None
                        source_verified = legacy_exact
                elif resource_name and not google_accounts:
                    # The legacy user-level token represents the sole
                    # configured source; keep its historical scalar mapping.
                    r = await db.execute(select(Contact).where(
                        Contact.user_id == uid,
                        Contact.google_resource_name == resource_name,
                    ).limit(1))
                    existing = r.scalar_one_or_none()
                    legacy_exact = existing is not None
                    source_verified = legacy_exact

                if not existing and emails:
                    for email in emails:
                        norm = normalize_email(email)
                        if not norm:
                            continue
                        r = await db.execute(
                            text(
                                """
                                SELECT id FROM contacts
                                WHERE user_id = :uid
                                  AND EXISTS (
                                    SELECT 1 FROM unnest(emails) e
                                    WHERE lower(trim(e)) = :norm
                                  )
                                LIMIT 1
                                """
                            ),
                            {"uid": uid, "norm": norm},
                        )
                        row = r.first()
                        if row:
                            existing = await db.get(Contact, row[0])
                            if existing:
                                break

                if not existing and resource_name and len(google_accounts) > 1:
                    legacy_result = await db.execute(select(Contact.id).where(
                        Contact.user_id == uid,
                        Contact.google_resource_name == resource_name,
                    ).limit(1))
                    if legacy_result.scalar_one_or_none() is not None:
                        errors.append(f"{account_email}: unresolved legacy Google link {resource_name} (multiple accounts)")
                        continue

                if existing:
                    edited = set(existing.user_edited_fields or [])
                    primary_google_account = await is_primary_google_account(db, existing.id, account_id)
                    for attribute, field in (("full_name", "full_name"), ("given_name", "given_name"), ("family_name", "family_name"), ("company", "company"), ("title", "title"), ("birthday", "birthday"), ("avatar_url", "avatar_url")):
                        proposed = fields.get(field)
                        current_value = getattr(existing, attribute)
                        source_value_matches = (
                            prior_snapshot is not None
                            and snapshot_scalar(prior_snapshot, field) == current_value
                        )
                        may_refresh = source_verified and primary_google_account and (legacy_exact or source_value_matches)
                        if proposed and field not in edited and not (field == "company" and existing.organization_id) and (not current_value or may_refresh):
                            setattr(existing, attribute, proposed)
                    if "emails" not in edited:
                        existing.emails = list(dict.fromkeys(list(existing.emails or []) + emails))
                    if "phones" not in edited:
                        existing.phones = list(dict.fromkeys(list(existing.phones or []) + list(fields.get("phones", []))))
                    if resource_name and not existing.google_resource_name:
                        existing.google_resource_name = resource_name
                    await save_contact_link(db, contact=existing, account_id=account_id, fields=fields)
                    await link_company_organization(db, existing, uid)
                    updated_count += 1
                else:
                    contact = Contact(
                        user_id=uid,
                        full_name=raw_name,
                        given_name=fields.get("given_name"),
                        family_name=fields.get("family_name"),
                        emails=emails,
                        phones=fields.get("phones", []),
                        company=fields.get("company"),
                        title=fields.get("title"),
                        source="google",
                        google_resource_name=resource_name,
                        birthday=fields.get("birthday"),
                        avatar_url=fields.get("avatar_url"),
                    )
                    db.add(contact)
                    await db.flush()
                    await save_contact_link(db, contact=contact, account_id=account_id, fields=fields)
                    await link_company_organization(db, contact, uid)
                    created_count += 1
            except Exception as exc:
                logger.exception(
                    "sync_google_contacts: failed to upsert contact",
                    extra={"provider": "google", "user_id": str(uid)},
                )
                name_hint = fields.get("full_name") or ", ".join(fields.get("emails", [])[:1]) or "unknown"
                errors.append(f"{name_hint}: {exc}")

    # Imports are additive. A partial account fetch or empty address book must
    # never archive CRM contacts based on an incomplete provider snapshot.

    if created_count or updated_count:
        score_result = await db.execute(
            select(Contact.id).where(
                Contact.user_id == uid,
                Contact.last_interaction_at.isnot(None),
            )
        )
        for (cid,) in score_result.all():
            try:
                await calculate_score(cid, db)
            except Exception:
                logger.warning(
                    "google_contacts: score recalc failed for contact %s",
                    cid,
                    exc_info=True,
                )

    parts = []
    if created_count:
        parts.append(f"{created_count} new")
    if updated_count:
        parts.append(f"{updated_count} updated")
    if archived_count:
        parts.append(f"{archived_count} archived")
    if errors:
        parts.append(f"{len(errors)} errors")
    body = ", ".join(parts) if parts else "No changes"
    if errors:
        body += "\n\nErrors:\n" + "\n".join(f"- {e}" for e in errors[:20])
    db.add(Notification(
        user_id=uid,
        notification_type="sync",
        title="Google Contacts sync completed",
        body=body,
        link="/contacts?sort=recent",
    ))
    await db.commit()

    return {
        "status": "ok",
        "created": created_count,
        "updated": updated_count,
        "archived": archived_count,
        "errors": len(errors),
    }


async def _sync_google_calendar(db: AsyncSession, uid: uuid.UUID) -> dict:
    """Pull recent calendar events for every Google account belonging to ``uid``."""
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if user is None:
        return {"status": "user_not_found"}

    ga_result = await db.execute(
        select(GoogleAccount).where(GoogleAccount.user_id == uid)
    )
    accounts = list(ga_result.scalars().all())

    if not accounts and not user.google_refresh_token:
        return {"status": "not_connected"}

    cal_result = {"new_contacts": 0, "new_interactions": 0, "events_processed": 0}
    auth_errors: list[str] = []
    if not accounts and user.google_refresh_token:
        try:
            cal_result = await sync_calendar_events(user, db)
        except RefreshError:
            logger.warning(
                "Calendar sync auth expired for %s",
                user.email,
                extra={"provider": "google", "email": user.email},
            )
            auth_errors.append(user.email)
    else:
        for ga in accounts:
            original_token = user.google_refresh_token
            user.google_refresh_token = ga.refresh_token
            try:
                r = await sync_calendar_events(user, db)
                cal_result["new_contacts"] += r.get("new_contacts", 0)
                cal_result["new_interactions"] += r.get("new_interactions", 0)
                cal_result["events_processed"] += r.get("events_processed", 0)
            except RefreshError:
                logger.warning(
                    "Calendar sync auth expired for %s",
                    ga.email,
                    extra={"provider": "google", "email": ga.email},
                )
                auth_errors.append(ga.email)
            except Exception:
                logger.exception(
                    "Calendar sync failed for %s",
                    ga.email,
                    extra={"provider": "google", "email": ga.email},
                )
            finally:
                user.google_refresh_token = original_token

    if auth_errors:
        emails = ", ".join(auth_errors)
        db.add(Notification(
            user_id=uid,
            notification_type="error",
            title="Google Calendar sync failed — re-authentication needed",
            body=f"The OAuth token for {emails} has expired or been revoked. Please reconnect your Google account in Settings.",
            link="/settings",
        ))

    if cal_result.get("new_interactions") or cal_result.get("new_contacts"):
        score_result = await db.execute(
            select(Contact.id).where(
                Contact.user_id == uid,
                Contact.last_interaction_at.isnot(None),
            )
        )
        for (cid,) in score_result.all():
            try:
                await calculate_score(cid, db)
            except Exception:
                logger.warning(
                    "google_calendar: score recalc failed for contact %s",
                    cid,
                    exc_info=True,
                )

    parts = []
    if cal_result.get("new_contacts"):
        parts.append(f"{cal_result['new_contacts']} new contacts")
    if cal_result.get("new_interactions"):
        parts.append(f"{cal_result['new_interactions']} meetings")
    db.add(Notification(
        user_id=uid,
        notification_type="sync",
        title="Google Calendar sync completed",
        body=", ".join(parts) if parts else "No new events",
        link="/contacts?sort=recent",
    ))

    # Auto-merge deterministic duplicates (same email/phone) across sources.
    # Mirrors the Gmail sync path so calendar-sourced contacts don't linger as
    # duplicates of contacts created by LinkedIn/manual entry.
    if cal_result.get("new_contacts"):
        try:
            from app.services.identity_resolution import find_deterministic_matches
            merged = await find_deterministic_matches(uid, db)
            if merged:
                logger.info(
                    "google_calendar sync: auto-merged %d duplicate(s) for user %s",
                    len(merged), uid,
                )
        except Exception:
            logger.warning(
                "google_calendar sync: auto-merge failed for user %s", uid, exc_info=True
            )

    await db.commit()

    return {"status": "ok", **cal_result}


async def _collect_google_calendar_user_ids(db: AsyncSession) -> list[str]:
    """User IDs eligible for the daily ``sync_google_calendar_all`` beat task."""
    result = await db.execute(
        select(User.id).where(
            User.google_refresh_token.isnot(None)
            | select(GoogleAccount.id).where(GoogleAccount.user_id == User.id).exists()
        )
    )
    return [str(row) for row in result.scalars().all()]


@shared_task(name="app.services.tasks.sync_google_contacts_for_user", bind=True, max_retries=3)
def sync_google_contacts_for_user(self, user_id: str) -> dict:
    """Sync Google Contacts for a single user in background."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return {"status": "invalid_user_id"}

    async def _runner() -> dict:
        async with task_session() as db:
            return await _sync_google_contacts(db, uid)

    try:
        return _run(_runner())
    except Exception as exc:
        logger.exception("sync_google_contacts_for_user failed for %s, retrying.", user_id)
        if self.request.retries >= self.max_retries:
            notify_sync_failure.delay(str(uid), "Google Contacts", str(exc))
        raise self.retry(exc=exc, countdown=60) from exc


@shared_task(name="app.services.tasks.sync_google_calendar_for_user", bind=True, max_retries=3)
def sync_google_calendar_for_user(self, user_id: str) -> dict:
    """Sync Google Calendar events for a single user in background."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return {"status": "invalid_user_id"}

    async def _runner() -> dict:
        async with task_session() as db:
            return await _sync_google_calendar(db, uid)

    try:
        return _run(_runner())
    except Exception as exc:
        logger.exception("sync_google_calendar_for_user failed for %s, retrying.", user_id)
        if self.request.retries >= self.max_retries:
            notify_sync_failure.delay(str(uid), "Google Calendar", str(exc))
        raise self.retry(exc=exc, countdown=60) from exc


@shared_task(name="app.services.tasks.sync_google_calendar_all")
def sync_google_calendar_all() -> dict:
    """Beat-scheduled task: enqueue a ``sync_google_calendar_for_user`` task for every
    user that has a google_refresh_token set.

    Returns:
        A dict with ``queued`` count.
    """
    async def _runner() -> list[str]:
        async with task_session() as db:
            return await _collect_google_calendar_user_ids(db)

    user_ids = _run(_runner())
    for uid in user_ids:
        sync_google_calendar_for_user.delay(uid)

    logger.info("sync_google_calendar_all: queued %d user(s).", len(user_ids))
    return {"queued": len(user_ids)}
