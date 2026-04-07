#!/usr/bin/env python3
"""
Sync LiteLLM team budgets with local DB limits for existing PERIODIC teams.

Background:
- PERIODIC teams were bootstrapped with max_budget=0.0 in LiteLLM
- This blocked all requests because LiteLLM enforces team-level budgets
  as independent gates alongside per-key budgets
- The fix: update team max_budget to match the local DB budget limit
- POOL teams are skipped (their budget is managed via purchases)

Usage:
    python scripts/sync_periodic_team_budgets.py [--dry-run]
"""

import os
import sys
import asyncio
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.db.models import DBTeam, DBRegion, DBLimitedResource
from app.schemas.models import BudgetType
from app.schemas.limits import OwnerType, ResourceType
from app.services.litellm import LiteLLMService


def get_team_budget_limit(session: Session, team_id: int) -> float | None:
    """Get the team's budget limit from the local DB."""
    limit = (
        session.query(DBLimitedResource)
        .filter(
            DBLimitedResource.owner_type == OwnerType.TEAM,
            DBLimitedResource.owner_id == team_id,
            DBLimitedResource.resource == ResourceType.BUDGET,
        )
        .first()
    )
    return limit.max_value if limit else None


async def sync_team_budget(
    session: Session, team: DBTeam, region: DBRegion, dry_run: bool
) -> dict:
    """Sync LiteLLM team budget to match the local DB limit."""
    lite_team_id = LiteLLMService.format_team_id(region.name, team.id)

    budget_limit = get_team_budget_limit(session, team.id)
    if budget_limit is None:
        return {
            "team_id": team.id,
            "team_name": team.name,
            "budget_type": team.budget_type,
            "region": region.name,
            "lite_team_id": lite_team_id,
            "action": "skipped",
            "success": True,
            "error": None,
            "detail": "No budget limit found in local DB",
        }

    litellm_service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )

    try:
        team_info = await litellm_service.get_team_info(lite_team_id)
    except Exception as e:
        return {
            "team_id": team.id,
            "team_name": team.name,
            "budget_type": team.budget_type,
            "region": region.name,
            "lite_team_id": lite_team_id,
            "action": "skipped",
            "success": True,
            "error": f"Could not fetch team info from LiteLLM: {e}",
        }

    info = team_info.get("info", {})
    current_max_budget = info.get("max_budget")

    if current_max_budget == budget_limit:
        return {
            "team_id": team.id,
            "team_name": team.name,
            "budget_type": team.budget_type,
            "region": region.name,
            "lite_team_id": lite_team_id,
            "action": "skipped",
            "success": True,
            "error": None,
            "detail": f"Already synced (max_budget={current_max_budget})",
        }

    if dry_run:
        return {
            "team_id": team.id,
            "team_name": team.name,
            "budget_type": team.budget_type,
            "region": region.name,
            "lite_team_id": lite_team_id,
            "action": "would_sync",
            "success": True,
            "error": None,
            "detail": f"{current_max_budget} -> {budget_limit}",
        }

    try:
        await litellm_service.update_team_budget(
            team_id=lite_team_id,
            max_budget=budget_limit,
        )
        return {
            "team_id": team.id,
            "team_name": team.name,
            "budget_type": team.budget_type,
            "region": region.name,
            "lite_team_id": lite_team_id,
            "action": "synced",
            "success": True,
            "error": None,
            "detail": f"{current_max_budget} -> {budget_limit}",
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
            f"[{mode}] Syncing budgets for {len(periodic_teams)} PERIODIC teams "
            f"across {len(active_regions)} regions ({len(periodic_teams) * len(active_regions)} operations)"
        )
        print()

        results = []
        skipped = 0
        for team in periodic_teams:
            for region in active_regions:
                result = await sync_team_budget(session, team, region, dry_run)
                results.append(result)
                if result["action"] == "skipped":
                    skipped += 1
                status = "OK" if result["success"] else "FAIL"
                detail = f" ({result.get('detail')})" if result.get("detail") else ""
                print(
                    f"  [{status}] Team {result['team_id']} ({result['team_name']}) "
                    f"-> Region {result['region']}: {result['action']}{detail}"
                    + (f" - {result['error']}" if result["error"] else "")
                )

        print()
        successes = sum(1 for r in results if r["success"])
        failures = sum(1 for r in results if not r["success"])
        print(f"Done: {successes} succeeded, {failures} failed, {skipped} skipped")

        if dry_run:
            print("\nThis was a dry run. Run without --dry-run to apply changes.")

    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sync LiteLLM team budgets with local DB limits for PERIODIC teams"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run))
