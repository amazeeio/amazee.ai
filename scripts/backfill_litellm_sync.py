#!/usr/bin/env python3
"""
One-time backfill to reconcile amazee.ai DB state with LiteLLM across regions.

Default mode is dry-run. Use --apply to execute changes.

Usage examples:
  python scripts/backfill_litellm_sync.py
  python scripts/backfill_litellm_sync.py --apply
  python scripts/backfill_litellm_sync.py --phase users --limit 100 --apply
  python scripts/backfill_litellm_sync.py --team-id 123 --apply
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Iterable

import httpx
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.limit_service import DEFAULT_MAX_SPEND
from app.db.database import SessionLocal
from app.db.models import DBLimitedResource, DBPrivateAIKey, DBRegion, DBTeam, DBTeamRegion, DBUser
from app.schemas.limits import OwnerType, ResourceType
from app.schemas.models import BudgetType
from app.services.litellm import LiteLLMService


def is_trial_user(email: str | None) -> bool:
    lowered = (email or "").lower()
    return lowered.startswith("trial-") and lowered.endswith("@example.com")


def dedupe_regions(regions: Iterable[DBRegion]) -> list[DBRegion]:
    seen: set[int] = set()
    out: list[DBRegion] = []
    for region in regions:
        if region.id in seen:
            continue
        seen.add(region.id)
        out.append(region)
    return out


def team_budget_limit(session: Session, team_id: int) -> float | None:
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


def parse_status_from_exc(exc: Exception) -> int | None:
    detail = getattr(exc, "detail", "") or str(exc)
    if "Status 404" in detail:
        return 404
    if "Status 409" in detail:
        return 409
    if "Status 400" in detail:
        return 400
    return None


@dataclass
class Counters:
    processed: int = 0
    changed: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict:
        return {
            "processed": self.processed,
            "changed": self.changed,
            "skipped": self.skipped,
            "failed": self.failed,
        }


class BackfillRunner:
    def __init__(
        self,
        session: Session,
        dry_run: bool,
        max_rows: int | None,
        team_id: int | None,
        user_id: int | None,
        key_id: int | None,
    ) -> None:
        self.session = session
        self.dry_run = dry_run
        self.max_rows = max_rows
        self.only_team_id = team_id
        self.only_user_id = user_id
        self.only_key_id = key_id
        self.failures: list[dict] = []

    def _active_shared_regions(self) -> list[DBRegion]:
        return (
            self.session.query(DBRegion)
            .filter(DBRegion.is_active.is_(True), DBRegion.is_dedicated.is_(False))
            .all()
        )

    def _active_dedicated_regions_by_team(self) -> dict[int, list[DBRegion]]:
        rows = (
            self.session.query(DBTeamRegion, DBRegion)
            .join(DBRegion, DBRegion.id == DBTeamRegion.region_id)
            .filter(DBRegion.is_active.is_(True), DBRegion.is_dedicated.is_(True))
            .all()
        )
        out: dict[int, list[DBRegion]] = {}
        for assoc, region in rows:
            out.setdefault(assoc.team_id, []).append(region)
        return out

    def _target_regions_for_team(
        self, team_id: int, shared: list[DBRegion], dedicated_by_team: dict[int, list[DBRegion]]
    ) -> list[DBRegion]:
        return dedupe_regions([*shared, *dedicated_by_team.get(team_id, [])])

    async def _ensure_team_exists(
        self, service: LiteLLMService, lite_team_id: str, max_budget: float | None, budget_duration: str | None
    ) -> tuple[bool, str]:
        try:
            await service.get_team_info(lite_team_id)
            return False, "exists"
        except Exception as exc:
            if parse_status_from_exc(exc) != 404:
                raise

        if self.dry_run:
            return True, "would_create"
        await service.create_team(
            team_id=lite_team_id,
            team_alias=lite_team_id,
            max_budget=max_budget,
            budget_duration=budget_duration,
        )
        return True, "created"

    async def phase_teams(self) -> Counters:
        counters = Counters()
        shared = self._active_shared_regions()
        dedicated_by_team = self._active_dedicated_regions_by_team()

        query = self.session.query(DBTeam).filter(
            DBTeam.deleted_at.is_(None), DBTeam.is_active.is_(True)
        )
        if self.only_team_id is not None:
            query = query.filter(DBTeam.id == self.only_team_id)
        teams = query.order_by(DBTeam.id.asc()).all()

        for team in teams:
            regions = self._target_regions_for_team(team.id, shared, dedicated_by_team)
            budget_duration = (
                "365d" if team.budget_type == BudgetType.POOL else None
            )
            budget_limit = team_budget_limit(self.session, team.id)
            max_budget = (
                0.0
                if team.budget_type == BudgetType.POOL
                else (budget_limit if budget_limit is not None else DEFAULT_MAX_SPEND)
            )

            for region in regions:
                counters.processed += 1
                lite_team_id = LiteLLMService.format_team_id(region.name, team.id)
                service = LiteLLMService(region.litellm_api_url, region.litellm_api_key)
                try:
                    changed, action = await self._ensure_team_exists(
                        service, lite_team_id, max_budget, budget_duration
                    )
                    # Reconcile budget even for existing teams.
                    if self.dry_run:
                        budget_action = "would_update_budget"
                        changed = True if changed else True
                    else:
                        await service.update_team_budget(
                            team_id=lite_team_id,
                            max_budget=max_budget,
                            budget_duration=budget_duration,
                        )
                        budget_action = "updated_budget"
                        changed = True
                    counters.changed += 1 if changed else 0
                    print(
                        f"[teams] team={team.id} region={region.name} team_id={lite_team_id} {action}+{budget_action}"
                    )
                except Exception as exc:
                    counters.failed += 1
                    self.failures.append(
                        {
                            "phase": "teams",
                            "team_id": team.id,
                            "region_id": region.id,
                            "error": str(exc),
                        }
                    )
                    print(
                        f"[teams] team={team.id} region={region.name} FAILED: {exc}"
                    )

                if self.max_rows is not None and counters.processed >= self.max_rows:
                    return counters
        return counters

    async def phase_users(self) -> Counters:
        counters = Counters()
        shared = self._active_shared_regions()
        dedicated_by_team = self._active_dedicated_regions_by_team()

        query = self.session.query(DBUser).filter(DBUser.is_active.is_(True))
        if self.only_user_id is not None:
            query = query.filter(DBUser.id == self.only_user_id)
        users = query.order_by(DBUser.id.asc()).all()

        for user in users:
            counters.processed += 1
            if is_trial_user(user.email):
                counters.skipped += 1
                print(f"[users] user={user.id} skipped trial")
                continue

            if user.team_id is None:
                regions = shared
            else:
                team = self.session.query(DBTeam).filter(DBTeam.id == user.team_id).first()
                if not team or team.deleted_at is not None or not team.is_active:
                    counters.skipped += 1
                    print(f"[users] user={user.id} skipped inactive/deleted team={user.team_id}")
                    continue
                regions = self._target_regions_for_team(user.team_id, shared, dedicated_by_team)

            try:
                for region in regions:
                    service = LiteLLMService(region.litellm_api_url, region.litellm_api_key)
                    if not self.dry_run:
                        await service.create_user(
                            user_id=str(user.id),
                            user_email=user.email,
                            auto_create_key=False,
                        )
                    if user.team_id is not None:
                        lite_team_id = LiteLLMService.format_team_id(region.name, user.team_id)
                        if not self.dry_run:
                            await service.add_team_member(
                                team_id=lite_team_id, user_id=str(user.id), role="user"
                            )
                counters.changed += 1
                mode = "would_sync" if self.dry_run else "synced"
                print(f"[users] user={user.id} {mode} regions={len(regions)}")
            except Exception as exc:
                counters.failed += 1
                self.failures.append(
                    {"phase": "users", "user_id": user.id, "error": str(exc)}
                )
                print(f"[users] user={user.id} FAILED: {exc}")

            if self.max_rows is not None and counters.processed >= self.max_rows:
                return counters
        return counters

    async def _update_litellm_key_associations(
        self, region: DBRegion, litellm_token: str, lite_team_id: str | None, user_id: int | None
    ) -> None:
        payload: dict = {"key": litellm_token}
        if lite_team_id is not None:
            payload["team_id"] = lite_team_id
        if user_id is not None:
            payload["user_id"] = str(user_id)

        headers = {"Authorization": f"Bearer {region.litellm_api_key}"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{region.litellm_api_url}/key/update", json=payload, headers=headers)
            resp.raise_for_status()

    async def phase_keys(self) -> Counters:
        counters = Counters()
        query = self.session.query(DBPrivateAIKey).filter(DBPrivateAIKey.litellm_token.isnot(None))
        if self.only_key_id is not None:
            query = query.filter(DBPrivateAIKey.id == self.only_key_id)
        keys = query.order_by(DBPrivateAIKey.id.asc()).all()

        for key in keys:
            counters.processed += 1
            region = self.session.query(DBRegion).filter(DBRegion.id == key.region_id).first()
            if not region or not region.is_active:
                counters.skipped += 1
                print(f"[keys] key={key.id} skipped inactive/missing region={key.region_id}")
                continue

            owner = self.session.query(DBUser).filter(DBUser.id == key.owner_id).first() if key.owner_id else None
            if owner and is_trial_user(owner.email):
                counters.skipped += 1
                print(f"[keys] key={key.id} skipped trial owner={owner.id}")
                continue

            effective_team_id = key.team_id
            if effective_team_id is None and owner and owner.team_id is not None:
                effective_team_id = owner.team_id
                if self.dry_run:
                    print(
                        f"[keys] key={key.id} would_repair_db_team_id null->{effective_team_id}"
                    )
                else:
                    key.team_id = effective_team_id
                    self.session.add(key)
                    self.session.flush()
                    print(
                        f"[keys] key={key.id} repaired_db_team_id null->{effective_team_id}"
                    )

            lite_team_id = (
                LiteLLMService.format_team_id(region.name, effective_team_id)
                if effective_team_id is not None
                else None
            )

            try:
                if self.dry_run:
                    print(
                        f"[keys] key={key.id} would_update_litellm team={lite_team_id} user={owner.id if owner else None}"
                    )
                else:
                    await self._update_litellm_key_associations(
                        region=region,
                        litellm_token=key.litellm_token,
                        lite_team_id=lite_team_id,
                        user_id=owner.id if owner else None,
                    )
                    print(
                        f"[keys] key={key.id} updated_litellm team={lite_team_id} user={owner.id if owner else None}"
                    )
                counters.changed += 1
            except Exception as exc:
                counters.failed += 1
                self.failures.append(
                    {"phase": "keys", "key_id": key.id, "error": str(exc)}
                )
                print(f"[keys] key={key.id} FAILED: {exc}")

            if self.max_rows is not None and counters.processed >= self.max_rows:
                break

        if not self.dry_run:
            self.session.commit()
        return counters


async def main() -> int:
    parser = argparse.ArgumentParser(description="One-time LiteLLM backfill script")
    parser.add_argument(
        "--phase",
        choices=["teams", "users", "keys", "all"],
        default="all",
        help="Which phase to run",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Without this flag, script runs in dry-run mode.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max rows per phase")
    parser.add_argument("--team-id", type=int, default=None, help="Scope to one team")
    parser.add_argument("--user-id", type=int, default=None, help="Scope to one user")
    parser.add_argument("--key-id", type=int, default=None, help="Scope to one key")
    parser.add_argument(
        "--failures-json",
        default="/tmp/litellm-backfill-failures.json",
        help="Path to write failure details JSON",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    mode = "DRY-RUN" if dry_run else "APPLY"
    print(f"Starting LiteLLM backfill in {mode} mode")

    session = SessionLocal()
    runner = BackfillRunner(
        session=session,
        dry_run=dry_run,
        max_rows=args.limit,
        team_id=args.team_id,
        user_id=args.user_id,
        key_id=args.key_id,
    )
    summary: dict[str, dict] = {}

    try:
        if args.phase in ("teams", "all"):
            summary["teams"] = (await runner.phase_teams()).as_dict()
        if args.phase in ("users", "all"):
            summary["users"] = (await runner.phase_users()).as_dict()
        if args.phase in ("keys", "all"):
            summary["keys"] = (await runner.phase_keys()).as_dict()

        if dry_run:
            session.rollback()
        else:
            session.commit()
    finally:
        session.close()

    with open(args.failures_json, "w", encoding="utf-8") as fp:
        json.dump(runner.failures, fp, indent=2)

    print("\nSummary:")
    print(json.dumps(summary, indent=2))
    print(f"Failures captured: {len(runner.failures)} ({args.failures_json})")
    return 0 if not runner.failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
