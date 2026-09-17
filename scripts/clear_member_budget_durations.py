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
        # Only a row we pushed loses its duration; a skipped row keeps it as
        # the marker that the next run must list it again.
        cleared_row_ids: set[int] = set()

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

            listed_user_ids: set[int] = set()
            listed_row_ids: set[int] = set()
            for row in rows:
                scanned += 1
                member_key = str(row.user_id)
                user = session.query(DBUser).filter(DBUser.id == row.user_id).first()
                if not user or user.team_id != team_id:
                    skipped += 1
                    reason = (
                        "user row is gone" if not user else "user no longer in the team"
                    )
                    print(
                        f"[SKIP] user_id={row.user_id} team_id={team_id} "
                        f"region={region.name} {reason}"
                    )
                    continue
                listed_user_ids.add(row.user_id)
                listed_row_ids.add(row.id)
                # LiteLLM writes the membership row on the first budget push,
                # so a member without one starts the cycle at spend 0.0.
                spend = member_spend.get(member_key, 0.0)
                marker = "" if member_key in member_spend else " (no membership row)"
                print(
                    f"user_id={row.user_id} team_id={team_id} region={region.name} "
                    f"budget_duration={row.budget_duration} spend={spend} "
                    f"cap={row.max_budget} -> "
                    f"max_budget_in_team={spend + float(row.max_budget)} "
                    f"budget_duration=null{marker}"
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
                user_ids=listed_user_ids,
            )
            for error in errors:
                print(f"[FAIL] team_id={team_id} region={region.name} {error}")
            failed += len(errors)
            cleared += max(len(listed_user_ids) - len(errors), 0)
            if not errors:
                cleared_row_ids |= listed_row_ids

        # A failed read or write leaves that member's cycle live in LiteLLM.
        # Nulling the rows anyway would make the cleanup look finished.
        if failed:
            print(
                f"Skipping the spend_caps cleanup: {failed} failure(s). "
                f"{cap_rows.count()} row(s) still hold a duration; re-run once "
                "the failures are resolved."
            )
        elif apply and cleared_row_ids:
            session.query(DBSpendCap).filter(DBSpendCap.id.in_(cleared_row_ids)).update(
                {"budget_duration": None}, synchronize_session=False
            )
            session.commit()

        print(
            f"Done. scanned={scanned} cleared={cleared} skipped={skipped} "
            f"failed={failed} left={cap_rows.count()} apply={apply}"
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
