#!/usr/bin/env python3
"""
Fix team budget gates in LiteLLM for existing PERIODIC teams.

Background:
- PERIODIC teams were bootstrapped with max_budget=0.0 in LiteLLM
- This blocked all requests because LiteLLM enforces team-level budgets
  as independent gates alongside per-key budgets
- The fix: set team max_budget to None (no gate) for PERIODIC teams
- POOL teams keep their 0.0 budget (raised by purchases)

Usage:
    python scripts/fix_periodic_team_budgets.py [--dry-run]
"""

import os
import sys
import asyncio
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.database import SessionLocal
from app.db.models import DBTeam, DBRegion
from app.schemas.models import BudgetType
from app.services.litellm import LiteLLMService


async def fix_team_budget(team: DBTeam, region: DBRegion, dry_run: bool) -> dict:
    """Remove team-level budget gate for a single team/region pair."""
    litellm_service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team.id)

    if dry_run:
        return {
            "team_id": team.id,
            "team_name": team.name,
            "budget_type": team.budget_type,
            "region": region.name,
            "lite_team_id": lite_team_id,
            "action": "would_remove_budget_gate",
            "success": True,
            "error": None,
        }

    try:
        await litellm_service.update_team_budget(
            team_id=lite_team_id,
            max_budget=None,
        )
        return {
            "team_id": team.id,
            "team_name": team.name,
            "budget_type": team.budget_type,
            "region": region.name,
            "lite_team_id": lite_team_id,
            "action": "removed_budget_gate",
            "success": True,
            "error": None,
        }
    except Exception as e:
        return {
            "team_id": team.id,
            "team_name": team.name,
            "budget_type": team.budget_type,
            "region": region.name,
            "lite_team_id": lite_team_id,
            "action": "failed",
            "success": False,
            "error": str(e),
        }


async def main(dry_run: bool = False):
    session = SessionLocal()
    try:
        periodic_teams = (
            session.query(DBTeam)
            .filter(
                DBTeam.budget_type == BudgetType.PERIODIC,
                DBTeam.deleted_at.is_(None),
            )
            .all()
        )
        active_regions = (
            session.query(DBRegion).filter(DBRegion.is_active.is_(True)).all()
        )

        if not periodic_teams:
            print("No PERIODIC teams found.")
            return

        if not active_regions:
            print("No active regions found.")
            return

        mode = "DRY RUN" if dry_run else "LIVE"
        print(
            f"[{mode}] Fixing budget gates for {len(periodic_teams)} PERIODIC teams "
            f"across {len(active_regions)} regions ({len(periodic_teams) * len(active_regions)} operations)"
        )
        print()

        results = []
        for team in periodic_teams:
            for region in active_regions:
                result = await fix_team_budget(team, region, dry_run)
                results.append(result)
                status = "OK" if result["success"] else "FAIL"
                print(
                    f"  [{status}] Team {result['team_id']} ({result['team_name']}) "
                    f"-> Region {result['region']}: {result['action']}"
                    + (f" - {result['error']}" if result["error"] else "")
                )

        print()
        successes = sum(1 for r in results if r["success"])
        failures = sum(1 for r in results if not r["success"])
        print(f"Done: {successes} succeeded, {failures} failed")

        if dry_run:
            print("\nThis was a dry run. Run without --dry-run to apply changes.")

    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fix LiteLLM team budget gates for existing PERIODIC teams"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run))
