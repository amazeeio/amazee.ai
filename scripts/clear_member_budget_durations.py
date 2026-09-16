#!/usr/bin/env python3
"""
One-off cleanup: clear the LiteLLM ``budget_duration`` on every team-member
cap and push the current ceiling (membership spend + cap) at the same time.

The billing cycle owns the member cap period now. Without this, a member that
already hit the cap this month stays blocked until the next cycle.

Usage:
    python scripts/clear_member_budget_durations.py
    python scripts/clear_member_budget_durations.py --apply
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.litellm_user_sync import team_role_for_litellm
from app.db.database import SessionLocal
from app.db.models import DBRegion, DBSpendCap, DBUser
from app.services.litellm import LiteLLMService


async def run(apply: bool) -> int:
    session = SessionLocal()
    try:
        scanned = 0
        cleared = 0
        skipped = 0
        failed = 0

        cap_rows = session.query(DBSpendCap).filter(
            DBSpendCap.scope == "team_member",
            DBSpendCap.budget_duration.isnot(None),
            DBSpendCap.max_budget.isnot(None),
        )
        groups: dict[tuple[int, int], list[DBSpendCap]] = defaultdict(list)
        for row in cap_rows.order_by(DBSpendCap.region_id, DBSpendCap.team_id).all():
            groups[(row.region_id, row.team_id)].append(row)

        for (region_id, team_id), rows in groups.items():
            region = session.query(DBRegion).filter(DBRegion.id == region_id).first()
            service = LiteLLMService(
                api_url=region.litellm_api_url,
                api_key=region.litellm_api_key,
            )
            lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
            try:
                team_info = await service.get_team_info(lite_team_id)
            except Exception as exc:
                failed += len(rows)
                print(
                    f"[FAIL] team_id={team_id} region={region.name} "
                    f"could not read team info: {exc}"
                )
                continue

            member_spend = {
                str(membership["user_id"]): float(membership.get("spend") or 0.0)
                for membership in (team_info.get("team_memberships") or [])
                if membership.get("user_id") is not None
            }

            for row in rows:
                scanned += 1
                user = session.query(DBUser).filter(DBUser.id == row.user_id).first()
                if str(row.user_id) not in member_spend or not user:
                    skipped += 1
                    print(
                        f"[SKIP] user_id={row.user_id} team_id={team_id} "
                        f"region={region.name} no LiteLLM membership"
                    )
                    continue

                spend = member_spend[str(row.user_id)]
                ceiling = spend + float(row.max_budget)
                print(
                    f"user_id={row.user_id} team_id={team_id} region={region.name} "
                    f"budget_duration={row.budget_duration} spend={spend} "
                    f"cap={row.max_budget} -> max_budget_in_team={ceiling} "
                    "budget_duration=null"
                )
                if not apply:
                    continue
                try:
                    await service.update_team_member(
                        team_id=lite_team_id,
                        user_id=str(row.user_id),
                        role=team_role_for_litellm(user),
                        max_budget_in_team=ceiling,
                        clear_budget_duration=True,
                    )
                    cleared += 1
                except Exception as exc:
                    failed += 1
                    print(
                        f"[FAIL] user_id={row.user_id} team_id={team_id} "
                        f"region={region.name} error={exc}"
                    )

        cap_count = cap_rows.count()
        # A failed read or write leaves that member's cycle live in LiteLLM.
        # Nulling the rows anyway would make the cleanup look finished.
        if failed:
            print(
                f"Skipping the spend_caps cleanup: {failed} failure(s). "
                f"{cap_count} row(s) still hold a duration; re-run once the "
                "failures are resolved."
            )
        elif apply and cap_count:
            cap_rows.update({"budget_duration": None}, synchronize_session=False)
            session.commit()

        print(
            f"Done. scanned={scanned} cleared={cleared} skipped={skipped} "
            f"failed={failed} apply={apply}"
        )
        return 0 if failed == 0 else 1
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Clear the LiteLLM budget_duration on every team-member cap"
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
