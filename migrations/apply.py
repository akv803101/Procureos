"""Ordered raw-SQL migration runner.

Why this exists (Phase 1 decision — see migrations/README.md):
  bootstrap.md specifies the schema as 15 numbered .sql files that must run in a
  fixed order (FK dependencies). Supabase setup (RLS policies, the auth.users
  table, storage) is raw SQL too. Rather than wrap each file in an Alembic
  Python revision, we run the .sql files directly — the simplest thing that
  works, and the schema stays readable as plain SQL.

What it does:
  1. Connects to SUPABASE_POSTGRES_URL.
  2. Ensures a `schema_migrations(filename, applied_at)` ledger table exists.
  3. Applies every migrations/versions/*.sql NOT yet recorded, in filename order
     (the numeric prefixes 001..015 give the correct order).
  4. Each file runs inside its own transaction; a failure rolls that file back
     and aborts (so a half-applied file never lands).

Usage:
    python -m migrations.apply            # apply all pending
    python -m migrations.apply --status   # show applied vs pending, apply nothing

This needs live Supabase credentials in .env. Without SUPABASE_POSTGRES_URL it
exits with a clear message rather than guessing.
"""
import os
import sys
from pathlib import Path

import psycopg2

VERSIONS_DIR = Path(__file__).parent / "versions"
LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _db_url() -> str:
    url = os.getenv("SUPABASE_POSTGRES_URL")
    if not url:
        sys.exit(
            "SUPABASE_POSTGRES_URL is not set. Fill .env from .env.example with "
            "your Supabase Postgres connection string, then re-run."
        )
    return url


def _sql_files() -> list[Path]:
    # sorted() on the 001_.. 015_.. prefixes yields the required dependency order.
    return sorted(VERSIONS_DIR.glob("*.sql"))


def _preflight(conn) -> None:
    """Fail early with a clear message if this isn't a Supabase-shaped DB.

    Migration 002 references auth.users (Supabase-managed) and 014 uses
    auth.role(); on a bare Postgres these don't exist and apply would fail
    mid-run with a cryptic 'relation auth.users does not exist'.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('auth.users');")
        if cur.fetchone()[0] is None:
            sys.exit(
                "Preflight failed: 'auth.users' not found. These migrations target a "
                "Supabase database (auth.users is Supabase-managed, and RLS uses "
                "auth.role()). Point SUPABASE_POSTGRES_URL at your Supabase project."
            )


def _applied(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(LEDGER_DDL)
        conn.commit()
        cur.execute("SELECT filename FROM schema_migrations;")
        return {row[0] for row in cur.fetchall()}


def status() -> None:
    conn = psycopg2.connect(_db_url())
    try:
        done = _applied(conn)
        for f in _sql_files():
            mark = "APPLIED" if f.name in done else "PENDING"
            print(f"  [{mark}] {f.name}")
    finally:
        conn.close()


def apply_all() -> None:
    conn = psycopg2.connect(_db_url())
    try:
        _preflight(conn)
        done = _applied(conn)
        pending = [f for f in _sql_files() if f.name not in done]
        if not pending:
            print("Nothing to apply — schema is up to date.")
            return
        for f in pending:
            print(f"Applying {f.name} ...", end=" ", flush=True)
            sql = f.read_text()
            with conn.cursor() as cur:
                try:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s);",
                        (f.name,),
                    )
                    conn.commit()
                    print("ok")
                except Exception as e:
                    conn.rollback()
                    sys.exit(f"\nFAILED on {f.name}: {e}")
        print(f"Applied {len(pending)} migration(s).")
    finally:
        conn.close()


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    else:
        apply_all()
