#!/usr/bin/env python3
"""
One-off cleanup: clear the LiteLLM ``budget_duration`` on every key whose
cycle the ledger owns (POOL keys and any key with a cap row).

The hourly reconcile repairs the same drift within an hour. This script lets an
operator do it at once and see the list of keys it touches.

Usage:
    python scripts/clear_key_budget_durations.py
    python scripts/clear_key_budget_durations.py --apply
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.pool_budget_service import team_gate_locked
from app.db.database import SessionLocal
from app.db.models import (
    BudgetType,
    DBPrivateAIKey,
    DBRegion,
    DBSpendCap,
    DBTeam,
    DBUser,
)
from app.services.litellm import LiteLLMService, hash_litellm_token


def _region_keys(session, region_id):
    """Keys in a region with a LiteLLM token, each paired with its team id."""
    rows = (
        session.query(DBPrivateAIKey, DBUser.team_id)
        .outerjoin(DBUser, DBUser.id == DBPrivateAIKey.owner_id)
        .filter(
            DBPrivateAIKey.region_id == region_id,
            DBPrivateAIKey.litellm_token.isnot(None),
        )
        .all()
    )
    return [(key, key.team_id or owner_team_id) for key, owner_team_id in rows]


async def run(apply: bool) -> int:
    session = SessionLocal()
    try:
        scanned = 0
        cleared = 0
        skipped_gated = 0
        failed = 0
        # (skip, is_pool) per team and region, so the DB is asked once per pair.
        team_cache: dict[tuple[int, int], tuple[bool, bool]] = {}

        for region in session.query(DBRegion).all():
            service = LiteLLMService(
                api_url=region.litellm_api_url,
                api_key=region.litellm_api_key,
            )
            try:
                snapshot = await service.list_all_keys()
            except Exception as exc:
                failed += 1
                print(f"[FAIL] region={region.name} could not list keys: {exc}")
                continue

            capped_key_ids = {
                key_id
                for (key_id,) in session.query(DBSpendCap.key_id).filter(
                    DBSpendCap.scope == "key",
                    DBSpendCap.region_id == region.id,
                    DBSpendCap.max_budget.isnot(None),
                )
            }

            for key, team_id in _region_keys(session, region.id):
                scanned += 1
                info = snapshot.get(hash_litellm_token(key.litellm_token)) or {}
                duration = info.get("budget_duration")
                if duration is None:
                    continue

                gate_locked, is_pool = False, False
                if team_id is not None:
                    cache_key = (team_id, region.id)
                    if cache_key not in team_cache:
                        team = (
                            session.query(DBTeam)
                            .filter(DBTeam.id == team_id)
                            .first()
                        )
                        team_cache[cache_key] = (
                            team_gate_locked(session, team, region.id),
                            team is not None and team.budget_type == BudgetType.POOL,
                        )
                    gate_locked, is_pool = team_cache[cache_key]
                if gate_locked:
                    skipped_gated += 1
                    continue
                # Same rule as the hourly reconcile: a PERIODIC key without a
                # cap row still runs on LiteLLM's own cycle, so leave it.
                if not is_pool and key.id not in capped_key_ids:
                    continue

                print(
                    f"key_id={key.id} region={region.name} team_id={team_id} "
                    f"budget_duration={duration} -> null"
                )
                if not apply:
                    continue
                try:
                    await service.update_key_budget(
                        litellm_token=key.litellm_token,
                        budget_duration=None,
                        clear_budget_duration=True,
                    )
                    cleared += 1
                except Exception as exc:
                    failed += 1
                    print(f"[FAIL] key_id={key.id} region={region.name} error={exc}")

        cap_rows = session.query(DBSpendCap).filter(
            DBSpendCap.scope == "key",
            DBSpendCap.budget_duration.isnot(None),
        )
        cap_count = cap_rows.count()
        if apply and cap_count:
            cap_rows.update({"budget_duration": None}, synchronize_session=False)
            session.commit()

        print(
            f"Done. scanned={scanned} cleared={cleared} skipped_gated={skipped_gated} "
            f"spend_caps_rows={cap_count} failed={failed} apply={apply}"
        )
        return 0 if failed == 0 else 1
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Clear the LiteLLM budget_duration on every key"
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
