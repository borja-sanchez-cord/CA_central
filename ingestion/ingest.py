#!/usr/bin/env python3
"""
CA Activity Visibility — Phase 1: daily raw ingestion.

Copies one day of raw activity from AmpleMarket (tasks + calls) and HubSpot
(emails + meetings) into raw landing tables in Supabase/Postgres.

Design notes (see docs/spec.md, docs/decisions.md):
- RAW copy: store the source payload faithfully in a `raw` jsonb column plus a
  few extracted columns for convenience. No normalization here (that's Phase 3).
- Idempotent: primary key = source id; INSERT ... ON CONFLICT DO NOTHING, so a
  second run of the same day inserts 0 new rows.
- Source-of-truth split (load-bearing): AmpleMarket owns its channels, so
  HubSpot emails that were *synced from AmpleMarket* are skipped, as are warmup
  emails. Everything else (manual Gmail, Apollo, etc.) is kept raw.
- AmpleMarket ignores date filters, so we page newest-first and stop once we
  cross below the target day.

Run:  python ingestion/ingest.py [YYYY-MM-DD]   (default: yesterday UTC)
"""
import os, sys, json, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone, date

import psycopg2
from psycopg2.extras import execute_values, Json

# ----------------------------------------------------------------------------- config
AMPLE_BASE = "https://api.amplemarket.com"
HS_BASE = "https://api.hubapi.com"

# HubSpot emails whose source is one of these are AmpleMarket-synced -> skip (dedup).
AMPLEMARKET_SOURCE_DETAILS = {"amplemarket"}
# Warmup / deliverability noise -> skip. Matched (case-insensitive) inside the subject.
WARMUP_SUBJECT_MARKERS = ("lemwarmup", "lemwarm", "amplemarketwarmup", "warmupemail")


def load_env():
    """In GitHub Actions the secrets are real env vars. Locally, read .env."""
    here = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(here, "..", ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def require(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing required setting: {name}")
    return v


# ----------------------------------------------------------------------------- http helpers
def http_get(url, headers, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(1.5 * (attempt + 1)); continue
            raise
    raise RuntimeError(f"GET failed after {tries} tries: {url}")


def http_post(url, headers, payload, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(1.5 * (attempt + 1)); continue
            raise
    raise RuntimeError(f"POST failed after {tries} tries: {url}")


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ----------------------------------------------------------------------------- schema
DDL = """
create table if not exists raw_amplemarket_tasks (
    id text primary key,
    user_id text, user_email text,
    type text, status text, automatic boolean,
    due_on timestamptz, finished_on timestamptz,
    contact_id text, contact_email text, contact_name text,
    activity_date date,
    raw jsonb not null,
    ingested_at timestamptz not null default now()
);
create table if not exists raw_amplemarket_calls (
    id text primary key,
    user_id text,
    start_date timestamptz, duration integer,
    answered boolean, human boolean, external boolean,
    task_id text,
    contact_id text, contact_email text, contact_name text,
    activity_date date,
    raw jsonb not null,
    ingested_at timestamptz not null default now()
);
create table if not exists raw_hubspot_emails (
    id text primary key,
    hs_timestamp timestamptz,
    subject text, direction text,
    object_source text, object_source_detail text,
    owner_id text, from_email text,
    activity_date date,
    raw jsonb not null,
    ingested_at timestamptz not null default now()
);
create table if not exists raw_hubspot_meetings (
    id text primary key,
    hs_timestamp timestamptz,
    title text,
    object_source text, object_source_detail text,
    owner_id text,
    activity_date date,
    raw jsonb not null,
    ingested_at timestamptz not null default now()
);
create table if not exists ingestion_runs (
    run_id bigint generated always as identity primary key,
    activity_date date not null,
    source text not null,
    object_type text not null,
    started_at timestamptz not null,
    finished_at timestamptz,
    rows_fetched integer,
    rows_new integer,
    rows_excluded integer,
    exclusion_breakdown jsonb,
    status text,
    error text
);
"""


def upsert(conn, table, columns, rows):
    """Insert rows, skipping ones whose id already exists. Returns count newly inserted."""
    if not rows:
        return 0
    cols = ", ".join(columns)
    sql = (f"insert into {table} ({cols}) values %s "
           f"on conflict (id) do nothing returning id")
    with conn.cursor() as cur:
        result = execute_values(cur, sql, rows, fetch=True)
    conn.commit()
    return len(result)


def log_run(conn, **kw):
    with conn.cursor() as cur:
        cur.execute(
            """insert into ingestion_runs
               (activity_date, source, object_type, started_at, finished_at,
                rows_fetched, rows_new, rows_excluded, exclusion_breakdown, status, error)
               values (%(activity_date)s,%(source)s,%(object_type)s,%(started_at)s,%(finished_at)s,
                       %(rows_fetched)s,%(rows_new)s,%(rows_excluded)s,%(exclusion_breakdown)s,
                       %(status)s,%(error)s)""",
            kw)
    conn.commit()


# ----------------------------------------------------------------------------- AmpleMarket
def ample_users(key):
    data = http_get(f"{AMPLE_BASE}/users", {"Authorization": f"Bearer {key}"})
    return data.get("users", [])


def ample_paged(path, key, list_field):
    """Yield items across cursor pages (newest first), following _links.next."""
    url = AMPLE_BASE + path
    headers = {"Authorization": f"Bearer {key}"}
    pages = 0
    while url and pages < 200:
        data = http_get(url, headers)
        for item in data.get(list_field, []):
            yield item
        nxt = (data.get("_links") or {}).get("next", {}).get("href")
        url = AMPLE_BASE + nxt if nxt else None
        pages += 1
        time.sleep(0.15)  # stay well under 500 req/min


def ingest_ample_tasks(conn, key, day_start, day_end, activity_date):
    users = ample_users(key)
    rows, fetched, skipped_users = [], 0, 0
    for u in users:
        uid = u.get("id")
        if not uid:
            continue
        try:
            page = ample_paged(f"/tasks?user_id={uid}&status=completed&page[size]=100", key, "tasks")
            for t in page:
                ts = parse_ts(t.get("finished_on"))
                if ts is None:
                    continue
                if ts >= day_end:
                    continue        # too new; keep paging back in time
                if ts < day_start:
                    break           # crossed below the day -> stop this user
                fetched += 1
                c = t.get("contact") or {}
                rows.append((
                    t["id"], t.get("user_id"), t.get("user_email"), t.get("type"),
                    t.get("status"), t.get("automatic"), parse_ts(t.get("due_on")), ts,
                    c.get("id"), c.get("email"), c.get("name"), activity_date, Json(t),
                ))
        except urllib.error.HTTPError as e:
            if e.code == 400:        # e.g. deactivated/invalid user in the user list
                skipped_users += 1
                continue
            raise
    if skipped_users:
        print(f"  (skipped {skipped_users} invalid/deactivated AmpleMarket users)")
    cols = ["id", "user_id", "user_email", "type", "status", "automatic", "due_on",
            "finished_on", "contact_id", "contact_email", "contact_name", "activity_date", "raw"]
    new = upsert(conn, "raw_amplemarket_tasks", cols, rows)
    return fetched, new


def ingest_ample_calls(conn, key, day_start, day_end, activity_date):
    rows, fetched = [], 0
    for c in ample_paged("/calls?page[size]=100", key, "calls"):
        ts = parse_ts(c.get("start_date"))
        if ts is None:
            continue
        if ts >= day_end:
            continue
        if ts < day_start:
            break
        fetched += 1
        ct = c.get("contact") or {}
        rows.append((
            c["id"], c.get("user_id"), ts, c.get("duration"), c.get("answered"),
            c.get("human"), c.get("external"), c.get("task_id"),
            ct.get("id"), ct.get("email"), ct.get("name"), activity_date, Json(c),
        ))
    cols = ["id", "user_id", "start_date", "duration", "answered", "human", "external",
            "task_id", "contact_id", "contact_email", "contact_name", "activity_date", "raw"]
    new = upsert(conn, "raw_amplemarket_calls", cols, rows)
    return fetched, new


# ----------------------------------------------------------------------------- HubSpot
def hs_search(obj, token, props, ts_from_ms, ts_to_ms):
    """Yield all records of `obj` with hs_timestamp in [from, to), newest first."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    after = None
    while True:
        payload = {
            "filterGroups": [{"filters": [
                {"propertyName": "hs_timestamp", "operator": "GTE", "value": str(ts_from_ms)},
                {"propertyName": "hs_timestamp", "operator": "LT", "value": str(ts_to_ms)},
            ]}],
            "sorts": [{"propertyName": "hs_timestamp", "direction": "DESCENDING"}],
            "properties": props,
            "limit": 100,
        }
        if after:
            payload["after"] = after
        data = http_post(f"{HS_BASE}/crm/v3/objects/{obj}/search", headers, payload)
        for r in data.get("results", []):
            yield r
        after = (data.get("paging") or {}).get("next", {}).get("after")
        time.sleep(0.3)  # HubSpot Search ~4 req/sec
        if not after:
            break


def ingest_hs_emails(conn, token, day_start, day_end, activity_date):
    props = ["hs_timestamp", "hs_email_subject", "hs_email_direction", "hs_object_source",
             "hs_object_source_detail_1", "hubspot_owner_id", "hs_email_from_email"]
    from_ms = int(day_start.timestamp() * 1000)
    to_ms = int(day_end.timestamp() * 1000)
    rows, fetched, excl = [], 0, {"amplemarket_synced": 0, "warmup": 0}
    for r in hs_search("emails", token, props, from_ms, to_ms):
        fetched += 1
        p = r["properties"]
        detail = (p.get("hs_object_source_detail_1") or "")
        subject = (p.get("hs_email_subject") or "")
        if detail.strip().lower() in AMPLEMARKET_SOURCE_DETAILS:
            excl["amplemarket_synced"] += 1; continue
        if any(m in subject.lower() for m in WARMUP_SUBJECT_MARKERS):
            excl["warmup"] += 1; continue
        rows.append((
            r["id"], parse_ts(p.get("hs_timestamp")), subject or None,
            p.get("hs_email_direction"), p.get("hs_object_source"), detail or None,
            p.get("hubspot_owner_id"), p.get("hs_email_from_email"), activity_date, Json(r),
        ))
    cols = ["id", "hs_timestamp", "subject", "direction", "object_source",
            "object_source_detail", "owner_id", "from_email", "activity_date", "raw"]
    new = upsert(conn, "raw_hubspot_emails", cols, rows)
    return fetched, new, excl


def ingest_hs_meetings(conn, token, day_start, day_end, activity_date):
    props = ["hs_timestamp", "hs_meeting_title", "hs_object_source",
             "hs_object_source_detail_1", "hubspot_owner_id"]
    from_ms = int(day_start.timestamp() * 1000)
    to_ms = int(day_end.timestamp() * 1000)
    rows, fetched = [], 0
    for r in hs_search("meetings", token, props, from_ms, to_ms):
        fetched += 1
        p = r["properties"]
        rows.append((
            r["id"], parse_ts(p.get("hs_timestamp")), p.get("hs_meeting_title"),
            p.get("hs_object_source"), p.get("hs_object_source_detail_1"),
            p.get("hubspot_owner_id"), activity_date, Json(r),
        ))
    cols = ["id", "hs_timestamp", "title", "object_source", "object_source_detail",
            "owner_id", "activity_date", "raw"]
    new = upsert(conn, "raw_hubspot_meetings", cols, rows)
    return fetched, new


# ----------------------------------------------------------------------------- main
def main():
    load_env()
    ample_key = require("AMPLEMARKET_API_KEY")
    hs_token = require("HUBSPOT_PRIVATE_APP_TOKEN")
    dsn = require("SUPABASE_DB_URL")

    if len(sys.argv) > 1:
        activity_date = date.fromisoformat(sys.argv[1])
    else:
        activity_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    day_start = datetime(activity_date.year, activity_date.month, activity_date.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    print(f"=== CA Activity ingestion for {activity_date} (UTC) ===")
    conn = psycopg2.connect(dsn, connect_timeout=20)
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()

    jobs = [
        ("amplemarket", "tasks",
         lambda: ingest_ample_tasks(conn, ample_key, day_start, day_end, activity_date)),
        ("amplemarket", "calls",
         lambda: ingest_ample_calls(conn, ample_key, day_start, day_end, activity_date)),
        ("hubspot", "emails",
         lambda: ingest_hs_emails(conn, hs_token, day_start, day_end, activity_date)),
        ("hubspot", "meetings",
         lambda: ingest_hs_meetings(conn, hs_token, day_start, day_end, activity_date)),
    ]

    summary = []
    for source, obj, fn in jobs:
        started = datetime.now(timezone.utc)
        try:
            res = fn()
            if obj == "emails":
                fetched, new, excl = res
                excluded = sum(excl.values())
            else:
                fetched, new = res
                excluded, excl = 0, None
            log_run(conn, activity_date=activity_date, source=source, object_type=obj,
                    started_at=started, finished_at=datetime.now(timezone.utc),
                    rows_fetched=fetched, rows_new=new, rows_excluded=excluded,
                    exclusion_breakdown=Json(excl) if excl else None,
                    status="ok", error=None)
            summary.append((source, obj, fetched, new, excluded, excl))
            print(f"  {source}/{obj}: fetched={fetched} new={new} excluded={excluded} {excl or ''}")
        except Exception as e:
            log_run(conn, activity_date=activity_date, source=source, object_type=obj,
                    started_at=started, finished_at=datetime.now(timezone.utc),
                    rows_fetched=None, rows_new=None, rows_excluded=None,
                    exclusion_breakdown=None, status="error", error=str(e)[:500])
            print(f"  {source}/{obj}: ERROR {e}")
            raise

    conn.close()
    print("=== done ===")
    return summary


if __name__ == "__main__":
    main()
