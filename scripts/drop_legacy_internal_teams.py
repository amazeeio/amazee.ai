#!/usr/bin/env python3
"""
Drop legacy internal teams from the amazee.ai database.

Identifies amazee-internal teams in the legacy dashboard that are no longer
needed (replaced by the Moad internal workspace) and soft-deletes them.

Soft-delete is reversible:
  - Sets deleted_at timestamp
  - Deactivates all users
  - Expires all keys in LiteLLM
  - Teams are hard-deleted automatically after 60 days by the retention job

Usage:
    # Dry run — list internal teams and their status (default)
    python scripts/drop_legacy_internal_teams.py

    # Actually soft-delete the identified teams
    python scripts/drop_legacy_internal_teams.py --execute

    # Include teams matching a custom email pattern
    python scripts/drop_legacy_internal_teams.py --email-pattern @amazee.io

    # Target specific team IDs only
    python scripts/drop_legacy_internal_teams.py --team-ids 12 34 56 --execute

See: https://github.com/amazeeio/moad/issues/350
"""

import argparse
import asyncio
import logging
import os
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from app.db.database import engine
from app.db.models import (
    DBPrivateAIKey,
    DBProduct,
    DBTeam,
    DBTeamProduct,
    DBUser,
)
from app.core.team_service import soft_delete_team

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Default email pattern for identifying internal teams
DEFAULT_EMAIL_PATTERN = "@amazee.io"


def find_internal_teams(
    db: Session,
    email_pattern: str = DEFAULT_EMAIL_PATTERN,
    team_ids: list[int] | None = None,
) -> list[DBTeam]:
    """
    Find internal teams by admin email pattern or explicit IDs.

    Only returns teams that are not already soft-deleted.
    """
    query = db.query(DBTeam).filter(DBTeam.deleted_at.is_(None))

    if team_ids:
        query = query.filter(DBTeam.id.in_(team_ids))
    else:
        escaped = email_pattern.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace(".", "\\.")
        query = query.filter(
            func.lower(DBTeam.admin_email).like(f"%{escaped}%", escape="\\")
        )

    return query.order_by(DBTeam.id).all()


def get_team_summary(db: Session, team: DBTeam) -> dict:
    """Build an activity summary for a single team."""
    user_count = db.query(DBUser).filter(DBUser.team_id == team.id).count()

    # Collect keys owned by users in the team (user-scoped keys)
    team_user_ids = [
        uid
        for (uid,) in db.query(DBUser.id).filter(DBUser.team_id == team.id).all()
    ]

    # Use OR (not addition) to avoid double-counting keys that carry both
    # team_id and owner_id belonging to this team.
    key_filter = DBPrivateAIKey.team_id == team.id
    if team_user_ids:
        key_filter = key_filter | DBPrivateAIKey.owner_id.in_(team_user_ids)

    total_keys = db.query(DBPrivateAIKey).filter(key_filter).count()

    products = (
        db.query(DBTeamProduct).filter(DBTeamProduct.team_id == team.id).all()
    )
    product_ids = [p.product_id for p in products]

    # Find most recent key activity
    latest_key = (
        db.query(DBPrivateAIKey)
        .filter(
            (DBPrivateAIKey.team_id == team.id)
            | (DBPrivateAIKey.owner_id.in_(team_user_ids) if team_user_ids else False)
        )
        .order_by(DBPrivateAIKey.updated_at.desc().nullslast())
        .first()
    )
    last_key_activity = latest_key.updated_at if latest_key else None

    return {
        "id": team.id,
        "name": team.name,
        "admin_email": team.admin_email,
        "created_at": team.created_at,
        "is_always_free": team.is_always_free,
        "budget_type": str(team.budget_type),
        "stripe_customer_id": team.stripe_customer_id,
        "user_count": user_count,
        "total_keys": total_keys,
        "product_ids": product_ids,
        "last_key_activity": last_key_activity,
        "last_payment": team.last_payment,
    }


def print_team_table(summaries: list[dict]) -> None:
    """Print a human-readable summary table."""
    if not summaries:
        print("\n  No matching teams found.\n")
        return

    print(f"\n{'='*100}")
    print(f"  Found {len(summaries)} internal team(s)\n")

    for s in summaries:
        has_products = "⚠️  HAS PRODUCTS" if s["product_ids"] else "no products"
        has_stripe = (
            f"stripe: {s['stripe_customer_id']}" if s["stripe_customer_id"] else "no stripe"
        )
        last_activity = (
            s["last_key_activity"].strftime("%Y-%m-%d")
            if s["last_key_activity"]
            else "never"
        )

        print(f"  [{s['id']:>4}] {s['name']}")
        print(f"         email: {s['admin_email']}")
        print(
            f"         users: {s['user_count']}  |  keys: {s['total_keys']}  |  {has_products}  |  {has_stripe}"
        )
        print(
            f"         created: {s['created_at'].strftime('%Y-%m-%d') if s['created_at'] else '?'}  |  last key activity: {last_activity}  |  budget: {s['budget_type']}"
        )
        print()

    print(f"{'='*100}\n")


async def drop_teams(db: Session, teams: list[DBTeam], dry_run: bool = True) -> None:
    """Soft-delete the given teams (or just print what would happen)."""
    if dry_run:
        print("  🔍 DRY RUN — no changes will be made.")
        print("     Re-run with --execute to soft-delete these teams.\n")
        return

    # Safety check: refuse to delete teams with active Stripe products
    teams_with_products = []
    for team in teams:
        active_product_count = (
            db.query(DBTeamProduct)
            .join(DBProduct)
            .filter(DBTeamProduct.team_id == team.id, DBProduct.active.is_(True))
            .count()
        )
        if active_product_count > 0:
            teams_with_products.append(team)

    if teams_with_products:
        print("  ❌ Cannot proceed — the following teams have active products:\n")
        for t in teams_with_products:
            print(f"     [{t.id}] {t.name}")
        print(
            "\n     Remove their product associations first, or exclude them with --team-ids.\n"
        )
        sys.exit(1)

    print(f"  🗑️  Soft-deleting {len(teams)} team(s)...\n")

    succeeded = []
    failed = []

    for team in teams:
        try:
            await soft_delete_team(db, team)
            succeeded.append(team)
            print(f"     ✅ [{team.id}] {team.name} — soft-deleted")
        except Exception as e:
            failed.append((team, str(e)))
            print(f"     ❌ [{team.id}] {team.name} — FAILED: {e}")

    print(f"\n  Done. {len(succeeded)} succeeded, {len(failed)} failed.\n")

    if failed:
        sys.exit(1)

    if succeeded:
        print("  📧 Next step: notify affected users to sign in to Moad with their")
        print("     amazee email to access the internal workspace and get new keys.\n")


async def main():
    parser = argparse.ArgumentParser(
        description="Drop legacy internal teams from amazee.ai (soft-delete)."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform the soft-delete. Without this flag, runs in dry-run mode.",
    )
    parser.add_argument(
        "--email-pattern",
        default=DEFAULT_EMAIL_PATTERN,
        help=f"Email pattern to match internal teams (default: {DEFAULT_EMAIL_PATTERN})",
    )
    parser.add_argument(
        "--team-ids",
        nargs="+",
        type=int,
        help="Target specific team IDs instead of matching by email pattern.",
    )
    args = parser.parse_args()

    if args.team_ids and args.email_pattern != DEFAULT_EMAIL_PATTERN:
        logger.warning(
            "--email-pattern is ignored when --team-ids is provided"
        )

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        teams = find_internal_teams(
            db,
            email_pattern=args.email_pattern,
            team_ids=args.team_ids,
        )

        summaries = [get_team_summary(db, team) for team in teams]
        print_team_table(summaries)

        if not teams:
            return

        await drop_teams(db, teams, dry_run=not args.execute)

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
