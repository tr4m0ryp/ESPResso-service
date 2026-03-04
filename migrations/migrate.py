"""Migration runner for ESPResso-related Avelero database changes.

Connects directly to the Avelero Supabase PostgreSQL database and
applies migration files in order. Tracks applied migrations in a
dedicated table to avoid re-running.

Usage:
    python -m migrations.migrate                  # Apply all pending
    python -m migrations.migrate --status         # Show migration status
    python -m migrations.migrate --rollback N     # Rollback migration N

Requires DATABASE_URL environment variable pointing to Avelero's
PostgreSQL instance.
"""

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Load .env file from project root if it exists
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value

try:
    import psycopg2
except ImportError:
    print(
        "psycopg2 not installed. Install it with:\n"
        "  pip install psycopg2-binary"
    )
    sys.exit(1)


MIGRATIONS_DIR = Path(__file__).parent
MIGRATIONS_TABLE = "espresso_migrations"


def get_connection():
    """Connect to the Avelero database."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL environment variable is required.")
        print("Set it to your Avelero Supabase PostgreSQL connection string.")
        print("Example: postgresql://postgres:password@localhost:54322/postgres")
        sys.exit(1)
    return psycopg2.connect(url)


def ensure_migrations_table(conn):
    """Create the migrations tracking table if it does not exist."""
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
                id SERIAL PRIMARY KEY,
                filename TEXT NOT NULL UNIQUE,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                rolled_back_at TIMESTAMPTZ
            )
        """)
    conn.commit()


def get_applied_migrations(conn):
    """Return set of applied migration filenames."""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT filename FROM {MIGRATIONS_TABLE}
            WHERE rolled_back_at IS NULL
            ORDER BY filename
        """)
        return {row[0] for row in cur.fetchall()}


def get_pending_migrations():
    """Return sorted list of (filename, up_path, down_path) tuples."""
    migrations = []
    for f in sorted(MIGRATIONS_DIR.glob("*.up.sql")):
        name = f.name.replace(".up.sql", "")
        down = f.with_name(f"{name}.down.sql")
        migrations.append((name, f, down if down.exists() else None))
    return migrations


def file_checksum(path):
    """SHA-256 checksum of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def apply_migration(conn, name, up_path):
    """Apply a single migration."""
    sql = up_path.read_text()
    checksum = file_checksum(up_path)

    print(f"  Applying: {name}")
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            f"INSERT INTO {MIGRATIONS_TABLE} (filename, checksum) "
            f"VALUES (%s, %s)",
            (name, checksum),
        )
    conn.commit()
    print(f"  Applied:  {name}")


def rollback_migration(conn, name, down_path):
    """Rollback a single migration."""
    if down_path is None or not down_path.exists():
        print(f"  ERROR: No rollback file for {name}")
        return False

    sql = down_path.read_text()
    print(f"  Rolling back: {name}")
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            f"UPDATE {MIGRATIONS_TABLE} "
            f"SET rolled_back_at = now() "
            f"WHERE filename = %s AND rolled_back_at IS NULL",
            (name,),
        )
    conn.commit()
    print(f"  Rolled back:  {name}")
    return True


def cmd_apply(conn):
    """Apply all pending migrations."""
    applied = get_applied_migrations(conn)
    pending = get_pending_migrations()

    to_apply = [(n, up, down) for n, up, down in pending if n not in applied]

    if not to_apply:
        print("No pending migrations.")
        return

    print(f"Applying {len(to_apply)} migration(s):\n")
    for name, up_path, _ in to_apply:
        apply_migration(conn, name, up_path)

    print(f"\nDone. {len(to_apply)} migration(s) applied.")


def cmd_status(conn):
    """Show migration status."""
    applied = get_applied_migrations(conn)
    pending = get_pending_migrations()

    print("Migration Status")
    print("=" * 60)

    for name, _, _ in pending:
        status = "APPLIED" if name in applied else "PENDING"
        marker = "[x]" if name in applied else "[ ]"
        print(f"  {marker} {name}  ({status})")

    if not pending:
        print("  No migration files found.")

    print()
    print(f"Applied: {len(applied)}  |  "
          f"Pending: {len([n for n, _, _ in pending if n not in applied])}")


def cmd_rollback(conn, target):
    """Rollback a specific migration by name prefix."""
    pending = get_pending_migrations()
    applied = get_applied_migrations(conn)

    matches = [
        (n, up, down) for n, up, down in pending
        if target in n and n in applied
    ]

    if not matches:
        print(f"No applied migration matching '{target}'.")
        return

    if len(matches) > 1:
        print(f"Multiple matches for '{target}':")
        for n, _, _ in matches:
            print(f"  - {n}")
        print("Be more specific.")
        return

    name, _, down_path = matches[0]
    rollback_migration(conn, name, down_path)


def main():
    parser = argparse.ArgumentParser(
        description="ESPResso database migration runner"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show migration status",
    )
    parser.add_argument(
        "--rollback", type=str, metavar="NAME",
        help="Rollback a specific migration (by name prefix)",
    )
    args = parser.parse_args()

    conn = get_connection()
    ensure_migrations_table(conn)

    try:
        if args.status:
            cmd_status(conn)
        elif args.rollback:
            cmd_rollback(conn, args.rollback)
        else:
            cmd_apply(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
