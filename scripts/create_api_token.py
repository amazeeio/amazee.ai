#!/usr/bin/env python3
"""Create an API token for a local user for use with curl or scripts."""

from __future__ import annotations

import argparse
import secrets
import sys

from sqlalchemy import func

from app.db.database import SessionLocal
from app.db.models import DBAPIToken, DBUser


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an API token in the local database. "
        "The script loads the same .env file as the backend."
    )
    parser.add_argument("--name", required=True, help="Friendly name for the token.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user-id", type=int, help="ID of the user who owns this token.")
    group.add_argument("--email", help="Email of the user who owns this token.")
    parser.add_argument(
        "--token",
        help="Optional token value to persist. Defaults to a randomly generated URL-safe string.",
    )
    return parser.parse_args()


def find_user(db, user_id: int | None, email: str | None) -> DBUser | None:
    if user_id is not None:
        return db.query(DBUser).filter(DBUser.id == user_id).first()
    if email is not None:
        return (
            db.query(DBUser)
            .filter(func.lower(DBUser.email) == email.lower())
            .first()
        )
    return None


def create_token(db, name: str, user: DBUser, token_value: str) -> DBAPIToken:
    existing = db.query(DBAPIToken).filter(DBAPIToken.token == token_value).first()
    if existing:
        raise ValueError("Token value already exists for another record.")

    db_token = DBAPIToken(name=name, token=token_value, user_id=user.id)
    db.add(db_token)
    db.commit()
    db.refresh(db_token)
    return db_token


def main() -> None:
    args = parse_args()
    token_value = args.token or secrets.token_urlsafe(32)

    db = SessionLocal()
    owner_email: str | None = None
    try:
        user = find_user(db, args.user_id, args.email)
        if not user:
            sys.exit("User not found. Provide an existing user via --user-id or --email.")

        owner_email = user.email
        token = create_token(db, args.name, user, token_value)
    except Exception as exc:  # pragma: no cover - utility script
        db.rollback()
        sys.exit(f"Failed to create token: {exc}")
    finally:
        db.close()

    print("✅ API token created")
    print(f"  Name:        {token.name}")
    print(f"  Token:       {token.token}")
    print(f"  User ID:     {token.user_id}")
    print(f"  Owner email: {owner_email}")


if __name__ == "__main__":
    main()
