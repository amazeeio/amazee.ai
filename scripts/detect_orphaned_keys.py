#!/usr/bin/env python3
"""
One-time script to detect and handle orphaned AI tokens.

An orphaned key is one where the litellm_token stored in the amazee.ai DB
no longer exists in the corresponding LiteLLM instance.

What this script does:
1. Iterates over all ai_tokens with a litellm_token
2. Checks each token against its region's LiteLLM via /key/info
3. For tokens that return 401 or 404 (not found): nullifies litellm_token and litellm_api_url
4. Also cleans up any spend_caps rows for orphaned keys

Default mode is dry-run. Use --apply to execute changes.

Safety:
  - Only touches keys where LiteLLM explicitly returns 401 or 404 "key does not exist"
  - Transient errors (502, timeouts, 403) are treated as failures, NOT orphans
  - The ai_tokens row is preserved — only litellm_token and litellm_api_url are nulled
  - spend_caps for orphaned keys are deleted (no budget to enforce for a dead key)

Usage:
  python scripts/detect_orphaned_keys.py
  python scripts/detect_orphaned_keys.py --apply
  python scripts/detect_orphaned_keys.py --region-id 2
  python scripts/detect_orphaned_keys.py --limit 50 --apply
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.database import SessionLocal
from app.db.models import DBPrivateAIKey, DBRegion, DBSpendCap
from app.services.litellm import LiteLLMService


def parse_status_from_exc(exc: Exception) -> int | None:
    detail = getattr(exc, "detail", "") or str(exc)
    if "Status 401" in detail:
        return 401
    if "Status 404" in detail:
        return 404
    if "Status 403" in detail:
        return 403
    if "Status 502" in detail:
        return 502
    return None


async def check_token_exists(service: LiteLLMService, token: str) -> str:
    """
    Check if a token exists in LiteLLM.

    Returns:
        "exists" — token is valid
        "orphaned" — token does not exist (401/404)
        "error" — transient error (should not mark as orphan)
    """
    try:
        await service.get_key_info(token)
        return "exists"
    except Exception as exc:
        status = parse_status_from_exc(exc)
        if status in (401, 404):
            return "orphaned"
        return "error"


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect and handle orphaned AI tokens (litellm_token not found in LiteLLM)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Without this flag, script runs in dry-run mode.",
    )
    parser.add_argument(
        "--region-id",
        type=int,
        default=None,
        help="Scope to a single region ID",
    )
    parser.add_argument(
        "--key-id",
        type=int,
        default=None,
        help="Scope to a single key ID",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max keys to process",
    )
    parser.add_argument(
        "--output-json",
        default="/tmp/orphaned-keys-report.json",
        help="Path to write the full report JSON",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    mode = "DRY-RUN" if dry_run else "APPLY"
    print(f"Starting orphaned key detection in {mode} mode")
    print(f"Report will be written to: {args.output_json}")
    print()

    session = SessionLocal()

    query = session.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.litellm_token.isnot(None)
    )
    if args.region_id is not None:
        query = query.filter(DBPrivateAIKey.region_id == args.region_id)
    if args.key_id is not None:
        query = query.filter(DBPrivateAIKey.id == args.key_id)
    query = query.order_by(DBPrivateAIKey.id.asc())
    if args.limit is not None:
        query = query.limit(args.limit)
    keys = query.all()

    print(f"Found {len(keys)} keys with litellm_token to check")

    counters = {
        "processed": 0,
        "exists": 0,
        "orphaned": 0,
        "errors": 0,
        "caps_deleted": 0,
    }
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "orphaned_keys": [],
        "error_keys": [],
    }

    try:
        # Preload all active regions once and build a LiteLLMService per region
        # to avoid an N+1 DB query and redundant service construction per key.
        region_query = session.query(DBRegion).filter(DBRegion.is_active.is_(True))
        if args.region_id is not None:
            region_query = region_query.filter(DBRegion.id == args.region_id)
        regions_by_id: dict[int, DBRegion] = {r.id: r for r in region_query.all()}
        services_by_region_id: dict[int, LiteLLMService] = {
            r.id: LiteLLMService(r.litellm_api_url, r.litellm_api_key)
            for r in regions_by_id.values()
        }

        for key in keys:
            counters["processed"] += 1

            region = regions_by_id.get(key.region_id)
            if not region:
                print(
                    f"  key={key.id:5d} | SKIP | region={key.region_id} inactive/missing"
                )
                continue

            service = services_by_region_id[region.id]
            status = await check_token_exists(service, key.litellm_token)

            if status == "exists":
                counters["exists"] += 1
                # Only log every 100th to reduce noise
                if counters["processed"] % 100 == 0:
                    print(
                        f"  key={key.id:5d} | EXISTS | region={region.name} | processed={counters['processed']}"
                    )
                continue

            if status == "orphaned":
                counters["orphaned"] += 1
                token = key.litellm_token or ""
                redacted_token = f"...{token[-4:]}" if len(token) >= 4 else "****"
                entry = {
                    "key_id": key.id,
                    "key_name": key.name,
                    "region_id": region.id,
                    "region_name": region.name,
                    "litellm_token_hint": redacted_token,
                    "owner_id": key.owner_id,
                    "team_id": key.team_id,
                    "has_spend_cap": False,
                    "action": "would_nullify" if dry_run else "nullified",
                }

                # Check for spend_caps
                caps = (
                    session.query(DBSpendCap)
                    .filter(
                        DBSpendCap.scope == "key",
                        DBSpendCap.key_id == key.id,
                    )
                    .all()
                )
                if caps:
                    entry["has_spend_cap"] = True
                    entry["spend_cap_ids"] = [c.id for c in caps]

                action_word = "WOULD ORPHAN" if dry_run else "ORPHANING"
                print(
                    f"  key={key.id:5d} | {action_word} | region={region.name} | "
                    f"name={key.name} | owner={key.owner_id} | team={key.team_id}"
                    f"{' | has_spend_cap' if caps else ''}"
                )

                if not dry_run:
                    # Nullify the litellm fields — row stays but token is gone
                    key.litellm_token = None
                    key.litellm_api_url = None
                    session.add(key)

                    # Delete any spend_caps for this key
                    if caps:
                        for cap in caps:
                            session.delete(cap)
                        counters["caps_deleted"] += len(caps)

                report["orphaned_keys"].append(entry)

            elif status == "error":
                counters["errors"] += 1
                entry = {
                    "key_id": key.id,
                    "key_name": key.name,
                    "region_id": region.id,
                    "region_name": region.name,
                }
                print(
                    f"  key={key.id:5d} | ERROR | region={region.name} | could not check token"
                )
                report["error_keys"].append(entry)

        if not dry_run:
            session.commit()

    finally:
        session.close()

    # Write report
    report["summary"] = counters
    with open(args.output_json, "w", encoding="utf-8") as fp:
        json.dump(report, fp, indent=2, default=str)

    print()
    print("Summary:")
    print(f"  Processed:  {counters['processed']}")
    print(f"  Exists:     {counters['exists']}")
    print(f"  Orphaned:   {counters['orphaned']}")
    print(f"  Errors:     {counters['errors']}")
    print(f"  Caps del:   {counters['caps_deleted']}")
    print(f"  Report:     {args.output_json}")

    return 0 if not report["error_keys"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
