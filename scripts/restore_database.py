#!/usr/bin/env python3
"""
Restore a PostgreSQL database from a Lagoon backup (.tar.gz).

Lagoon backups are .tar.gz files containing a pg_dump directory-format archive:
  .tar.gz -> .tar -> directory with (*.dat files, toc.dat, restore.sql)

This script:
  1. (Optionally) takes a pre-restore safety backup with `pg_dump -Fc`.
  2. Drops and recreates the target database via `dropdb` / `createdb`.
  3. Extracts the .tar.gz (and the nested .tar) to a temp directory.
  4. Applies the dump with `pg_restore -Fd --no-owner --no-privileges`.

Requires the postgres client tools (pg_dump, pg_restore, dropdb, createdb,
psql) on PATH. In Lagoon these are provided by the `cli` container image.

IMPORTANT: Only use this with backups from trusted sources (e.g. Lagoon).

Usage:
    python scripts/restore_database.py /path/to/backup.tar.gz
"""

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import unquote, urlparse


REQUIRED_TOOLS = ("pg_dump", "pg_restore", "dropdb", "createdb", "psql")


def sanitize(msg, config):
    """Remove sensitive data (password) from messages."""
    if not msg:
        return msg
    pw = config.get("password")
    if pw:
        msg = msg.replace(pw, "****")
    return msg


def get_db_config():
    """Parse DATABASE_URL into connection components."""
    database_url = os.getenv(
        "DATABASE_URL", "postgres://postgres:postgres@postgres:5432/postgres_service"
    )
    parsed = urlparse(database_url)
    db_name = unquote(parsed.path or "").removeprefix("/")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": unquote(parsed.username) if parsed.username is not None else None,
        "password": unquote(parsed.password) if parsed.password is not None else None,
        "database": db_name,
    }


def pg_env(config):
    """Build an environment dict with libpq connection variables set."""
    env = os.environ.copy()
    if config.get("host"):
        env["PGHOST"] = str(config["host"])
    if config.get("port"):
        env["PGPORT"] = str(config["port"])
    if config.get("user"):
        env["PGUSER"] = str(config["user"])
    if config.get("password"):
        env["PGPASSWORD"] = str(config["password"])
    # libpq picks up these on its own; no need to pass them as CLI flags.
    return env


def check_required_tools():
    """Verify all required postgres client tools are on PATH."""
    missing = [t for t in REQUIRED_TOOLS if shutil.which(t) is None]
    if missing:
        print(f"Error: Required postgres client tool(s) not found: {', '.join(missing)}")
        print("  This script must run in an environment with postgres client tools")
        print("  installed (e.g. the Lagoon `cli` container).")
        sys.exit(1)


def run_pg_tool(cmd, config, *, capture=True, check=True):
    """Run a postgres client command, returning the CompletedProcess.

    Stderr is captured (and sanitized) so passwords don't leak into logs.
    """
    try:
        result = subprocess.run(
            cmd,
            env=pg_env(config),
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise SystemExit(f"Error: {sanitize(str(e), config)}")

    if not capture and result.stderr:
        sys.stderr.write(sanitize(result.stderr, config))

    if check and result.returncode != 0:
        stderr = sanitize((result.stderr or "").strip(), config)
        raise subprocess.CalledProcessError(
            result.returncode, cmd, output=result.stdout, stderr=stderr
        )
    return result


def backup_current_database(config, backup_path):
    """Run `pg_dump -Fc` against the current database as a safety net.

    Custom format (-Fc) is compact, supports selective restore via pg_restore,
    and faithfully captures everything pg_dump can capture (schemas, tables,
    indexes, views, functions, triggers, sequences, types, extensions, ACLs,
    RLS policies, etc).

    Replay with:  pg_restore -d "$DATABASE_URL" --clean <this_file>.dump
    """
    print("  Backing up current database to a local dump file")

    # Open with restrictive permissions before pg_dump writes to it.
    fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)

    cmd = [
        "pg_dump",
        "--format=custom",
        "--file", backup_path,
        "--dbname", config["database"],
    ]
    try:
        run_pg_tool(cmd, config)
    except subprocess.CalledProcessError as e:
        # Clean up the empty/partial file so we don't leave noise behind.
        try:
            os.unlink(backup_path)
        except OSError as cleanup_err:
            # Best-effort cleanup only: preserve original pg_dump failure path.
            print(f"  Warning: could not remove partial backup file: {cleanup_err}", file=sys.stderr)
        raise SystemExit(f"  pg_dump failed: {e.stderr or e}")

    size_mb = os.path.getsize(backup_path) / (1024 * 1024)
    print(f"  Backup complete ({size_mb:.1f} MB)")
    return backup_path


def drop_and_recreate_database(config):
    """Drop the target database (forcing connections closed) and recreate it."""
    db_name = config["database"]

    if db_name == "postgres":
        raise SystemExit(
            "Error: Cannot drop the 'postgres' maintenance database. "
            "Set DATABASE_URL to point to a different target database."
        )

    print("  Dropping target database (forcing existing connections closed)...")
    # --force terminates active connections (PostgreSQL 13+).
    try:
        run_pg_tool(
            ["dropdb", "--if-exists", "--force", db_name],
            config,
        )
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"  dropdb failed: {e.stderr or e}")

    print("  Creating target database...")
    try:
        run_pg_tool(["createdb", db_name], config)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"  createdb failed: {e.stderr or e}")

    print("  Database recreated.")


def safe_extract_tar(tar, extract_dir):
    """Extract tar members, validating paths stay within extract_dir."""
    extract_path = pathlib.Path(extract_dir).resolve()
    safe_members = []
    for member in tar.getmembers():
        if not (member.isreg() or member.isdir()):
            raise ValueError(
                f"Only regular files/directories are allowed in archive: {member.name}"
            )
        member_path = (extract_path / member.name).resolve()
        if not member_path.is_relative_to(extract_path):
            raise ValueError(f"Path traversal detected in archive: {member.name}")
        safe_members.append(member)
    tar.extractall(path=extract_dir, members=safe_members)


def _find_dump_dir(root):
    """Locate a pg_dump directory-format dump under `root`.

    A directory-format dump is a directory containing a `toc.dat` file (and
    typically a number of `*.dat` files). We accept either `root` itself or
    a single-level subdirectory.
    """
    if os.path.isfile(os.path.join(root, "toc.dat")):
        return root
    for entry in os.listdir(root):
        sub = os.path.join(root, entry)
        if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "toc.dat")):
            return sub
    return None


def extract_backup(backup_path, extract_dir):
    """Extract .tar.gz (and any nested .tar) to `extract_dir`.

    Returns the path to the directory containing `toc.dat` (the pg_dump
    directory-format archive root). Exits if it cannot be located.
    """
    print(f"  Extracting {backup_path}...")

    with tarfile.open(backup_path, "r:gz") as tar:
        safe_extract_tar(tar, extract_dir)

    # The .tar.gz typically contains a nested .tar; unwrap it if present.
    for entry in os.listdir(extract_dir):
        entry_path = os.path.join(extract_dir, entry)
        if entry.endswith(".tar") and tarfile.is_tarfile(entry_path):
            nested_dir = os.path.join(extract_dir, "dump")
            os.makedirs(nested_dir, exist_ok=True)
            with tarfile.open(entry_path, "r:") as tar:
                safe_extract_tar(tar, nested_dir)
            dump_dir = _find_dump_dir(nested_dir)
            if dump_dir:
                return dump_dir
            print("  Error: Could not locate toc.dat in nested archive.")
            print(f"  Nested contents: {os.listdir(nested_dir)}")
            sys.exit(1)

    dump_dir = _find_dump_dir(extract_dir)
    if dump_dir:
        return dump_dir

    print("  Error: Could not locate toc.dat in archive.")
    print(f"  Top-level contents: {os.listdir(extract_dir)}")
    sys.exit(1)


def apply_restore(config, dump_dir):
    """Apply the database restore from a pg_dump directory-format archive.

    Uses `pg_restore -Fd --no-owner --no-privileges` so the restore runs as
    the current connection user without requiring superuser privileges or
    the original owner roles to exist. `--exit-on-error` makes pg_restore
    fail fast on the first error rather than logging warnings and pressing on.
    """
    cmd = [
        "pg_restore",
        "--format=directory",
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        "--dbname", config["database"],
        dump_dir,
    ]
    try:
        run_pg_tool(cmd, config, capture=False)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"  pg_restore failed: {e.stderr or e}\n"
            "  The database has been dropped and is now empty. "
            "Re-run the restore or recover from the safety backup with:\n"
            "    pg_restore -d \"$DATABASE_URL\" --clean <safety_backup>.dump"
        )

    print("  Restore complete.")


def check_disk_space(path, required_mb=500):
    """Check if there's enough disk space at the given path."""
    stat = os.statvfs(path)
    available_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
    if available_mb < required_mb:
        print(f"  Warning: Only {available_mb:.0f}MB available at {path} (recommend >= {required_mb}MB)")
        return False
    return True


def _presence(value):
    """Display whether a connection field is set without logging its value."""
    return "(set)" if value else "(not set)"


def main():
    parser = argparse.ArgumentParser(
        description="Restore a PostgreSQL database from a Lagoon .tar.gz backup."
    )
    parser.add_argument(
        "backup_file",
        help="Path to the .tar.gz backup file",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip creating a backup of the current database before restoring",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="Directory to store the pre-restore safety backup (default: next to backup file)",
    )
    parser.add_argument(
        "--extract-dir",
        default=None,
        help="Directory to extract the backup into (default: system temp directory)",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    args = parser.parse_args()

    check_required_tools()

    # Validate backup file exists and is .tar.gz
    if not os.path.exists(args.backup_file):
        print(f"Error: Backup file not found: {args.backup_file}")
        sys.exit(1)

    if not args.backup_file.endswith(".tar.gz"):
        print(f"Error: Backup file must be a .tar.gz file: {args.backup_file}")
        sys.exit(1)

    # Validate extract dir if provided, otherwise check tmp is writable
    if args.extract_dir:
        if not os.path.isdir(args.extract_dir):
            print(f"Error: Extract directory does not exist: {args.extract_dir}")
            sys.exit(1)
        if not os.access(args.extract_dir, os.W_OK):
            print(f"Error: Extract directory is not writable: {args.extract_dir}")
            sys.exit(1)
        extract_base = args.extract_dir
    else:
        extract_base = tempfile.gettempdir()
        if not os.access(extract_base, os.W_OK):
            print(f"Error: Temp directory is not writable: {extract_base}")
            print("  Use --extract-dir to specify an alternative directory.")
            sys.exit(1)

    # Check disk space (estimate: backup file * 3 for extraction headroom)
    backup_size_mb = os.path.getsize(args.backup_file) / (1024 * 1024)
    recommended_mb = max(500, int(backup_size_mb * 3))
    if not check_disk_space(extract_base, recommended_mb):
        if not args.yes:
            response = input("  Continue anyway? [yes/no]: ").strip().lower()
            if response not in ("yes", "y"):
                print("Aborted.")
                sys.exit(0)

    config = get_db_config()

    print("\nDatabase restore configuration:")
    print(f"  Backup file : {args.backup_file}")
    print(f"  Target host : {_presence(config.get('host'))}")
    print(f"  Target DB   : {_presence(config.get('database'))}")
    print(f"  User        : {_presence(config.get('user'))}")
    print()

    if not args.yes:
        print("WARNING: This will DROP the existing database and restore from backup.")
        if not args.no_backup:
            print("A safety backup of the current database will be created first.")
        else:
            print("No safety backup will be created (--no-backup specified).")
        response = input("\nAre you sure you want to proceed? [yes/no]: ").strip().lower()
        if response not in ("yes", "y"):
            print("Aborted.")
            sys.exit(0)

    tmpdir = tempfile.mkdtemp(prefix="db_restore_", dir=args.extract_dir)
    os.chmod(tmpdir, 0o700)

    try:
        # Step 1: Backup current database (unless skipped)
        if not args.no_backup:
            print("\n[1/4] Backing up current database...")
            backup_dir = args.backup_dir or os.path.dirname(os.path.abspath(args.backup_file))
            os.makedirs(backup_dir, exist_ok=True)
            safety_backup_path = os.path.join(
                backup_dir, f"{config['database']}_pre_restore.dump"
            )
            # Avoid overwriting an existing backup
            if os.path.exists(safety_backup_path):
                base, ext = os.path.splitext(safety_backup_path)
                i = 1
                while os.path.exists(f"{base}_{i}{ext}"):
                    i += 1
                safety_backup_path = f"{base}_{i}{ext}"

            try:
                backup_current_database(config, safety_backup_path)
                print("  Safety backup created successfully.")
            except SystemExit as exc:
                print(f"  {exc}", file=sys.stderr)
                if args.yes:
                    print("  Error: Cannot proceed without safety backup in non-interactive mode.")
                    print("  Use --no-backup to explicitly skip the safety backup.")
                    sys.exit(1)
                response = input("  Continue without backup? [yes/no]: ").strip().lower()
                if response not in ("yes", "y"):
                    print("Aborted.")
                    sys.exit(0)
        else:
            print("\n[1/4] Skipping backup (--no-backup)")

        # Step 2: Extract the backup archive
        print("\n[2/4] Extracting backup archive...")
        extract_dir = os.path.join(tmpdir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        dump_dir = extract_backup(args.backup_file, extract_dir)
        dump_contents = os.listdir(dump_dir)
        dat_count = sum(1 for f in dump_contents if f.endswith(".dat") and f != "toc.dat")
        print(f"  Dump directory: {dump_dir}")
        print(f"  Found: toc.dat + {dat_count} data files")

        # Step 3: Drop and recreate the database
        print("\n[3/4] Dropping and recreating database...")
        drop_and_recreate_database(config)

        # Step 4: Apply the restore
        print("\n[4/4] Applying database restore...")
        apply_restore(config, dump_dir)

        print("\nRestore complete!")

    finally:
        # Clean up temp extraction directory
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
