#!/usr/bin/env python3
"""
Manual, script-first migration for converting one region from dedicated to public
and reconciling DB + LiteLLM state.

Default is dry-run. Use --apply for writes.
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, UTC

import httpx
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models import (
    DBPrivateAIKey,
    DBRegion,
    DBTeam,
    DBTeamRegion,
    DBUser,
    DBUserAdminRegion,
)
from app.services.litellm import LiteLLMService


def parse_status_from_exc(exc: Exception) -> int | None:
    detail = getattr(exc, "detail", "") or str(exc)
    for code in (404, 409, 400, 500, 502, 503, 504):
        if f"Status {code}" in detail:
            return code
    return None


def is_trial_user(email: str | None) -> bool:
    lowered = (email or "").lower()
    return lowered.startswith("trial-") and lowered.endswith("@example.com")


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


class RegionConversionRunner:
    def __init__(
        self,
        session: Session,
        region_id: int,
        dry_run: bool,
        cleanup_admin_regions: str,
        batch_size: int,
        max_rows: int | None,
    ) -> None:
        self.session = session
        self.region_id = region_id
        self.dry_run = dry_run
        self.cleanup_admin_regions = cleanup_admin_regions
        self.batch_size = max(1, batch_size)
        self.max_rows = max_rows

        self.failures: list[dict] = []
        self.report: dict = {
            "region_id": region_id,
            "dry_run": dry_run,
            "cleanup_admin_regions": cleanup_admin_regions,
            "started_at": datetime.now(UTC).isoformat(),
            "phases": {},
        }

        self._team_members_cache: dict[tuple[int, str], frozenset[str]] = {}
        self._pre_conversion_dedicated_only_team_ids: list[int] = []

    def _region(self) -> DBRegion | None:
        return (
            self.session.query(DBRegion).filter(DBRegion.id == self.region_id).first()
        )

    def _active_teams_query(self):
        return self.session.query(DBTeam).filter(
            DBTeam.deleted_at.is_(None),
            DBTeam.is_active.is_(True),
        )

    def _in_scope_team_ids(self) -> list[int]:
        return [
            t.id for t in self._active_teams_query().order_by(DBTeam.id.asc()).all()
        ]

    def _active_public_regions(self) -> list[DBRegion]:
        return (
            self.session.query(DBRegion)
            .filter(DBRegion.is_active.is_(True), DBRegion.is_dedicated.is_(False))
            .order_by(DBRegion.id.asc())
            .all()
        )

    def _current_associated_region_ids_for_team(self, team_id: int) -> set[int]:
        rows = (
            self.session.query(DBTeamRegion.region_id)
            .filter(DBTeamRegion.team_id == team_id)
            .all()
        )
        return {r[0] for r in rows}

    def _snapshot_preconversion_dedicated_only_teams(self) -> list[int]:
        teams = self._active_teams_query().order_by(DBTeam.id.asc()).all()
        public_region_ids = {
            r.id
            for r in self.session.query(DBRegion)
            .filter(DBRegion.is_active.is_(True), DBRegion.is_dedicated.is_(False))
            .all()
        }
        dedicated_only: list[int] = []
        for team in teams:
            assoc_region_ids = self._current_associated_region_ids_for_team(team.id)
            if assoc_region_ids and assoc_region_ids.isdisjoint(public_region_ids):
                dedicated_only.append(team.id)
        return dedicated_only

    def _active_users_for_teams(self, team_ids: list[int]) -> list[DBUser]:
        if not team_ids:
            return []
        return (
            self.session.query(DBUser)
            .filter(DBUser.is_active.is_(True), DBUser.team_id.in_(team_ids))
            .order_by(DBUser.id.asc())
            .all()
        )

    def _record_phase(
        self, name: str, counters: Counters, extra: dict | None = None
    ) -> None:
        payload = counters.as_dict()
        if extra:
            payload.update(extra)
        self.report["phases"][name] = payload

    async def phase_preflight(self) -> Counters:
        counters = Counters(processed=1)
        region = self._region()
        if not region:
            counters.failed = 1
            self.failures.append({"phase": "preflight", "error": "Region not found"})
            self._record_phase("preflight", counters)
            return counters
        if not region.is_active:
            counters.failed = 1
            self.failures.append({"phase": "preflight", "error": "Region is inactive"})
            self._record_phase("preflight", counters)
            return counters

        try:
            service = LiteLLMService(region.litellm_api_url, region.litellm_api_key)
            await service.get_model_info()
        except Exception as exc:
            counters.failed = 1
            self.failures.append(
                {"phase": "preflight", "error": f"LiteLLM reachability failed: {exc}"}
            )
            self._record_phase("preflight", counters)
            return counters

        associated_teams = (
            self.session.query(DBTeamRegion)
            .filter(DBTeamRegion.region_id == self.region_id)
            .count()
        )
        active_teams = self._active_teams_query().count()
        active_users = (
            self.session.query(DBUser)
            .filter(DBUser.is_active.is_(True), DBUser.team_id.isnot(None))
            .count()
        )
        region_keys = (
            self.session.query(DBPrivateAIKey)
            .filter(DBPrivateAIKey.region_id == self.region_id)
            .count()
        )
        admin_region_rows = (
            self.session.query(DBUserAdminRegion)
            .filter(DBUserAdminRegion.region_id == self.region_id)
            .count()
        )

        self._pre_conversion_dedicated_only_team_ids = (
            self._snapshot_preconversion_dedicated_only_teams()
        )

        counters.changed = 1
        self._record_phase(
            "preflight",
            counters,
            {
                "region_name": region.name,
                "region_is_dedicated": bool(region.is_dedicated),
                "active_teams": active_teams,
                "associated_teams": associated_teams,
                "active_team_users": active_users,
                "region_keys": region_keys,
                "user_admin_region_rows": admin_region_rows,
                "pre_conversion_dedicated_only_teams": len(
                    self._pre_conversion_dedicated_only_team_ids
                ),
            },
        )
        return counters

    async def phase_convert(self) -> Counters:
        counters = Counters(processed=1)
        region = self._region()
        if not region:
            counters.failed = 1
            self.failures.append({"phase": "convert", "error": "Region not found"})
            self._record_phase("convert", counters)
            return counters

        if not region.is_dedicated:
            counters.skipped = 1
            self._record_phase(
                "convert", counters, {"result": "noop", "reason": "already_public"}
            )
            return counters

        counters.changed = 1
        if not self.dry_run:
            region.is_dedicated = False
            self.session.commit()

        self._record_phase("convert", counters, {"result": "converted"})
        return counters

    async def phase_db_backfill(self) -> Counters:
        counters = Counters()
        team_ids = self._in_scope_team_ids()

        for team_id in team_ids:
            counters.processed += 1
            assoc = (
                self.session.query(DBTeamRegion)
                .filter(
                    DBTeamRegion.team_id == team_id,
                    DBTeamRegion.region_id == self.region_id,
                )
                .first()
            )
            if assoc:
                counters.skipped += 1
            else:
                counters.changed += 1
                if not self.dry_run:
                    self.session.add(
                        DBTeamRegion(team_id=team_id, region_id=self.region_id)
                    )

            if self.max_rows is not None and counters.processed >= self.max_rows:
                break

        cleanup_changed = 0
        cleanup_skipped = 0
        if self.cleanup_admin_regions == "delete":
            rows = (
                self.session.query(DBUserAdminRegion)
                .filter(DBUserAdminRegion.region_id == self.region_id)
                .all()
            )
            for row in rows:
                cleanup_changed += 1
                if not self.dry_run:
                    self.session.delete(row)
        else:
            cleanup_skipped = (
                self.session.query(DBUserAdminRegion)
                .filter(DBUserAdminRegion.region_id == self.region_id)
                .count()
            )

        if not self.dry_run:
            self.session.commit()
        else:
            self.session.rollback()

        self._record_phase(
            "db-backfill",
            counters,
            {
                "in_scope_teams": len(team_ids),
                "admin_region_rows_deleted": cleanup_changed,
                "admin_region_rows_kept": cleanup_skipped,
            },
        )
        return counters

    async def phase_litellm_teams(self) -> Counters:
        counters = Counters()
        region = self._region()
        if not region:
            counters.failed = 1
            self.failures.append(
                {"phase": "litellm-teams", "error": "Region not found"}
            )
            self._record_phase("litellm-teams", counters)
            return counters

        team_ids = self._in_scope_team_ids()
        teams = (
            self.session.query(DBTeam)
            .filter(DBTeam.id.in_(team_ids))
            .order_by(DBTeam.id.asc())
            .all()
        )

        service = LiteLLMService(region.litellm_api_url, region.litellm_api_key)
        for team in teams:
            counters.processed += 1
            lite_team_id = LiteLLMService.format_team_id(region.name, team.id)
            try:
                await service.get_team_info(lite_team_id)
                counters.skipped += 1
            except Exception as exc:
                if parse_status_from_exc(exc) != 404:
                    counters.failed += 1
                    self.failures.append(
                        {
                            "phase": "litellm-teams",
                            "team_id": team.id,
                            "error": str(exc),
                        }
                    )
                    continue

                counters.changed += 1
                if not self.dry_run:
                    if team.requires_pool_purchase_gate:
                        max_budget = 0.0
                        budget_duration = f"{settings.POOL_BUDGET_EXPIRATION_DAYS}d"
                    else:
                        max_budget = None
                        budget_duration = None
                    await service.create_team(
                        team_id=lite_team_id,
                        team_alias=lite_team_id,
                        max_budget=max_budget,
                        budget_duration=budget_duration,
                    )

            if self.max_rows is not None and counters.processed >= self.max_rows:
                break

        self._record_phase("litellm-teams", counters, {"in_scope_teams": len(teams)})
        return counters

    async def phase_litellm_users(self) -> Counters:
        counters = Counters()
        region = self._region()
        if not region:
            counters.failed = 1
            self.failures.append(
                {"phase": "litellm-users", "error": "Region not found"}
            )
            self._record_phase("litellm-users", counters)
            return counters

        team_ids = self._in_scope_team_ids()
        users = self._active_users_for_teams(team_ids)
        service = LiteLLMService(region.litellm_api_url, region.litellm_api_key)

        for user in users:
            counters.processed += 1
            if is_trial_user(user.email):
                counters.skipped += 1
                continue

            try:
                user_exists = False
                try:
                    await service.get_user_info(str(user.id))
                    user_exists = True
                except Exception as exc:
                    if parse_status_from_exc(exc) != 404:
                        raise

                if not user_exists:
                    counters.changed += 1
                    if not self.dry_run:
                        await service.create_user(
                            user_id=str(user.id),
                            user_email=user.email,
                            auto_create_key=False,
                        )

                lite_team_id = LiteLLMService.format_team_id(region.name, user.team_id)
                cache_key = (region.id, lite_team_id)
                if cache_key not in self._team_members_cache:
                    try:
                        info = await service.get_team_info(lite_team_id)
                        team_data = info.get("team_info", info)
                        members = team_data.get("members", [])
                        self._team_members_cache[cache_key] = frozenset(
                            str(m.get("user_id", "")) for m in members
                        )
                    except Exception as exc:
                        if parse_status_from_exc(exc) != 404:
                            raise
                        self._team_members_cache[cache_key] = frozenset()

                if str(user.id) not in self._team_members_cache[cache_key]:
                    counters.changed += 1
                    if not self.dry_run:
                        await service.add_team_member(
                            team_id=lite_team_id,
                            user_id=str(user.id),
                            role="user",
                        )
                    # keep cache warm for further users in same team
                    self._team_members_cache[cache_key] = frozenset(
                        set(self._team_members_cache[cache_key]) | {str(user.id)}
                    )
                else:
                    counters.skipped += 1

            except Exception as exc:
                counters.failed += 1
                self.failures.append(
                    {"phase": "litellm-users", "user_id": user.id, "error": str(exc)}
                )

            if self.max_rows is not None and counters.processed >= self.max_rows:
                break

        self._record_phase("litellm-users", counters, {"in_scope_users": len(users)})
        return counters

    async def phase_litellm_keys(self) -> Counters:
        counters = Counters()
        region = self._region()
        if not region:
            counters.failed = 1
            self.failures.append({"phase": "litellm-keys", "error": "Region not found"})
            self._record_phase("litellm-keys", counters)
            return counters

        in_scope_teams = set(self._in_scope_team_ids())
        query = (
            self.session.query(DBPrivateAIKey)
            .filter(
                DBPrivateAIKey.region_id == self.region_id,
                DBPrivateAIKey.litellm_token.isnot(None),
            )
            .order_by(DBPrivateAIKey.id.asc())
        )
        keys = query.all()

        headers = {"Authorization": f"Bearer {region.litellm_api_key}"}
        async with httpx.AsyncClient() as client:
            for key in keys:
                counters.processed += 1
                owner = (
                    self.session.query(DBUser).filter(DBUser.id == key.owner_id).first()
                    if key.owner_id
                    else None
                )

                effective_team_id = key.team_id
                if effective_team_id is None and owner and owner.team_id:
                    effective_team_id = owner.team_id
                    if effective_team_id in in_scope_teams:
                        counters.changed += 1
                        if not self.dry_run:
                            key.team_id = effective_team_id
                            self.session.add(key)

                if (
                    effective_team_id is not None
                    and effective_team_id not in in_scope_teams
                ):
                    counters.skipped += 1
                    continue

                payload = {"key": key.litellm_token}
                if effective_team_id is not None:
                    payload["team_id"] = LiteLLMService.format_team_id(
                        region.name, effective_team_id
                    )
                if owner is not None:
                    payload["user_id"] = str(owner.id)

                try:
                    if not self.dry_run:
                        resp = await client.post(
                            f"{region.litellm_api_url}/key/update",
                            json=payload,
                            headers=headers,
                        )
                        resp.raise_for_status()
                    counters.changed += 1
                except Exception as exc:
                    counters.failed += 1
                    self.failures.append(
                        {"phase": "litellm-keys", "key_id": key.id, "error": str(exc)}
                    )

                if self.max_rows is not None and counters.processed >= self.max_rows:
                    break

        if not self.dry_run:
            self.session.commit()
        else:
            self.session.rollback()

        self._record_phase("litellm-keys", counters, {"region_keys": len(keys)})
        return counters

    async def phase_reverse_transfer_to_public_regions(self) -> Counters:
        counters = Counters()
        source_team_ids = list(self._pre_conversion_dedicated_only_team_ids)
        if not source_team_ids:
            self._record_phase(
                "reverse-transfer",
                counters,
                {"dedicated_only_teams": 0, "public_regions": 0},
            )
            return counters

        public_regions = self._active_public_regions()
        users = self._active_users_for_teams(source_team_ids)

        for team_id in source_team_ids:
            current_assocs = self._current_associated_region_ids_for_team(team_id)
            for region in public_regions:
                counters.processed += 1
                if region.id in current_assocs:
                    counters.skipped += 1
                    continue
                counters.changed += 1
                if not self.dry_run:
                    self.session.add(DBTeamRegion(team_id=team_id, region_id=region.id))

        if not self.dry_run:
            self.session.commit()
        else:
            self.session.rollback()

        for region in public_regions:
            service = LiteLLMService(region.litellm_api_url, region.litellm_api_key)
            for team_id in source_team_ids:
                lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
                try:
                    await service.get_team_info(lite_team_id)
                except Exception as exc:
                    if parse_status_from_exc(exc) != 404:
                        counters.failed += 1
                        self.failures.append(
                            {
                                "phase": "reverse-transfer",
                                "team_id": team_id,
                                "region_id": region.id,
                                "error": str(exc),
                            }
                        )
                        continue
                    if not self.dry_run:
                        team = (
                            self.session.query(DBTeam)
                            .filter(DBTeam.id == team_id)
                            .first()
                        )
                        if team is None:
                            counters.failed += 1
                            continue
                        if team.requires_pool_purchase_gate:
                            max_budget = 0.0
                            budget_duration = f"{settings.POOL_BUDGET_EXPIRATION_DAYS}d"
                        else:
                            max_budget = None
                            budget_duration = None
                        await service.create_team(
                            team_id=lite_team_id,
                            team_alias=lite_team_id,
                            max_budget=max_budget,
                            budget_duration=budget_duration,
                        )

            for user in users:
                if is_trial_user(user.email):
                    continue
                lite_team_id = LiteLLMService.format_team_id(region.name, user.team_id)
                try:
                    try:
                        await service.get_user_info(str(user.id))
                    except Exception as exc:
                        if parse_status_from_exc(exc) != 404:
                            raise
                        if not self.dry_run:
                            await service.create_user(
                                user_id=str(user.id),
                                user_email=user.email,
                                auto_create_key=False,
                            )

                    cache_key = (region.id, lite_team_id)
                    if cache_key not in self._team_members_cache:
                        try:
                            info = await service.get_team_info(lite_team_id)
                            team_data = info.get("team_info", info)
                            members = team_data.get("members", [])
                            self._team_members_cache[cache_key] = frozenset(
                                str(m.get("user_id", "")) for m in members
                            )
                        except Exception as exc:
                            if parse_status_from_exc(exc) != 404:
                                raise
                            self._team_members_cache[cache_key] = frozenset()

                    if str(user.id) not in self._team_members_cache[cache_key]:
                        if not self.dry_run:
                            await service.add_team_member(
                                team_id=lite_team_id,
                                user_id=str(user.id),
                                role="user",
                            )
                        self._team_members_cache[cache_key] = frozenset(
                            set(self._team_members_cache[cache_key]) | {str(user.id)}
                        )
                except Exception as exc:
                    counters.failed += 1
                    self.failures.append(
                        {
                            "phase": "reverse-transfer",
                            "user_id": user.id,
                            "team_id": user.team_id,
                            "region_id": region.id,
                            "error": str(exc),
                        }
                    )

        self._record_phase(
            "reverse-transfer",
            counters,
            {
                "dedicated_only_teams": len(source_team_ids),
                "public_regions": len(public_regions),
                "users_in_scope": len(users),
            },
        )
        return counters

    async def phase_verify(self) -> Counters:
        counters = Counters(processed=1)
        region = self._region()
        if not region:
            counters.failed = 1
            self.failures.append({"phase": "verify", "error": "Region not found"})
            self._record_phase("verify", counters)
            return counters

        team_ids = self._in_scope_team_ids()
        assoc_count = (
            self.session.query(DBTeamRegion)
            .filter(
                DBTeamRegion.region_id == self.region_id,
                DBTeamRegion.team_id.in_(team_ids) if team_ids else False,
            )
            .count()
            if team_ids
            else 0
        )
        admin_region_count = (
            self.session.query(DBUserAdminRegion)
            .filter(DBUserAdminRegion.region_id == self.region_id)
            .count()
        )

        counters.changed = 1
        self._record_phase(
            "verify",
            counters,
            {
                "region_is_dedicated": bool(region.is_dedicated),
                "in_scope_teams": len(team_ids),
                "in_scope_associations": assoc_count,
                "remaining_user_admin_region_rows": admin_region_count,
            },
        )
        return counters


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a dedicated region to public and reconcile DB/LiteLLM state"
    )
    parser.add_argument("--region-id", type=int, required=True)
    parser.add_argument(
        "--phase",
        choices=[
            "preflight",
            "convert",
            "db-backfill",
            "litellm-teams",
            "litellm-users",
            "litellm-keys",
            "verify",
            "all",
        ],
        default="all",
    )
    parser.add_argument("--apply", action="store_true", help="Execute writes")
    parser.add_argument(
        "--cleanup-admin-regions",
        choices=["delete", "keep"],
        default="delete",
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--failures-json",
        default="/tmp/region-conversion-failures.json",
    )
    parser.add_argument(
        "--report-json",
        default="/tmp/region-conversion-report.json",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    print(
        f"Starting region conversion script in {'DRY-RUN' if dry_run else 'APPLY'} mode for region_id={args.region_id}"
    )

    session = SessionLocal()
    runner = RegionConversionRunner(
        session=session,
        region_id=args.region_id,
        dry_run=dry_run,
        cleanup_admin_regions=args.cleanup_admin_regions,
        batch_size=args.batch_size,
        max_rows=args.limit,
    )

    try:
        if args.phase in ("preflight", "all"):
            await runner.phase_preflight()
        if args.phase in ("convert", "all"):
            await runner.phase_convert()
        if args.phase in ("db-backfill", "all"):
            await runner.phase_db_backfill()
        if args.phase in ("litellm-teams", "all"):
            await runner.phase_litellm_teams()
        if args.phase in ("litellm-users", "all"):
            await runner.phase_litellm_users()
        if args.phase in ("litellm-keys", "all"):
            await runner.phase_litellm_keys()
        if args.phase in ("all",):
            await runner.phase_reverse_transfer_to_public_regions()
        if args.phase in ("verify", "all"):
            await runner.phase_verify()
    finally:
        session.close()

    runner.report["completed_at"] = datetime.now(UTC).isoformat()
    runner.report["failure_count"] = len(runner.failures)

    with open(args.failures_json, "w", encoding="utf-8") as fp:
        json.dump(runner.failures, fp, indent=2)
    with open(args.report_json, "w", encoding="utf-8") as fp:
        json.dump(runner.report, fp, indent=2)

    print(json.dumps(runner.report, indent=2))
    print(f"Failures captured: {len(runner.failures)} ({args.failures_json})")
    print(f"Report written: {args.report_json}")
    return 0 if not runner.failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
