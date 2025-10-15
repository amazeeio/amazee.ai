#!/usr/bin/env python3
"""
Test script to monitor database connection pool health.
Run this while the application is under load to verify connections are properly managed.
"""

import os
import sys
import time

# Add the parent directory to the path so we can import app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import text
from app.db.database import engine, get_db

def check_pool_status():
    """Check and display current pool status."""
    pool = engine.pool

    print("\n" + "="*60)
    print("DATABASE CONNECTION POOL STATUS")
    print("="*60)
    print(f"Pool size (max connections):     {pool.size()}")
    print(f"Currently checked out:           {pool.checkedout()}")
    print(f"Overflow (extra connections):    {max(0, pool.overflow())}")
    print(f"Max overflow allowed:            {pool._max_overflow}")

    # Calculate total active connections
    total_active = pool.checkedout()
    print(f"Total active connections:        {total_active}")

    # Calculate available
    available = pool.size() + pool._max_overflow - total_active
    print(f"Available connections:           {available}")
    print("="*60)

    # Calculate utilization percentage
    total_available = pool.size() + pool._max_overflow
    in_use = pool.checkedout()
    utilization = (in_use / total_available * 100) if total_available > 0 else 0

    print(f"\nPool Utilization: {utilization:.1f}%")

    if utilization > 80:
        print("⚠️  WARNING: Pool utilization over 80%!")
    elif utilization > 50:
        print("⚡ Pool utilization is moderate")
    else:
        print("✅ Pool utilization is healthy")

    # Check for leaks by looking at long-running connections
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT
                    COUNT(*) as total_connections,
                    COUNT(*) FILTER (WHERE state = 'idle') as idle_connections,
                    COUNT(*) FILTER (WHERE state = 'active') as active_connections,
                    COALESCE(MAX(EXTRACT(EPOCH FROM (now() - query_start))), 0) as longest_query_seconds
                FROM pg_stat_activity
                WHERE datname = current_database()
                AND pid != pg_backend_pid()
            """))
            row = result.fetchone()

            print(f"\nPostgreSQL Connection Stats:")
            print(f"  Total DB connections:     {row[0]}")
            print(f"  Idle connections:         {row[1]}")
            print(f"  Active connections:       {row[2]}")
            print(f"  Longest query (seconds):  {float(row[3]):.2f}")

            if row[1] > (pool.size() + pool._max_overflow):
                print("⚠️  WARNING: More idle connections than pool size - possible leak!")

    except Exception as error:
        print(f"\n⚠️  Could not fetch PostgreSQL stats: {error}")

    return utilization

def monitor_pool_continuous(interval_seconds=5, duration_seconds=60):
    """
    Continuously monitor database connections for leaks.
    Monitors at the PostgreSQL level to see ALL processes (including the running app).
    """
    print(f"\n👀 Monitoring DATABASE connections every {interval_seconds}s for {duration_seconds}s...")
    print("   This shows connections from ALL processes (including the running app)")
    print("   Watch for connections that don't get released (stay 'idle' for long periods)\n")

    print("=" * 80)
    print(f"{'Time':<12} {'Total':<8} {'Active':<8} {'Idle':<8} {'Idle in Txn':<15} {'Max Idle (s)':<12}")
    print("=" * 80)

    start_time = time.time()
    leak_warnings = 0
    max_idle_warnings = 0

    while time.time() - start_time < duration_seconds:
        try:
            with engine.connect() as conn:
                # Get connection statistics from PostgreSQL
                result = conn.execute(text("""
                    SELECT
                        COALESCE(COUNT(*), 0) as total,
                        COALESCE(SUM(CASE WHEN state = 'active' THEN 1 ELSE 0 END), 0) as active,
                        COALESCE(SUM(CASE WHEN state = 'idle' THEN 1 ELSE 0 END), 0) as idle,
                        COALESCE(SUM(CASE WHEN state = 'idle in transaction' THEN 1 ELSE 0 END), 0) as idle_in_transaction,
                        COALESCE(MAX(CASE
                            WHEN state = 'idle'
                            THEN EXTRACT(EPOCH FROM (now() - state_change))
                            ELSE 0
                        END), 0) as max_idle_seconds
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                    AND pid != pg_backend_pid()
                """))
                row = result.fetchone()

                timestamp = time.strftime("%H:%M:%S")
                total = int(row[0]) if row[0] is not None else 0
                active = int(row[1]) if row[1] is not None else 0
                idle = int(row[2]) if row[2] is not None else 0
                idle_in_txn = int(row[3]) if row[3] is not None else 0
                max_idle = float(row[4]) if row[4] is not None else 0.0

                print(f"{timestamp:<12} {total:<8} {active:<8} {idle:<8} {idle_in_txn:<15} {max_idle:<12.1f}")

                # Detect potential leaks
                if idle > 10:
                    leak_warnings += 1
                    if leak_warnings > 3:
                        print(f"⚠️  WARNING: High number of idle connections ({idle}) - possible leak!")
                else:
                    leak_warnings = 0

                # Warn about long-idle connections
                if max_idle > 300:  # 5 minutes
                    max_idle_warnings += 1
                    if max_idle_warnings > 2:
                        print(f"⚠️  WARNING: Connection idle for {max_idle:.0f}s - possible abandoned connection!")
                else:
                    max_idle_warnings = 0

        except Exception as error:
            timestamp = time.strftime("%H:%M:%S")
            print(f"{timestamp:<12} Error querying database: {error}")

        time.sleep(interval_seconds)

    print("=" * 80)
    print("\n✅ Monitoring complete")
    print("\nInterpretation:")
    print("  - 'Active': Currently executing queries (normal during load)")
    print("  - 'Idle': Waiting for next query (normal, should fluctuate)")
    print("  - 'Idle in Txn': In transaction but not executing (possible leak if persists)")
    print("  - 'Max Idle': Longest a connection has been idle (high values = possible leak)")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/test_connection_pool.py status          # Check current pool status")
        print("  python scripts/test_connection_pool.py monitor         # Monitor database connections (all processes)")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "status":
        check_pool_status()
    elif command == "monitor":
        monitor_pool_continuous(interval_seconds=5, duration_seconds=60)
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

