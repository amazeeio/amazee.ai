#!/usr/bin/env python3
"""
One-off cleanup: put back the purchase gate on keys whose budget was raised.

Before the set endpoint learned to respect the gate, writing a key cap on a
purchase-gated team pushed that cap to LiteLLM. The key went from max_budget=0
to the cap, and since LiteLLM denies a team only above its budget, one request
got through unpaid.

This resets those keys to max_budget=0 and restores the gate duration. The
spend_caps row is left alone, so the cap still applies on the first purchase.

Usage:
    python scripts/regate_unpurchased_key_budgets.py
    python scripts/regate_unpurchased_key_budgets.py --apply
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import func

from app.core.config import settings
from app.core.pool_budget_service import pool_team_has_ever_purchased
from app.db.database import SessionLocal
from app.db.models import DBPrivateAIKey, DBRegion, DBSpendCap, DBTeam, DBUser
from app.services.litellm import LiteLLMService


def _raised_gated_keys(session):
    """Keys with a positive cap whose team is gated and has never purchased.

    A user-scoped key stores a null team_id on its cap row and names the owner
    instead, so the team is resolved the way _key_gate_locked does: the cap
    row's own team, else the owner's team. Joining on team_id alone would skip
    every user-scoped key and leave its raised budget in place.
    """
    rows = (
        session.query(
            DBSpendCap.key_id,
            DBSpendCap.region_id,
            func.coalesce(DBSpendCap.team_id, DBUser.team_id).label("team_id"),
        )
        .outerjoin(DBUser, DBUser.id == DBSpendCap.user_id)
        .filter(
            DBSpendCap.scope == "key",
            DBSpendCap.key_id.isnot(None),
            DBSpendCap.max_budget.isnot(None),
            DBSpendCap.max_budget > 0,
        )
        .all()
    )
    # The gate and purchase checks are per team and region, so ask once per pair.
    checked: dict[tuple[int, int], bool] = {}
    for key_id, region_id, team_id in rows:
        if team_id is None:
            continue
        pair = (team_id, region_id)
        if pair not in checked:
            team = session.query(DBTeam).filter(DBTeam.id == team_id).first()
            # requires_pool_purchase_gate, not the raw flag: that flag defaults
            # to true on every team, so a PERIODIC team would be selected here
            # and have a valid key zeroed.
            checked[pair] = bool(
                team is not None
                and team.requires_pool_purchase_gate
                and not pool_team_has_ever_purchased(session, team_id, region_id)
            )
        if checked[pair]:
            yield key_id, region_id, team_id


async def run(apply: bool) -> int:
    session = SessionLocal()
    try:
        found = 0
        regated = 0
        failed = 0
        services: dict[int, LiteLLMService] = {}

        for key_id, region_id, team_id in _raised_gated_keys(session):
            key = (
                session.query(DBPrivateAIKey)
                .filter(DBPrivateAIKey.id == key_id)
                .first()
            )
            if key is None or key.litellm_token is None:
                continue
            found += 1

            if region_id not in services:
                region = (
                    session.query(DBRegion).filter(DBRegion.id == region_id).first()
                )
                if region is None:
                    failed += 1
                    print(f"[FAIL] key_id={key_id} region={region_id} not found")
                    continue
                services[region_id] = LiteLLMService(
                    api_url=region.litellm_api_url,
                    api_key=region.litellm_api_key,
                )
            service = services[region_id]

            print(
                f"key_id={key_id} region={region_id} team_id={team_id} "
                "max_budget -> 0.0 (cap row kept)"
            )
            if not apply:
                continue
            try:
                await service.update_key_budget(
                    litellm_token=key.litellm_token,
                    budget_duration=f"{settings.POOL_PURCHASE_EXPIRY_DAYS}d",
                    max_budget=0.0,
                    clear_max_budget=False,
                )
                regated += 1
            except Exception as exc:
                failed += 1
                print(f"[FAIL] key_id={key_id} region={region_id} error={exc}")

        print(
            f"Done. found={found} regated={regated} failed={failed} apply={apply}"
        )
        return 0 if failed == 0 else 1
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Reset raised key budgets on gated teams that never purchased"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes; without it the script only prints them",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.apply)))


if __name__ == "__main__":
    main()
