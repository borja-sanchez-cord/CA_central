#!/usr/bin/env python3
"""
CA Activity Visibility — meeting↔contact associations (attendees).

Fills the ONE gap the Phase 1 meetings pull could never close: who attended a
meeting. HubSpot keeps meeting→contact links as ASSOCIATIONS (not properties),
so the Search-based pull in ingest.py cannot see them; this script reads them
via the v4 associations batch API into `raw_hubspot_meeting_contacts`, plus
each attendee contact's email (the join key the identity layer resolves on).

Feasibility verified live 2026-07-21 (read-only probe, decisions.md): 48/48
sampled meetings had associated contacts, 46/48 resolvable to an EXTERNAL
prospect email — including RevenueHero booking-link meetings.

Design (agreed with PM 2026-07-21 — Dillon fix #22 groundwork):
- ADDITIVE AND SEPARATE. New table only. ingest.py, identity/resolve.py and
  model/build_activity.py are not touched; `activity`/`activity_flat` are
  proven byte-identical before/after this script's first run. Everything that
  reads this table lives in migrations/006 (a droppable, read-only layer).
- Full sweep every run: associations for ALL stored meetings are re-read each
  time (~543 meetings = ~6 batch calls — trivial), so late-added attendees
  heal automatically and the first run IS the full historic backfill
  (HubSpot retains associations, unlike AmpleMarket's rolling feed).
- Append-only upsert keyed on the synthetic id `<meeting_id>:<contact_id>`
  (reuses ingest.upsert unchanged); contact_email refreshes on conflict so a
  contact whose email appears later heals too. Removed associations do NOT
  propagate (rows are never deleted) — same accepted posture as every other
  HubSpot mirror (deletions never propagate; decisions.md audit 2026-07-15).
- Logged to ingestion_runs (source=hubspot, object_type=meeting_contacts) —
  the audit rule: anything writing a raw table must log there.

Run:  python ingestion/ingest_meeting_contacts.py     (no arguments)
"""
import sys
from datetime import datetime, timezone, date

import psycopg2

from ingest import load_env, require, http_post, upsert, log_run, HS_BASE

BATCH = 100  # HubSpot batch endpoints accept up to 100 inputs per call

DDL = """
create table if not exists raw_hubspot_meeting_contacts (
    id            text primary key,          -- '<meeting_id>:<contact_id>'
    meeting_id    text not null,             -- raw_hubspot_meetings.id
    contact_id    text not null,             -- HubSpot contact id (attendee)
    contact_email text,                      -- lowercased; null = contact has no email
    ingested_at   timestamptz not null default now()
);
create index if not exists rhmc_meeting_idx on raw_hubspot_meeting_contacts (meeting_id);
"""


def fetch_associations(token, meeting_ids):
    """meeting_id -> [contact_id, ...] via the v4 associations batch API."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    out = {}
    for i in range(0, len(meeting_ids), BATCH):
        payload = {"inputs": [{"id": m} for m in meeting_ids[i:i + BATCH]]}
        data = http_post(f"{HS_BASE}/crm/v4/associations/meetings/contacts/batch/read",
                         headers, payload)
        for res in data.get("results", []):
            out[res["from"]["id"]] = [str(t["toObjectId"]) for t in res.get("to", [])]
    return out


def fetch_contact_emails(token, contact_ids):
    """contact_id -> lowercased email (None when the contact has no email).
    Deliberately NOT written into raw_hubspot_contacts — that mirror stays
    activity-scoped and owned by ingest.py; attendee emails live here."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    out = {}
    for i in range(0, len(contact_ids), BATCH):
        payload = {"inputs": [{"id": c} for c in contact_ids[i:i + BATCH]],
                   "properties": ["email"]}
        data = http_post(f"{HS_BASE}/crm/v3/objects/contacts/batch/read", headers, payload)
        for r in data.get("results", []):
            email = (r.get("properties", {}).get("email") or "").strip().lower()
            out[r["id"]] = email or None
    return out


def main():
    load_env()
    token = require("HUBSPOT_PRIVATE_APP_TOKEN")
    conn = psycopg2.connect(require("SUPABASE_DB_URL"), connect_timeout=20)
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()

    started = datetime.now(timezone.utc)
    try:
        with conn.cursor() as cur:
            cur.execute("select id from raw_hubspot_meetings")
            meeting_ids = [r[0] for r in cur.fetchall()]

        assoc = fetch_associations(token, meeting_ids)
        contact_ids = sorted({c for cs in assoc.values() for c in cs})
        emails = fetch_contact_emails(token, contact_ids)

        rows = [(f"{m}:{c}", m, c, emails.get(c))
                for m, cs in assoc.items() for c in cs]
        new = upsert(conn, "raw_hubspot_meeting_contacts",
                     ["id", "meeting_id", "contact_id", "contact_email"],
                     rows, update_cols=["contact_email"])

        with_att = len(assoc)
        print(f"meeting_contacts: {len(meeting_ids)} meetings swept, "
              f"{with_att} with attendees, {len(rows)} links ({new} new), "
              f"{sum(1 for r in rows if r[3] is None)} attendee(s) without email")
        log_run(conn, activity_date=date.today(), source="hubspot",
                object_type="meeting_contacts", started_at=started,
                finished_at=datetime.now(timezone.utc), rows_fetched=len(rows),
                rows_new=new, rows_excluded=0, exclusion_breakdown=None,
                status="ok", error=None)
    except Exception as e:
        conn.rollback()  # a failed write leaves the connection aborted — clear it
        log_run(conn, activity_date=date.today(), source="hubspot",
                object_type="meeting_contacts", started_at=started,
                finished_at=datetime.now(timezone.utc), rows_fetched=None,
                rows_new=None, rows_excluded=None, exclusion_breakdown=None,
                status="error", error=str(e)[:500])
        sys.exit(f"meeting_contacts FAILED: {e}")
    conn.close()


if __name__ == "__main__":
    main()
