#!/usr/bin/env python3
"""
One-time backfill: revoke PUBLIC CONNECT on existing tenant vector databases.

PostgreSQL grants CONNECT to PUBLIC on every new database, and every tenant role
is a member of PUBLIC — so on a shared vectordb cluster that default lets any
tenant role open a session against any other tenant's database.
`PostgresManager.create_database` now revokes it at provisioning time; this
script applies the same change to databases created before that.

For each tracked vector DB it runs (in this order, so an interrupted run never
locks a tenant out of its own database):

    GRANT CONNECT ON DATABASE db_x TO user_x;
    REVOKE CONNECT ON DATABASE db_x FROM PUBLIC;

Tenant databases present on the cluster but not tracked by the platform (failed
deletes) are reported, not modified — dropping or locking them is an operator
decision.

Usage:
    python scripts/lock_vector_db_connect.py --dry-run
    python scripts/lock_vector_db_connect.py
    python scripts/lock_vector_db_connect.py --region eu-central-1
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.database import SessionLocal
from app.db.models import DBPrivateAIKey, DBRegion
from app.db.postgres import PostgresManager


def get_vector_dbs_grouped_by_region(session, region_name: str | None):
    """Return (region, [(database_name, database_username), ...]) pairs."""
    regions = {
        r.id: r
        for r in session.query(DBRegion)
        .filter(DBRegion.postgres_host.isnot(None))
        .all()
        if region_name is None or r.name == region_name
    }

    keys_by_region = defaultdict(list)
    rows = (
        session.query(
            DBPrivateAIKey.region_id,
            DBPrivateAIKey.database_name,
            DBPrivateAIKey.database_username,
        )
        .filter(DBPrivateAIKey.database_name.isnot(None))
        .filter(DBPrivateAIKey.database_username.isnot(None))
        .filter(DBPrivateAIKey.region_id.in_(regions.keys()))
        .all()
    )
    for region_id, database_name, database_username in rows:
        keys_by_region[region_id].append((database_name, database_username))

    # every region, not just ones with tracked keys — a region whose keys were all
    # deleted without dropping their databases still needs its orphans reported
    return [(region, keys_by_region.get(rid, [])) for rid, region in regions.items()]


async def run(dry_run: bool, region_name: str | None) -> int:
    session = SessionLocal()
    try:
        work = get_vector_dbs_grouped_by_region(session, region_name)
    finally:
        session.close()

    total_locked = 0
    total_failed = 0
    total_untracked = 0

    for region, databases in work:
        manager = PostgresManager(region=region)
        tracked = {name for name, _ in databases}

        try:
            on_cluster = set(await manager.list_tenant_databases())
        except Exception as e:
            print(f"[FAIL] region={region.name} could not list databases: {e}")
            total_failed += 1
            continue

        untracked = sorted(on_cluster - tracked)
        if untracked:
            total_untracked += len(untracked)
            print(
                f"[WARN] region={region.name} {len(untracked)} untracked tenant "
                f"database(s) still allow PUBLIC CONNECT (not modified): "
                f"{', '.join(untracked)}"
            )

        for database_name, database_username in sorted(databases):
            if database_name not in on_cluster:
                print(f"[SKIP] region={region.name} db={database_name} not on cluster")
                continue
            if dry_run:
                print(
                    f"[DRY-RUN] region={region.name} db={database_name} -> "
                    f"CONNECT only for {database_username}"
                )
                continue
            try:
                await manager.restrict_connect_to_owner(
                    database_name, database_username
                )
                total_locked += 1
                print(f"[OK] region={region.name} db={database_name}")
            except Exception as e:
                total_failed += 1
                print(f"[FAIL] region={region.name} db={database_name} error={e}")

    print(
        f"Done. locked={total_locked} failed={total_failed} "
        f"untracked={total_untracked} dry_run={dry_run}"
    )
    return 0 if total_failed == 0 else 1


def main():
    parser = argparse.ArgumentParser(
        description="Revoke PUBLIC CONNECT on existing tenant vector databases"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned changes without applying them",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="Only process this region name (default: all regions)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.dry_run, args.region)))


if __name__ == "__main__":
    main()
