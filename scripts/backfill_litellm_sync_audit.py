#!/usr/bin/env python3
"""
Audit/fix purchase-gated POOL teams with no purchases but non-zero LiteLLM team budget.

Default mode is dry-run (audit only). Use --apply to execute remediations.

What this script checks
-----------------------
- Active teams where:
  - budget_type == pool
  - require_purchase_for_requests == true
- For each target region (active shared + active dedicated associated to the team):
  - Sum purchases in DB for team+region.
  - If purchased total == 0, inspect LiteLLM team max_budget.
  - Flag when LiteLLM team max_budget > 0.

What remediation does (--apply)
-------------------------------
- Set LiteLLM team max_budget=0 with pool expiration duration.
- Lock all team keys in that region at max_budget=0 (monthly duration).
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.core.team_service import get_team_region_litellm_keys
from app.db.database import SessionLocal
from app.db.models import DBPoolPurchase, DBRegion, DBTeam, DBTeamRegion
from app.services.litellm import LiteLLMService


def dedupe_regions(regions: Iterable[DBRegion]) -> list[DBRegion]:
    seen: set[int] = set()
    out: list[DBRegion] = []
    for region in regions:
        if region.id in seen:
            continue
        seen.add(region.id)
        out.append(region)
    return out


def parse_status_from_exc(exc: Exception) -> int | None:
    detail = getattr(exc, "detail", "") or str(exc)
    if "Status 404" in detail:
        return 404
    if "Status 409" in detail:
        return 409
    if "Status 400" in detail:
        return 400
    return None


def to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class Counters:
    teams_processed: int = 0
    regions_checked: int = 0
    flagged: int = 0
    fixed: int = 0
    skipped_missing_team: int = 0
    failed: int = 0

    def as_dict(self) -> dict:
        return {
            "teams_processed": self.teams_processed,
            "regions_checked": self.regions_checked,
            "flagged": self.flagged,
            "fixed": self.fixed,
            "skipped_missing_team": self.skipped_missing_team,
            "failed": self.failed,
        }


class AuditRunner:
    def __init__(
        self,
        session: Session,
        dry_run: bool,
        team_id: int | None,
        region_id: int | None,
        max_rows: int | None,
    ) -> None:
        self.session = session
        self.dry_run = dry_run
        self.only_team_id = team_id
        self.only_region_id = region_id
        self.max_rows = max_rows
        self.findings: list[dict] = []
        self.failures: list[dict] = []

    def _active_shared_regions(self) -> list[DBRegion]:
        query = self.session.query(DBRegion).filter(
            DBRegion.is_active.is_(True), DBRegion.is_dedicated.is_(False)
        )
        if self.only_region_id is not None:
            query = query.filter(DBRegion.id == self.only_region_id)
        return query.all()

    def _active_dedicated_regions_by_team(self) -> dict[int, list[DBRegion]]:
        query = (
            self.session.query(DBTeamRegion, DBRegion)
            .join(DBRegion, DBRegion.id == DBTeamRegion.region_id)
            .filter(DBRegion.is_active.is_(True), DBRegion.is_dedicated.is_(True))
        )
        if self.only_region_id is not None:
            query = query.filter(DBRegion.id == self.only_region_id)
        rows = query.all()
        out: dict[int, list[DBRegion]] = {}
        for assoc, region in rows:
            out.setdefault(assoc.team_id, []).append(region)
        return out

    def _target_regions_for_team(
        self,
        team_id: int,
        shared: list[DBRegion],
        dedicated_by_team: dict[int, list[DBRegion]],
    ) -> list[DBRegion]:
        return dedupe_regions([*shared, *dedicated_by_team.get(team_id, [])])

    def _purchased_total_for_team_region(self, team_id: int, region_id: int) -> float:
        total_purchased_cents = (
            self.session.query(func.sum(DBPoolPurchase.amount_cents))
            .filter(
                DBPoolPurchase.team_id == team_id, DBPoolPurchase.region_id == region_id
            )
            .scalar()
            or 0
        )
        return round(float(total_purchased_cents) / 100.0, 4)

    async def run(self) -> Counters:
        counters = Counters()
        shared = self._active_shared_regions()
        dedicated_by_team = self._active_dedicated_regions_by_team()

        query = self.session.query(DBTeam).filter(
            DBTeam.deleted_at.is_(None),
            DBTeam.is_active.is_(True),
            DBTeam.budget_type == "pool",
            DBTeam.require_purchase_for_requests.is_(True),
        )
        if self.only_team_id is not None:
            query = query.filter(DBTeam.id == self.only_team_id)
        teams = query.order_by(DBTeam.id.asc()).all()

        for team in teams:
            counters.teams_processed += 1
            regions = self._target_regions_for_team(team.id, shared, dedicated_by_team)
            for region in regions:
                counters.regions_checked += 1
                purchased_total = self._purchased_total_for_team_region(
                    team.id, region.id
                )
                if purchased_total > 0:
                    print(
                        f"[audit] team={team.id} region={region.name} purchased_total={purchased_total:.4f} skip"
                    )
                    continue

                service = LiteLLMService(region.litellm_api_url, region.litellm_api_key)
                lite_team_id = LiteLLMService.format_team_id(region.name, team.id)
                try:
                    team_resp = await service.get_team_info(lite_team_id)
                except Exception as exc:
                    if parse_status_from_exc(exc) == 404:
                        counters.skipped_missing_team += 1
                        print(
                            f"[audit] team={team.id} region={region.name} missing_litellm_team skip"
                        )
                        continue
                    counters.failed += 1
                    self.failures.append(
                        {
                            "team_id": team.id,
                            "region_id": region.id,
                            "stage": "get_team_info",
                            "error": str(exc),
                        }
                    )
                    print(
                        f"[audit] team={team.id} region={region.name} FAILED(get_team_info): {exc}"
                    )
                    continue

                team_info = team_resp.get("team_info", team_resp)
                max_budget = to_float(team_info.get("max_budget"))
                if max_budget is None or max_budget <= 0:
                    print(
                        f"[audit] team={team.id} region={region.name} max_budget={max_budget} ok"
                    )
                    continue

                counters.flagged += 1
                finding = {
                    "team_id": team.id,
                    "region_id": region.id,
                    "region_name": region.name,
                    "lite_team_id": lite_team_id,
                    "purchased_total": purchased_total,
                    "max_budget": max_budget,
                }
                self.findings.append(finding)
                print(
                    f"[audit] team={team.id} region={region.name} FLAG max_budget={max_budget:.4f} purchased_total=0"
                )

                if self.dry_run:
                    continue

                try:
                    await service.update_team_budget(
                        team_id=lite_team_id,
                        max_budget=0.0,
                        budget_duration=f"{settings.POOL_BUDGET_EXPIRATION_DAYS}d",
                    )
                    keys = get_team_region_litellm_keys(
                        self.session,
                        team_id=team.id,
                        region_id=region.id,
                    )
                    for key in keys:
                        await service.update_key_budget(
                            litellm_token=key.litellm_token,
                            budget_duration="1mo",
                            max_budget=0.0,
                            clear_max_budget=False,
                        )
                    counters.fixed += 1
                    print(
                        f"[audit] team={team.id} region={region.name} FIXED team_budget=0 keys_locked={len(keys)}"
                    )
                except Exception as exc:
                    counters.failed += 1
                    self.failures.append(
                        {
                            "team_id": team.id,
                            "region_id": region.id,
                            "stage": "apply_fix",
                            "error": str(exc),
                        }
                    )
                    print(
                        f"[audit] team={team.id} region={region.name} FAILED(apply_fix): {exc}"
                    )

                if self.max_rows is not None and counters.flagged >= self.max_rows:
                    return counters

            if self.max_rows is not None and counters.flagged >= self.max_rows:
                return counters

        return counters


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit/fix purchase-gated POOL teams with stale LiteLLM budgets"
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Apply remediations. Without this flag, script runs in dry-run mode.",
    )
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly run in dry-run mode (default behavior).",
    )
    parser.add_argument("--team-id", type=int, default=None, help="Scope to one team")
    parser.add_argument(
        "--region-id", type=int, default=None, help="Scope to one region"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of flagged team/region findings to process",
    )
    parser.add_argument(
        "--findings-json",
        default="/tmp/litellm-backfill-audit-findings.json",
        help="Path to write flagged findings JSON",
    )
    parser.add_argument(
        "--failures-json",
        default="/tmp/litellm-backfill-audit-failures.json",
        help="Path to write failure details JSON",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    mode = "DRY-RUN" if dry_run else "APPLY"
    print(f"Starting LiteLLM purchase-gate audit in {mode} mode")

    session = SessionLocal()
    try:
        runner = AuditRunner(
            session=session,
            dry_run=dry_run,
            team_id=args.team_id,
            region_id=args.region_id,
            max_rows=args.limit,
        )
        counters = await runner.run()
    finally:
        session.close()

    with open(args.findings_json, "w", encoding="utf-8") as fp:
        json.dump(runner.findings, fp, indent=2)
    with open(args.failures_json, "w", encoding="utf-8") as fp:
        json.dump(runner.failures, fp, indent=2)

    print("\nSummary:")
    print(json.dumps(counters.as_dict(), indent=2))
    print(f"Findings captured: {len(runner.findings)} ({args.findings_json})")
    print(f"Failures captured: {len(runner.failures)} ({args.failures_json})")
    return 0 if not runner.failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
