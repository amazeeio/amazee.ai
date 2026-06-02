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
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import quote, unquote, urlparse, urlunparse


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
    maintenance_url = urlunparse(
        parsed._replace(netloc=safe_netloc, path="/postgres")
    )
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

    # Pass the password-stripped DATABASE_URL via --dbname so libpq parses
    # every param (sslmode, connect_timeout, socket host=..., etc.) instead
    # of relying on the four fields we extracted in get_db_config(). The
    # password is supplied via PGPASSWORD in pg_env() so it never appears on
    # argv / in `ps` output.
    cmd = [
        "pg_dump",
        "--format=custom",
        "--file", backup_path,
        "--dbname", config["url"],
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


def apply_restore(config, dump_dir):
    """Apply the database restore from a pg_dump directory-format archive.

    Connects to the `postgres` maintenance DB and lets pg_restore itself
    drop and recreate the target with the source's recorded metadata
    (encoding, collation, locale provider) replayed from `toc.dat`.

    Flag rationale:
      --create / --clean / --if-exists
          Replay the source's `CREATE DATABASE` (preserving encoding and
          collation, fixing the cluster-defaults correctness gap that a
          plain `createdb` would introduce). `--clean --if-exists` makes
          the preceding `DROP DATABASE` idempotent so re-running the
          script is safe even when the target does not exist.
      --no-owner / --no-privileges
          Skip OWNER and GRANT clauses (both for the database itself and
          for objects inside it), so the restore does not require the
          source's roles to exist on the target cluster. The restoring
          role takes ownership.
      --no-tablespaces
          Strip TABLESPACE clauses, so the restore does not require the
          source's tablespaces to exist on the target cluster (the DB
          and its objects land in the default tablespace).
      --exit-on-error
          Fail fast on the first error rather than logging warnings and
          continuing with a half-restored database.
    """
    db_name = config["database"]

    # Refuse to wipe the maintenance DB. (`pg_restore --clean --create`
    # would happily DROP DATABASE postgres if asked.)
    if db_name == "postgres":
        raise SystemExit(
            "Error: Cannot restore over the 'postgres' maintenance database. "
            "Set DATABASE_URL to point to a different target database."
        )

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
        # Connect to the maintenance DB so pg_restore can drop/create the
        # target. `maintenance_url` carries every libpq param from the
        # configured DATABASE_URL (sslmode, connect_timeout, etc.).
        "--dbname", config["maintenance_url"],
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
            "      -d \"<maintenance DATABASE_URL, path=/postgres>\" "
            "<safety_backup>.dump"
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
            print("\n[1/3] Backing up current database...")
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
            print("\n[1/3] Skipping backup (--no-backup)")

        # Step 2: Extract the backup archive
        print("\n[2/3] Extracting backup archive...")
        extract_dir = os.path.join(tmpdir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        dump_dir = extract_backup(args.backup_file, extract_dir)
        dump_contents = os.listdir(dump_dir)
        dat_count = sum(1 for f in dump_contents if f.endswith(".dat") and f != "toc.dat")
        print(f"  Dump directory: {dump_dir}")
        print(f"  Found: toc.dat + {dat_count} data files")

        # Step 3: Apply the restore. pg_restore --create --clean handles
        # the drop/recreate so the target DB is rebuilt with the source's
        # encoding/collation rather than cluster defaults.
        print("\n[3/3] Applying database restore...")
        apply_restore(config, dump_dir)

        print("\nRestore complete!")

    finally:
        # Clean up temp extraction directory
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
