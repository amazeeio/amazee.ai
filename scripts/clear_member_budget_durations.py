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

from app.core.worker import reanchor_member_caps
from app.db.database import SessionLocal
from app.db.models import DBRegion, DBSpendCap, DBUser
from app.services.litellm import LiteLLMService, membership_spend_by_user


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
            if not region:
                failed += len(rows)
                print(
                    f"[FAIL] team_id={team_id} region_id={region_id} region not found"
                )
                continue
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

            member_spend = membership_spend_by_user(team_info)

            pushable = 0
            for row in rows:
                scanned += 1
                member_key = str(row.user_id)
                user = session.query(DBUser).filter(DBUser.id == row.user_id).first()
                if member_key not in member_spend or not user:
                    skipped += 1
                    print(
                        f"[SKIP] user_id={row.user_id} team_id={team_id} "
                        f"region={region.name} no LiteLLM membership"
                    )
                    continue
                pushable += 1
                spend = member_spend[member_key]
                print(
                    f"user_id={row.user_id} team_id={team_id} region={region.name} "
                    f"budget_duration={row.budget_duration} spend={spend} "
                    f"cap={row.max_budget} -> "
                    f"max_budget_in_team={spend + float(row.max_budget)} "
                    "budget_duration=null"
                )

            if not apply:
                continue
            errors = await reanchor_member_caps(
                db=session,
                litellm_service=service,
                region=region,
                team_id=team_id,
                lite_team_id=lite_team_id,
                team_info=team_info,
            )
            for error in errors:
                print(f"[FAIL] team_id={team_id} region={region.name} {error}")
            failed += len(errors)
            cleared += max(pushable - len(errors), 0)

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
