#!/usr/bin/env python3
"""Delete anonymous-trial keys from ONE region, with their remote resources.

Written after 2026-08-02, when an ad-hoc script walked every region and expired
1,110 trial keys that had just been minted on the new trial region. The safety
properties are not in this file — they are in ``app.core.trial_cleanup``, which
refuses an unfiltered sweep of whatever region ``AI_TRIAL_REGION`` resolves to.
That region can still be cleaned, but only of keys old enough to be abandoned
that never recorded any spend. This script is a command line over that.

Deletes, per key: the LiteLLM key, the Postgres vector database, the key row and
its dependent rows, and optionally the trial user. Remote resources go first, so
a failure leaves a retryable state rather than a stranded database.

Both endpoints must be reachable. Once a region's LiteLLM proxy or vector-DB
host is gone the remote resources cannot be deleted, and this script will refuse
each key rather than silently drop the rows that point at them.

Usage:
    # what would happen (default)
    python scripts/cleanup_trial_keys.py --region 5

    # only long-abandoned trials
    python scripts/cleanup_trial_keys.py --region 5 --older-than-days 30 --unused-only

    # do it, taking the trial user rows too
    python scripts/cleanup_trial_keys.py --region 5 --apply --delete-users

Re-running is safe: deleted keys are gone from the selection, and both remote
calls are idempotent, so keys that failed part-way through are simply retried.
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.trial_cleanup import (  # noqa: E402
    LiveTrialRegionError,
    TrialCleanupSummary,
    delete_trial_key,
    select_trial_keys,
)
from app.db.database import SessionLocal  # noqa: E402
from app.db.models import DBRegion  # noqa: E402
from app.db.postgres import PostgresManager  # noqa: E402
from app.services.litellm import LiteLLMService  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("cleanup_trial_keys")


async def run(args) -> int:
    db = SessionLocal()
    try:
        region = db.query(DBRegion).filter(DBRegion.id == args.region).first()
        if region is None:
            logger.error("Region %s not found", args.region)
            return 1

        try:
            keys = select_trial_keys(
                db,
                args.region,
                older_than_days=args.older_than_days,
                unused_only=args.unused_only,
                limit=args.limit,
            )
        except LiveTrialRegionError as e:
            logger.error("%s", e)
            return 2

        filters = []
        if args.older_than_days is not None:
            filters.append(f"older than {args.older_than_days}d")
        if args.unused_only:
            filters.append("no recorded spend")
        if args.limit is not None:
            filters.append(f"first {args.limit}")

        print(f"\nRegion {region.id} ({region.name})")
        print(f"LiteLLM:  {region.litellm_api_url}")
        print(f"Postgres: {region.postgres_host}")
        print(f"Filters:  {', '.join(filters) if filters else 'none — whole region'}")
        print(f"Matched:  {len(keys)} trial key(s)")
        print(f"Users:    {'deleted with their last key' if args.delete_users else 'kept'}")

        if not keys:
            return 0

        if not args.apply:
            for key in keys[:10]:
                print(
                    f"  [DRY-RUN] key_id={key.id} created={key.created_at} "
                    f"db={key.database_name}"
                )
            if len(keys) > 10:
                print(f"  ... and {len(keys) - 10} more")
            print("\nDry run. Re-run with --apply to delete.")
            return 0

        if not args.yes:
            answer = input(f"\nDelete {len(keys)} key(s) from {region.name}? (y/N): ")
            if answer.lower() != "y":
                print("Cancelled.")
                return 0

        # One service per region, not per key — each key is an HTTP call plus a
        # DROP DATABASE, and reconnecting for every one of thousands is wasteful.
        litellm_service = LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )
        postgres_manager = PostgresManager(region=region)

        summary = TrialCleanupSummary()
        for index, key in enumerate(keys, start=1):
            result = await delete_trial_key(
                db,
                key,
                region,
                delete_user=args.delete_users,
                litellm_service=litellm_service,
                postgres_manager=postgres_manager,
            )
            summary.add(result)
            if not result.ok:
                logger.error("key_id=%s %s", result.key_id, result.error)
                if summary.failed >= args.max_failures:
                    logger.error(
                        "Stopping after %s failures. If the region's endpoints are "
                        "down, keys cannot be deleted and the rows must stay.",
                        summary.failed,
                    )
                    break
            if index % args.progress_every == 0:
                print(
                    f"  {index}/{len(keys)} processed "
                    f"(deleted={summary.deleted} failed={summary.failed})"
                )

        print(
            f"\nDone. deleted={summary.deleted} failed={summary.failed} "
            f"users_deleted={summary.users_deleted}"
        )
        return 0 if summary.failed == 0 else 1
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Delete anonymous-trial keys from one region."
    )
    parser.add_argument(
        "--region",
        type=int,
        required=True,
        help="Region id to clean. If it is the live trial region, both "
        "--older-than-days and --unused-only are required.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without this the script only reports.",
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        help="Only keys created more than this many days ago.",
    )
    parser.add_argument(
        "--unused-only",
        action="store_true",
        help="Only keys whose owner has no recorded spend.",
    )
    parser.add_argument("--limit", type=int, help="Process at most this many keys.")
    parser.add_argument(
        "--delete-users",
        action="store_true",
        help="Also delete the trial user once their last key is gone.",
    )
    parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt."
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=10,
        help="Stop after this many failures (default 10). Repeated failures "
        "usually mean an endpoint is down, not a bad key.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N keys (default 100).",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
