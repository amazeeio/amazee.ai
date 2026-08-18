"""Revocation of access tokens (JWTs) at logout.

The access token is a self-contained JWT: the backend signs it, hands it to the
client, and keeps no session record. Without a denylist, deleting the cookie at
logout only affects the browser, so a copied token keeps working until its ``exp``
(CWE-613). Every token now carries a unique ``jti``. Logout stores that id in the
``revoked_tokens`` table and the auth path refuses any id it finds there.

Postgres backs the denylist because this backend has no Redis, and the shared
database keeps the answer the same on every pod.

API tokens (the ``api_tokens`` table) are not touched here. They are opaque,
already stored server-side, and are deleted through their own endpoint.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.email import normalize_email_for_lookup
from app.db.models import DBRevokedToken, DBUser

logger = logging.getLogger(__name__)


def is_token_revoked(db: Session, jti: Optional[str]) -> bool:
    """True if this token id is on the denylist."""
    if not jti:
        return False
    return (
        db.query(DBRevokedToken.id).filter(DBRevokedToken.jti == jti).first()
        is not None
    )


def revoke_access_token(db: Session, token: str) -> bool:
    """Add the token's ``jti`` to the denylist. True if it is now revoked.

    The signature is verified but the expiry is not: an already-expired token is
    rejected anyway, so it needs no row. Returns False when the token cannot be
    trusted or carries no ``jti``, so the caller can stay quiet about it — logout
    must not tell an anonymous caller whether a token was genuine.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"verify_exp": False},
        )
    except JWTError:
        return False

    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti or not exp:
        return False

    expires_at = datetime.fromtimestamp(exp, tz=UTC)
    if expires_at <= datetime.now(UTC):
        return True

    if is_token_revoked(db, jti):
        return True

    user_id = _user_id_for_subject(db, payload.get("sub"))
    db.add(DBRevokedToken(jti=jti, user_id=user_id, expires_at=expires_at))
    try:
        db.commit()
    except IntegrityError:
        # Two logouts raced on the same token. The other one won, which is the
        # same outcome we wanted.
        db.rollback()
        return is_token_revoked(db, jti)

    logger.info("Revoked access token jti=%s user_id=%s", jti, user_id)
    return True


def prune_revoked_tokens(db: Session) -> int:
    """Delete denylist rows for tokens that have expired. Returns rows removed.

    An expired token fails validation on its own, so keeping its row only grows
    the table. Runs from the daily cron.
    """
    cutoff = datetime.now(UTC) - timedelta(days=settings.REVOKED_TOKENS_RETENTION_DAYS)
    deleted = (
        db.query(DBRevokedToken)
        .filter(DBRevokedToken.expires_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("Pruned %d revoked_tokens rows expired before %s", deleted, cutoff)
    return deleted


def _user_id_for_subject(db: Session, subject: Optional[str]) -> Optional[int]:
    """Best-effort owner of the token, stored only to make the table readable."""
    if not subject:
        return None
    email = normalize_email_for_lookup(subject)
    row = db.query(DBUser.id).filter(DBUser.email.ilike(email)).first()
    return row[0] if row else None
