#!/usr/bin/env python3
"""
Restore a PostgreSQL database from a Lagoon backup (.tar.gz).

Lagoon backups are .tar.gz files containing a pg_dump directory-format archive:
  .tar.gz -> .tar -> directory with (*.dat files, toc.dat, restore.sql)

This script:
  1. (Optionally) takes a pre-restore safety backup with `pg_dump -Fc`.
  2. Extracts the .tar.gz (and the nested .tar) to a temp directory.
  3. Applies the dump with
     `pg_restore -Fd --create --clean --if-exists --no-owner --no-privileges
                 --no-tablespaces --exit-on-error`
     connected to the `postgres` maintenance database. `--create --clean`
     replays the source DB's own `CREATE DATABASE` from `toc.dat`, so the
     target is recreated with the source's encoding and collation rather
     than the cluster's defaults. `--no-owner` and `--no-tablespaces` strip
     out OWNER/TABLESPACE clauses so the restore does not depend on the
     source's roles or tablespaces existing on the target cluster.

Requires the postgres client tools (pg_dump, pg_restore, psql) on PATH. In
Lagoon these are provided by the `cli` container image.

The connecting role needs CREATEDB (to recreate the target database) and
must be able to connect to the `postgres` maintenance database.

IMPORTANT: Only use this with backups from trusted sources (e.g. Lagoon).

Usage:
    python scripts/restore_database.py /path/to/backup.tar.gz
"""

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import quote, unquote, urlparse, urlunparse


def validate_db_name(name):
    """Validate that a database name contains only safe identifier characters (alphanumeric, underscores, and hyphens)."""
    if not name:
        return False
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", name))


REQUIRED_TOOLS = ("pg_dump", "pg_restore", "psql")


def sanitize(msg, config):
    """Remove sensitive data (password) from messages.

    The password may appear verbatim (from PGPASSWORD-style logs) or
    URL-encoded inside the DATABASE_URL that we now pass on argv via
    `--dbname` / `--maintenance-db`. Strip both forms.
    """
    if not msg:
        return msg
    msg = str(msg)
    pw = config.get("password")
    if pw:
        msg = msg.replace(pw, "****")
        encoded_pw = quote(pw, safe="")
        if encoded_pw and encoded_pw != pw:
            msg = msg.replace(encoded_pw, "****")
    return msg


def _strip_password_from_netloc(parsed):
    """Return a netloc string with the password component removed.

    Keeps the username (if any) and host[:port] intact, preserving the
    original percent-encoding of the username so we don't double-encode
    (`urlparse` returns raw, percent-encoded `username` / `password`).

    The password is stripped because `url` / `maintenance_url` are passed
    on argv via `--dbname`, where they would otherwise be visible in `ps`
    / `/proc/<pid>/cmdline`. The password is supplied to libpq through
    PGPASSWORD instead (see `pg_env`).
    """
    netloc = parsed.netloc
    # Split off any userinfo ("user[:password]@host[:port]"). Use rsplit so
    # an `@` in the password (which is illegal unencoded but defensive) is
    # handled by taking the *last* `@` as the userinfo/host boundary.
    if "@" not in netloc:
        return netloc
    userinfo, _, hostport = netloc.rpartition("@")
    # Drop the password portion of "user:password" (if present); keep the
    # username's original percent-encoding verbatim.
    raw_user = userinfo.split(":", 1)[0]
    if not raw_user:
        return hostport
    return f"{raw_user}@{hostport}"


def get_db_config():
    """Parse DATABASE_URL into connection components.

    `url` and `maintenance_url` are handed to libpq via `--dbname` /
    `--maintenance-db` so any libpq-supported parameters (sslmode,
    connect_timeout, host=/var/run/..., etc.) flow through unchanged. The
    password is stripped from these URLs before they hit argv to avoid
    leaking the credential into `ps` / `/proc/<pid>/cmdline`; libpq picks
    it up from PGPASSWORD via `pg_env`. Parsed fields are kept only for
    local needs:
      - `database` for the safety guard, the dropdb/createdb target, and the
        backup filename;
      - `password` for the log-sanitizer and PGPASSWORD (so it never lands in
        argv);
      - `host` / `user` for the diagnostic presence printout.

    Also derives `maintenance_url`, a URL with the path swapped to
    `/postgres`, so `dropdb`/`createdb` can connect to a different database
    than the one being dropped/created while inheriting all other params.
    """
    database_url = os.getenv(
        "DATABASE_URL", "postgres://postgres:postgres@postgres:5432/postgres_service"
    )
    parsed = urlparse(database_url)
    db_name = unquote(parsed.path or "").removeprefix("/")
    # Strip the password from the netloc so neither `url` nor
    # `maintenance_url` carry the credential when passed on argv via
    # `--dbname`. The password flows through PGPASSWORD instead.
    safe_netloc = _strip_password_from_netloc(parsed)
    safe_url = urlunparse(parsed._replace(netloc=safe_netloc))
    maintenance_url = urlunparse(parsed._replace(netloc=safe_netloc, path="/postgres"))
    return {
        "url": safe_url,
        "maintenance_url": maintenance_url,
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": unquote(parsed.username) if parsed.username is not None else None,
        "password": unquote(parsed.password) if parsed.password is not None else None,
        "database": db_name,
    }


def pg_env(config):
    """Build an environment dict with PGPASSWORD set.

    All other connection parameters are passed via the libpq URL on the
    command line (`--dbname` / `--maintenance-db`), so libpq parses any
    extra params (sslmode, connect_timeout, socket host=..., etc.) verbatim.
    PGPASSWORD is kept in the environment rather than the URL to avoid
    leaking the password into argv / process listings.
    """
    env = os.environ.copy()
    if config.get("password"):
        env["PGPASSWORD"] = str(config["password"])
    return env


def check_required_tools():
    """Verify all required postgres client tools are on PATH."""
    missing = [t for t in REQUIRED_TOOLS if shutil.which(t) is None]
    if missing:
        print(
            f"Error: Required postgres client tool(s) not found: {', '.join(missing)}"
        )
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

    # Pass the password-stripped DATABASE_URL via --dbname so libpq parses
    # every param (sslmode, connect_timeout, socket host=..., etc.) instead
    # of relying on the four fields we extracted in get_db_config(). The
    # password is supplied via PGPASSWORD in pg_env() so it never appears on
    # argv / in `ps` output.
    cmd = [
        "pg_dump",
        "--format=custom",
        "--file",
        backup_path,
        "--dbname",
        config["url"],
    ]
    try:
        run_pg_tool(cmd, config)
    except subprocess.CalledProcessError as e:
        # Clean up the empty/partial file so we don't leave noise behind.
        try:
            os.unlink(backup_path)
        except OSError as cleanup_err:
            # Best-effort cleanup only: preserve original pg_dump failure path.
            print(
                f"  Warning: could not remove partial backup file: {cleanup_err}",
                file=sys.stderr,
            )
        raise SystemExit(f"  pg_dump failed: {sanitize(e.stderr or e, config)}")

    size_mb = os.path.getsize(backup_path) / (1024 * 1024)
    print(f"  Backup complete ({size_mb:.1f} MB)")
    return backup_path


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


def detect_database_name(dump_dir, config):
    """Run pg_restore -l to find the database name in the TOC header."""
    cmd = ["pg_restore", "-l", dump_dir]
    try:
        result = run_pg_tool(cmd, config, capture=True)
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if re.match(r"^;\s+dbname:", stripped):
                return stripped.split(":", 1)[1].strip()
    except Exception as e:
        print(
            sanitize(
                f"  Warning: Could not detect source database name from backup: {e}",
                config,
            )
        )
    return None


def recreate_target_db(config):
    """Drop and recreate the target database to ensure a clean restore without constraint errors."""
    db_name = config["database"]
    if not validate_db_name(db_name):
        raise SystemExit(
            "Error: Database name is invalid. Only alphanumeric characters and underscores are allowed."
        )

    print("  Recreating target database...")

    # 1. Terminate other connections to target database (best effort)
    terminate_cmd = [
        "psql",
        "--dbname",
        config["maintenance_url"],
        "-v",
        f"target_db={db_name}",
        "-tAc",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :'target_db' AND pid <> pg_backend_pid()",
    ]
    try:
        run_pg_tool(terminate_cmd, config, check=False)
    except subprocess.CalledProcessError:
        warning_msg = "  Warning: Could not terminate all active connections for database (continuing)."
        print(warning_msg)

    # 2. Drop database if exists
    drop_cmd = [
        "psql",
        "--dbname",
        config["maintenance_url"],
        "-c",
        f'DROP DATABASE IF EXISTS "{db_name}"',
    ]
    try:
        run_pg_tool(drop_cmd, config)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            sanitize(f"  Failed to drop target database: {e.stderr or e}", config)
        )

    # 3. Create database
    create_cmd = [
        "psql",
        "--dbname",
        config["maintenance_url"],
        "-c",
        f'CREATE DATABASE "{db_name}"',
    ]
    try:
        run_pg_tool(create_cmd, config)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            sanitize(
                f"  Failed to create target database '{db_name}': {e.stderr or e}",
                config,
            )
        )


def apply_restore(config, dump_dir, restore_mode="target"):
    """Apply the database restore from a pg_dump directory-format archive.

    If restore_mode is 'as-is':
      Connects to the `postgres` maintenance DB and lets pg_restore itself
      drop and recreate the target with the source's recorded metadata
      (encoding, collation, locale provider) replayed from `toc.dat`.

    If restore_mode is 'target':
      Recreates the configured local target database (to ensure it is clean
      and avoids foreign key constraint errors) and runs pg_restore directly
      into the target database without --create.
    """
    db_name = config["database"]

    # Refuse to wipe the maintenance DB.
    if db_name == "postgres":
        raise SystemExit(
            "Error: Cannot restore over the 'postgres' maintenance database. "
            "Set DATABASE_URL to point to a different target database."
        )

    if restore_mode == "as-is":
        cmd = [
            "pg_restore",
            "--format=directory",
            "--create",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--no-tablespaces",
            "--exit-on-error",
            "--dbname",
            config["maintenance_url"],
            dump_dir,
        ]
    else:
        # Recreate the target database to ensure constraint-free restore
        recreate_target_db(config)

        cmd = [
            "pg_restore",
            "--format=directory",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--no-tablespaces",
            "--exit-on-error",
            "--dbname",
            config["url"],
            dump_dir,
        ]

    try:
        run_pg_tool(cmd, config, capture=False)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"  pg_restore failed: {sanitize(e.stderr or e, config)}\n"
            "  The target database may have been dropped or partially "
            "restored. Recover from the safety backup with:\n"
            "    pg_restore --clean --create --if-exists --no-owner "
            "--no-privileges \\\n"
            '      -d "<maintenance DATABASE_URL, path=/postgres>" '
            "<safety_backup>.dump"
        )

    print("  Restore complete.")


def check_disk_space(path, required_mb=500):
    """Check if there's enough disk space at the given path."""
    stat = os.statvfs(path)
    available_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
    if available_mb < required_mb:
        print(
            f"  Warning: Only {available_mb:.0f}MB available at {path} (recommend >= {required_mb}MB)"
        )
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
        "--restore-mode",
        choices=["target", "as-is"],
        default="target",
        help="Whether to restore directly into the configured local target database ('target') or recreate the original database name from the dump ('as-is')",
    )
    parser.add_argument(
        "--yes",
        "-y",
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
    target_db = config["database"]
    if not validate_db_name(target_db):
        print(
            "Error: Target database name is invalid. Only alphanumeric characters and underscores are allowed."
        )
        sys.exit(1)

    display_host = _presence(config.get("host"))
    display_target_db = _presence(target_db)
    display_user = _presence(config.get("user"))

    print("\nDatabase restore configuration:")
    print(f"  Backup file : {args.backup_file}")
    print(f"  Target host : {display_host}")
    print(f"  Target DB   : {display_target_db}")
    print(f"  User        : {display_user}")
    print()

    tmpdir = tempfile.mkdtemp(prefix="db_restore_", dir=args.extract_dir)
    os.chmod(tmpdir, 0o700)

    try:
        # Step 1: Extract the backup archive
        print("\n[1/3] Extracting backup archive...")
        extract_dir = os.path.join(tmpdir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        dump_dir = extract_backup(args.backup_file, extract_dir)
        dump_contents = os.listdir(dump_dir)
        dat_count = sum(
            1 for f in dump_contents if f.endswith(".dat") and f != "toc.dat"
        )
        print(f"  Dump directory: {dump_dir}")
        print(f"  Found: toc.dat + {dat_count} data files")

        # Detect source database name
        source_db = detect_database_name(dump_dir, config)
        if source_db:
            print("  Detected source database name in backup.")
        else:
            print("  Could not detect source database name in backup.")

        # Determine restore mode (target vs as-is)
        restore_mode = args.restore_mode
        if not args.yes:
            if source_db and source_db != target_db:
                print(
                    "\nWARNING: The backup source database name differs from your locally-configured target database."
                )
                print("How would you like to restore this database?")
                print(
                    "  1) Restore directly into the configured target database (Recommended for local dev - no .env changes needed)"
                )
                print(
                    "  2) Recreate and restore using the backup's original database name (Requires updating DATABASE_URL in .env)"
                )
                while True:
                    choice = input("\nSelect option [1 or 2]: ").strip()
                    if choice == "1":
                        restore_mode = "target"
                        break
                    elif choice == "2":
                        restore_mode = "as-is"
                        break
                    else:
                        print("Invalid choice. Please enter 1 or 2.")
            else:
                print(
                    "\nWARNING: This will DROP the existing database and restore from backup."
                )
                if not args.no_backup:
                    print(
                        "A safety backup of the current database will be created first."
                    )
                else:
                    print("No safety backup will be created (--no-backup specified).")
                response = (
                    input("\nAre you sure you want to proceed? [yes/no]: ")
                    .strip()
                    .lower()
                )
                if response not in ("yes", "y"):
                    print("Aborted.")
                    sys.exit(0)
        else:
            # Non-interactive mode
            if restore_mode == "as-is":
                print(
                    "  Non-interactive: Restoring as-is into configured target database"
                )
            else:
                print("  Non-interactive: Restoring into target database")

        # Validate source_db ONLY if we are actually using it in as-is mode
        if restore_mode == "as-is" and source_db:
            if not validate_db_name(source_db):
                print(
                    "Error: Detected source database name is invalid for 'as-is' restore."
                )
                print(
                    "Only alphanumeric characters and underscores are allowed for database names."
                )
                sys.exit(1)

        # Step 2: Backup current database (unless skipped)
        if not args.no_backup:
            active_db = (
                source_db if (restore_mode == "as-is" and source_db) else target_db
            )
            backup_msg = "\n[2/3] Backing up current database before restore..."
            print(backup_msg)
            backup_dir = args.backup_dir or os.path.dirname(
                os.path.abspath(args.backup_file)
            )
            os.makedirs(backup_dir, exist_ok=True)

            # Create a temporary config for the active database to backup
            backup_config = config.copy()
            backup_config["database"] = active_db
            parsed_url = urlparse(config["url"])
            backup_config["url"] = urlunparse(parsed_url._replace(path=f"/{active_db}"))

            safety_backup_path = os.path.join(
                backup_dir, f"{active_db}_pre_restore.dump"
            )
            if os.path.exists(safety_backup_path):
                base, ext = os.path.splitext(safety_backup_path)
                i = 1
                while os.path.exists(f"{base}_{i}{ext}"):
                    i += 1
                safety_backup_path = f"{base}_{i}{ext}"

            try:
                # Check if the database exists before trying to backup
                check_cmd = [
                    "psql",
                    "--dbname",
                    config["maintenance_url"],
                    "-v",
                    f"active_db={active_db}",
                    "-tAc",
                    "SELECT 1 FROM pg_database WHERE datname=:'active_db'",
                ]
                db_exists = False
                try:
                    res = run_pg_tool(check_cmd, config)
                    db_exists = res.stdout.strip() == "1"
                except Exception:
                    print(
                        "  Warning: Could not verify whether the target database exists. Assuming it does not exist and skipping safety backup.",
                        file=sys.stderr,
                    )
                    db_exists = False

                if db_exists:
                    backup_current_database(backup_config, safety_backup_path)
                    print("  Safety backup created successfully.")
                else:
                    print("  Skipping backup: Target database does not exist yet.")
            except SystemExit as exc:
                print(f"  {exc}", file=sys.stderr)
                if args.yes:
                    print(
                        "  Error: Cannot proceed without safety backup in non-interactive mode."
                    )
                    print("  Use --no-backup to explicitly skip the safety backup.")
                    sys.exit(1)
                response = (
                    input("  Continue without backup? [yes/no]: ").strip().lower()
                )
                if response not in ("yes", "y"):
                    print("Aborted.")
                    sys.exit(0)
        else:
            print("\n[2/3] Skipping backup (--no-backup)")

        # Step 3: Apply the restore
        print("\n[3/3] Applying database restore...")
        apply_restore(config, dump_dir, restore_mode=restore_mode)
        print("\nRestore complete!")

        if restore_mode == "as-is" and source_db and source_db != target_db:
            print(
                "\nIMPORTANT: To connect your application to this database, update DATABASE_URL in your .env file."
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
