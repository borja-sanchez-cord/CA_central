#!/usr/bin/env python3
"""
CA Activity Visibility — Gong call-recording mirror (Ray ask #1: close the
unknown-meeting-outcome gap).

81% of counted CA meetings have no outcome in HubSpot (reps don't fill the
field). Gong records the meetings — AEs tag along on CA-booked calls — and
Gong's HubSpot integration already writes every recording into HubSpot as a
CALL record ("Call with X", hs_object_source_detail_1 = 'Gong'). Feasibility
verified live 2026-07-23: 13k+ Gong call records; with the strict match rule
(status COMPLETED + within the booked slot + shared contact) 91 of the 173
unknown-outcome meetings get a defensible "held" — no rep behavior change.

Key semantics learned on probe day (why the schema looks like this):
- hs_call_status: QUEUED = Gong saw it on a calendar (proves NOTHING — future
  and never-happened calls sit at QUEUED forever); COMPLETED = Gong actually
  processed a recording. Only COMPLETED is evidence a meeting happened.
- Gong flips the SAME record QUEUED->COMPLETED after the call, so this is an
  entity mirror like deals: full sweep every run, upsert refreshes status.
- hs_timestamp can be in the future (scheduled calls). Never trust presence.

Design (mirrors ingest_deals.py, the #24/#25 additive pattern):
- ADDITIVE AND SEPARATE. Two new tables only; activity/activity_flat are not
  touched and are proven byte-identical around the first run. Everything that
  reads these tables lives in migrations/009 (droppable, read-only layer).
- Plain paging endpoint over ALL calls (~700 pages), Gong rows kept client-side
  — NOT Search: no 10k cap, no lagging-index trust (the 2026-07-20 gotcha).
  Contact associations come back inline, no extra batch calls.
- ONE sweep timestamp per run stamped on every Gong row seen; deleted records
  stop receiving the stamp so 009 reads only the latest completed sweep.
- Logged to ingestion_runs (source=hubspot, object_type=gong_calls).

Run:  python ingestion/ingest_gong_calls.py     (no arguments)
"""
import sys
import time
from datetime import datetime, timezone, date

import psycopg2

from ingest import load_env, require, http_get, upsert, log_run, parse_ts, HS_BASE

PROPS = ["hs_call_title", "hs_timestamp", "hs_call_status", "hs_call_duration",
         "hs_call_recording_url", "hubspot_owner_id", "hs_object_source_detail_1"]

DDL = """
create table if not exists raw_hubspot_gong_calls (
    id             text primary key,          -- HubSpot call object id
    title          text,                      -- "Call with <person/company>"
    call_status    text,                      -- QUEUED (no evidence) / COMPLETED (recorded)
    call_time      timestamptz,               -- hs_timestamp; CAN be in the future
    duration_ms    bigint,                    -- rarely synced; kept for audit
    recording_url  text,                      -- rarely synced; kept for audit
    owner_id       text,                      -- often the AE who ran it, not the CA
    last_seen_at   timestamptz not null,      -- sweep stamp; see module doc
    ingested_at    timestamptz not null default now()
);
create index if not exists rhgc_time_idx on raw_hubspot_gong_calls (call_time);
create table if not exists raw_hubspot_gong_call_contacts (
    id            text primary key,           -- '<call_id>:<contact_id>'
    call_id       text not null,
    contact_id    text not null,              -- raw HubSpot contact id
    last_seen_at  timestamptz not null,
    ingested_at   timestamptz not null default now()
);
create index if not exists rhgcc_call_idx on raw_hubspot_gong_call_contacts (call_id);
"""


def fetch_gong_calls(token):
    """All Gong-created call records + their contact associations, via the
    plain paging endpoint over the whole calls object (~70k records, ~700
    pages). Gong rows are filtered client-side — the paging endpoint has no
    server-side property filter, and that is the price of not trusting
    Search's cap or its lagging index."""
    headers = {"Authorization": f"Bearer {token}"}
    url = (f"{HS_BASE}/crm/v3/objects/calls?limit=100&archived=false"
           f"&properties={','.join(PROPS)}&associations=contacts")
    calls, links, after, scanned = [], [], None, 0
    while True:
        data = http_get(url + (f"&after={after}" if after else ""), headers)
        for r in data.get("results", []):
            scanned += 1
            p = r.get("properties", {})
            if p.get("hs_object_source_detail_1") != "Gong":
                continue
            dur = p.get("hs_call_duration")
            calls.append((r["id"], p.get("hs_call_title"), p.get("hs_call_status"),
                          parse_ts(p.get("hs_timestamp")),
                          int(dur) if dur not in (None, "") else None,
                          p.get("hs_call_recording_url"),
                          p.get("hubspot_owner_id")))
            for c in (r.get("associations", {}).get("contacts", {})
                       .get("results", [])):
                links.append((f"{r['id']}:{c['id']}", r["id"], str(c["id"])))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        time.sleep(0.15)  # ~700 pages; stay far under HubSpot's rate limits
        if not after:
            break
    # inline associations repeat a contact per association TYPE — dedup on id
    links = list({l[0]: l for l in links}.values())
    return calls, links, scanned


def main():
    load_env()
    token = require("HUBSPOT_PRIVATE_APP_TOKEN")
    conn = psycopg2.connect(require("SUPABASE_DB_URL"), connect_timeout=20)
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()

    started = datetime.now(timezone.utc)
    sweep = started  # ONE stamp for the whole run — 009 keys on max(last_seen_at)
    try:
        calls, links, scanned = fetch_gong_calls(token)

        # links land BEFORE calls so max(calls.last_seen_at) — the gate the
        # verification view reads through — only advances once its inputs are in
        upsert(conn, "raw_hubspot_gong_call_contacts",
               ["id", "call_id", "contact_id", "last_seen_at"],
               [r + (sweep,) for r in links], update_cols=["last_seen_at"])
        new = upsert(conn, "raw_hubspot_gong_calls",
                     ["id", "title", "call_status", "call_time", "duration_ms",
                      "recording_url", "owner_id", "last_seen_at"],
                     [r + (sweep,) for r in calls],
                     update_cols=["title", "call_status", "call_time",
                                  "duration_ms", "recording_url", "owner_id",
                                  "last_seen_at"])

        completed = sum(1 for c in calls if c[2] == "COMPLETED")
        print(f"gong_calls: {scanned} calls scanned, {len(calls)} Gong rows swept "
              f"({new} new), {completed} COMPLETED, {len(links)} contact links")
        log_run(conn, activity_date=date.today(), source="hubspot",
                object_type="gong_calls", started_at=started,
                finished_at=datetime.now(timezone.utc), rows_fetched=len(calls),
                rows_new=new, rows_excluded=0, exclusion_breakdown=None,
                status="ok", error=None)
    except Exception as e:
        conn.rollback()  # a failed write leaves the connection aborted — clear it
        log_run(conn, activity_date=date.today(), source="hubspot",
                object_type="gong_calls", started_at=started,
                finished_at=datetime.now(timezone.utc), rows_fetched=None,
                rows_new=None, rows_excluded=None, exclusion_breakdown=None,
                status="error", error=str(e)[:500])
        sys.exit(f"gong_calls FAILED: {e}")
    conn.close()


if __name__ == "__main__":
    main()
