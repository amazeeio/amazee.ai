#!/usr/bin/env python3
"""
One-time migration: clear per-key max_budget for POOL teams in LiteLLM.

This script sets key max_budget to null and keeps budget_duration aligned to
the configured POOL period so POOL teams are enforced by team budget only.

Usage:
    python scripts/clear_pool_key_budgets.py --dry-run
    python scripts/clear_pool_key_budgets.py
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models import DBPrivateAIKey, DBTeam, DBUser, DBRegion
from app.schemas.models import BudgetType
from app.services.litellm import LiteLLMService


def get_pool_keys_grouped_by_region(session):
    """Return POOL-team keys grouped by region_id."""
    pool_team_ids = {
        team_id
        for (team_id,) in session.query(DBTeam.id)
        .filter(DBTeam.budget_type == BudgetType.POOL)
        .all()
    }
    if not pool_team_ids:
        return defaultdict(list)

    keys = (
        session.query(DBPrivateAIKey)
        .outerjoin(DBUser, DBUser.id == DBPrivateAIKey.owner_id)
        .filter(DBPrivateAIKey.litellm_token.isnot(None))
        .filter(DBPrivateAIKey.region_id.isnot(None))
        .filter(
            (DBPrivateAIKey.team_id.in_(pool_team_ids))
            | (DBPrivateAIKey.team_id.is_(None) & DBUser.team_id.in_(pool_team_ids))
        )
        .all()
    )

    keys_by_region = defaultdict(list)
    for key in keys:
        keys_by_region[key.region_id].append(key)
    return keys_by_region


async def run(dry_run: bool) -> int:
    session = SessionLocal()
    try:
        regions = {r.id: r for r in session.query(DBRegion).all()}
        keys_by_region = get_pool_keys_grouped_by_region(session)

        total_scanned = 0
        total_updated = 0
        total_failed = 0

        for region_id, keys in keys_by_region.items():
            region = regions.get(region_id)
            if region is None:
                continue

            service = LiteLLMService(
                api_url=region.litellm_api_url,
                api_key=region.litellm_api_key,
            )
            for key in keys:
                total_scanned += 1
                if dry_run:
                    print(
                        f"[DRY-RUN] key_id={key.id} region={region.name} -> max_budget=null"
                    )
                    continue

                try:
                    await service.update_budget(
                        litellm_token=key.litellm_token,
                        budget_duration=f"{settings.POOL_PURCHASE_EXPIRY_DAYS}d",
                        budget_amount=None,
                        include_max_budget=True,
                    )
                    total_updated += 1
                    print(f"[OK] key_id={key.id} region={region.name}")
                except Exception as e:
                    total_failed += 1
                    print(f"[FAIL] key_id={key.id} region={region.name} error={e}")

        print(
            f"Done. scanned={total_scanned} updated={total_updated} failed={total_failed} dry_run={dry_run}"
        )
        return 0 if total_failed == 0 else 1
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Clear per-key max_budget for POOL team keys in LiteLLM"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned updates without applying them",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.dry_run)))


if __name__ == "__main__":
    main()
