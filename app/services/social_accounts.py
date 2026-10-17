"""Create and update social_accounts rows. Tokens never live here."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.social_account import SocialAccount
from app.schemas.clip import SocialAccountCreate, SocialAccountUpdate


class SocialAccountConflict(ValueError):
    """(platform, account_key) is already registered."""


class SocialAccountInvalid(ValueError):
    """Payload failed validation outside the request model (script / DB check)."""


def get_social_account(db: Session, platform: str, account_key: str) -> SocialAccount | None:
    return db.execute(
        select(SocialAccount).where(
            SocialAccount.platform == platform,
            SocialAccount.account_key == account_key,
        )
    ).scalar_one_or_none()


def _from_integrity(exc: IntegrityError) -> Exception:
    raw = str(getattr(exc, "orig", "") or "").lower()
    if "ck_social_accounts" in raw or "check constraint" in raw:
        return SocialAccountInvalid("invalid platform, status or auth_kind")
    return SocialAccountConflict("social account already exists")


def create_social_account(db: Session, payload: SocialAccountCreate) -> SocialAccount:
    if get_social_account(db, payload.platform, payload.account_key) is not None:
        raise SocialAccountConflict(
            f"social account {payload.platform}/{payload.account_key} already exists"
        )
    row = SocialAccount(
        platform=payload.platform,
        account_key=payload.account_key,
        handle=payload.handle,
        status=payload.status,
        auth_kind=payload.auth_kind,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _from_integrity(exc) from exc
    db.refresh(row)
    return row


def update_social_account(
    db: Session,
    platform: str,
    account_key: str,
    payload: SocialAccountUpdate,
) -> SocialAccount | None:
    row = get_social_account(db, platform, account_key)
    if row is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    if not data:
        return row
    for key, value in data.items():
        setattr(row, key, value)
    row.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _from_integrity(exc) from exc
    db.refresh(row)
    return row


def format_validation_error(exc: ValidationError) -> SocialAccountInvalid:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err.get("loc", ())) or "body"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return SocialAccountInvalid("; ".join(parts) or "invalid social account")


def upsert_social_account(
    db: Session,
    *,
    platform: str,
    account_key: str = "default",
    handle: str | None = None,
    status: str | None = None,
    auth_kind: str | None = None,
    handle_set: bool = False,
) -> tuple[SocialAccount, str]:
    """Insert or update one account. Returns (row, created|updated|unchanged)."""
    existing = get_social_account(db, platform, account_key or "default")
    if existing is None:
        body: dict = {"platform": platform, "account_key": account_key or "default"}
        if handle_set:
            body["handle"] = handle
        if status is not None:
            body["status"] = status
        if auth_kind is not None:
            body["auth_kind"] = auth_kind
        try:
            payload = SocialAccountCreate.model_validate(body)
        except ValidationError as exc:
            raise format_validation_error(exc) from exc
        return create_social_account(db, payload), "created"

    data: dict = {}
    if handle_set:
        data["handle"] = handle
    if status is not None:
        data["status"] = status
    if auth_kind is not None:
        data["auth_kind"] = auth_kind
    if not data:
        return existing, "unchanged"
    try:
        payload = SocialAccountUpdate.model_validate(data)
    except ValidationError as exc:
        raise format_validation_error(exc) from exc
    row = update_social_account(db, existing.platform, existing.account_key, payload)
    if row is None:
        raise SocialAccountConflict("social account disappeared during update")
    return row, "updated"
