#!/usr/bin/env python3
"""
One-time backfill: scope existing anonymous-trial keys to inference routes.

All anonymous trials share one team so their keys stay trackable, but LiteLLM
treats a key's team_id as team membership: a trial key can call
`/team/info?team_id=<shared team>` and read every sibling trial's owner, spend
and budget metadata. New keys are created with `allowed_routes` (see
`create_llm_token`); this script applies the same scoping to keys minted before
that.

Usage:
    python scripts/restrict_trial_key_routes.py --dry-run
    python scripts/restrict_trial_key_routes.py
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.team_service import is_anonymous_trial_team
from app.db.database import SessionLocal
from app.db.models import DBPrivateAIKey, DBRegion, DBTeam, DBUser
from app.services.litellm import INFERENCE_ONLY_ROUTES, LiteLLMService


def get_trial_keys_grouped_by_region(session):
    """Return trial-team LiteLLM keys grouped by region_id."""
    trial_team_ids = [
        team.id for team in session.query(DBTeam).all() if is_anonymous_trial_team(team)
    ]
    if not trial_team_ids:
        return defaultdict(list)

    keys = (
        session.query(DBPrivateAIKey)
        .outerjoin(DBUser, DBUser.id == DBPrivateAIKey.owner_id)
        .filter(DBPrivateAIKey.litellm_token.isnot(None))
        .filter(DBPrivateAIKey.region_id.isnot(None))
        .filter(
            (DBPrivateAIKey.team_id.in_(trial_team_ids))
            | (DBUser.team_id.in_(trial_team_ids))
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
        keys_by_region = get_trial_keys_grouped_by_region(session)

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
                if dry_run:
                    print(
                        f"[DRY-RUN] key_id={key.id} region={region.name} -> "
                        f"allowed_routes={INFERENCE_ONLY_ROUTES}"
                    )
                    continue
                try:
                    await service.set_key_allowed_routes(
                        litellm_token=key.litellm_token,
                        allowed_routes=INFERENCE_ONLY_ROUTES,
                    )
                    total_updated += 1
                    print(f"[OK] key_id={key.id} region={region.name}")
                except Exception as e:
                    total_failed += 1
                    print(f"[FAIL] key_id={key.id} region={region.name} error={e}")

        print(f"Done. updated={total_updated} failed={total_failed} dry_run={dry_run}")
        return 0 if total_failed == 0 else 1
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Restrict existing anonymous-trial LiteLLM keys to inference routes"
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
