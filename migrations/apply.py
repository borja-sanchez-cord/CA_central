#!/usr/bin/env python3
"""
CA Activity Visibility — schema migrations (from Phase 3 on).

Applies every migrations/NNN_*.sql not yet recorded in schema_migrations,
in filename order, each in its own transaction. Schema changes to the model
layer are made HERE (a new numbered file), never by editing live tables —
so the database schema is exactly what's in Git, reviewable and replayable.
This is the Supabase<->GitHub link: the daily workflow runs this before the
model build, so merging a migration to main IS deploying it.

Run:  python migrations/apply.py
"""
import os
import sys

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ingestion"))
from ingest import load_env, require


def main():
    load_env()
    here = os.path.dirname(os.path.abspath(__file__))
    files = sorted(f for f in os.listdir(here)
                   if f.endswith(".sql") and f[:3].isdigit())
    conn = psycopg2.connect(require("SUPABASE_DB_URL"), connect_timeout=20)
    with conn.cursor() as cur:
        cur.execute("""create table if not exists schema_migrations (
                           filename text primary key,
                           applied_at timestamptz not null default now())""")
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("select filename from schema_migrations")
        done = {r[0] for r in cur.fetchall()}

    applied = 0
    for f in files:
        if f in done:
            continue
        sql = open(os.path.join(here, f)).read()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute("insert into schema_migrations (filename) values (%s)", (f,))
            conn.commit()  # one transaction per migration: applied fully or not at all
            applied += 1
            print(f"applied {f}")
        except Exception:
            conn.rollback()
            print(f"FAILED {f} — rolled back; nothing after it was attempted")
            raise
    print(f"migrations: {applied} applied, {len(files) - applied} already in place")
    conn.close()


if __name__ == "__main__":
    main()
