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
- Faithful raw copy: HubSpot emails are kept regardless of origin (each tagged
  via object_source/detail), because AmpleMarket's API does NOT expose sent
  emails -- the HubSpot copy is the only record of them. Only genuine warmup
  noise is filtered out. Precise task<->send de-duplication is deferred to
  Phase 3, where the full picture is available.
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

# Warmup / deliverability noise -> skip. Matched (case-insensitive) inside the subject.
# (This also catches AmpleMarket's own warmup emails, e.g. "amplemarketwarmupemail:".)
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
    body_preview text, body_html text,
    activity_date date,
    raw jsonb not null,
    ingested_at timestamptz not null default now()
);
-- body columns added 2026-07-15 (going-forward capture; older rows stay null)
alter table raw_hubspot_emails add column if not exists body_preview text;
alter table raw_hubspot_emails add column if not exists body_html text;
-- recipient columns added 2026-07-15 (raw format varies: 'a@b.com' / 'Name <a@b.com>'; normalize in Phase 3)
alter table raw_hubspot_emails add column if not exists to_email text;
alter table raw_hubspot_emails add column if not exists cc_email text;
create table if not exists raw_hubspot_meetings (
    id text primary key,
    hs_timestamp timestamptz,
    title text,
    object_source text, object_source_detail text,
    owner_id text,
    outcome text, attendee_owner_ids text,
    start_time timestamptz, end_time timestamptz,
    activity_date date,
    raw jsonb not null,
    ingested_at timestamptz not null default now()
);
-- outcome/attendee columns added 2026-07-15. outcome mutates after the meeting
-- (held/cancelled/no-show), so meetings upsert with UPDATE, not insert-only.
-- attendee_owner_ids = internal attendees only (external attendees are HubSpot
-- associations, not properties — not available via this pull).
alter table raw_hubspot_meetings add column if not exists outcome text;
alter table raw_hubspot_meetings add column if not exists attendee_owner_ids text;
alter table raw_hubspot_meetings add column if not exists start_time timestamptz;
alter table raw_hubspot_meetings add column if not exists end_time timestamptz;
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


def upsert(conn, table, columns, rows, update_cols=None):
    """Insert rows keyed on id. Returns count newly inserted.

    Default: skip rows whose id already exists (raw layer is append-only).
    With update_cols: refresh those columns on existing rows too (used for
    meetings, where outcome/attendees mutate after the meeting happens).
    Only true inserts count as "new" (xmax = 0 marks a freshly inserted row).
    """
    if not rows:
        return 0
    cols = ", ".join(columns)
    if update_cols:
        setters = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
        conflict = f"on conflict (id) do update set {setters}"
    else:
        conflict = "on conflict (id) do nothing"
    sql = (f"insert into {table} ({cols}) values %s "
           f"{conflict} returning (xmax = 0) as inserted")
    with conn.cursor() as cur:
        result = execute_values(cur, sql, rows, fetch=True)
    conn.commit()
    return sum(1 for r in result if r[0])


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
    # /users is paginated (20 per page) — must follow the cursor to get everyone.
    return list(ample_paged("/users?page[size]=20", key, "users"))


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
             "hs_object_source_detail_1", "hubspot_owner_id", "hs_email_from_email",
             "hs_email_to_email", "hs_email_cc_email", "hs_email_bcc_email",
             "hs_body_preview", "hs_email_html"]
    from_ms = int(day_start.timestamp() * 1000)
    to_ms = int(day_end.timestamp() * 1000)
    rows, fetched, excl = [], 0, {"warmup": 0}
    for r in hs_search("emails", token, props, from_ms, to_ms):
        fetched += 1
        p = r["properties"]
        detail = (p.get("hs_object_source_detail_1") or "")
        subject = (p.get("hs_email_subject") or "")
        # NOTE: AmpleMarket-synced emails are KEPT (their `object_source_detail`
        # tags them as "Amplemarket"). AmpleMarket's API does not expose sent
        # emails, so these HubSpot copies are the only record of them — dropping
        # them here undercounted real rep emails. Precise task<->send dedup is
        # deferred to Phase 3. Only genuine warmup noise is filtered out.
        if any(m in subject.lower() for m in WARMUP_SUBJECT_MARKERS):
            excl["warmup"] += 1; continue
        rows.append((
            r["id"], parse_ts(p.get("hs_timestamp")), subject or None,
            p.get("hs_email_direction"), p.get("hs_object_source"), detail or None,
            p.get("hubspot_owner_id"), p.get("hs_email_from_email"),
            p.get("hs_email_to_email") or None, p.get("hs_email_cc_email") or None,
            p.get("hs_body_preview") or None, p.get("hs_email_html") or None,
            activity_date, Json(r),
        ))
    cols = ["id", "hs_timestamp", "subject", "direction", "object_source",
            "object_source_detail", "owner_id", "from_email", "to_email", "cc_email",
            "body_preview", "body_html", "activity_date", "raw"]
    new = upsert(conn, "raw_hubspot_emails", cols, rows)
    return fetched, new, excl


def ingest_hs_meetings(conn, token, day_start, day_end, activity_date):
    props = ["hs_timestamp", "hs_meeting_title", "hs_object_source",
             "hs_object_source_detail_1", "hubspot_owner_id",
             "hs_meeting_outcome", "hs_attendee_owner_ids",
             "hs_meeting_start_time", "hs_meeting_end_time"]
    from_ms = int(day_start.timestamp() * 1000)
    to_ms = int(day_end.timestamp() * 1000)
    rows, fetched = [], 0
    for r in hs_search("meetings", token, props, from_ms, to_ms):
        fetched += 1
        p = r["properties"]
        rows.append((
            r["id"], parse_ts(p.get("hs_timestamp")), p.get("hs_meeting_title"),
            p.get("hs_object_source"), p.get("hs_object_source_detail_1"),
            p.get("hubspot_owner_id"),
            p.get("hs_meeting_outcome") or None, p.get("hs_attendee_owner_ids") or None,
            parse_ts(p.get("hs_meeting_start_time")), parse_ts(p.get("hs_meeting_end_time")),
            activity_date, Json(r),
        ))
    cols = ["id", "hs_timestamp", "title", "object_source", "object_source_detail",
            "owner_id", "outcome", "attendee_owner_ids", "start_time", "end_time",
            "activity_date", "raw"]
    # Meetings mutate after first capture (outcome set once held/cancelled/no-show,
    # reschedules move times) — so refresh those fields on re-runs instead of
    # insert-only. The other raw tables stay append-only.
    new = upsert(conn, "raw_hubspot_meetings", cols, rows,
                 update_cols=["hs_timestamp", "title", "outcome", "attendee_owner_ids",
                              "start_time", "end_time", "raw"])
    return fetched, new


# ----------------------------------------------------------------------------- main
# Each scheduled run re-checks the last LOOKBACK_DAYS days, not just yesterday:
# some records land in the source tools hours after the day's first snapshot
# (observed live), and re-runs are idempotent, so the sweep is free of dupes.
LOOKBACK_DAYS = 3


def run_day(conn, ample_key, hs_token, activity_date):
    """Run the 4 ingestion jobs for one day. Jobs fail independently; returns
    a list of (source, object_type, error) for any that failed."""
    day_start = datetime(activity_date.year, activity_date.month, activity_date.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

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

    failures = []
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
            print(f"  {source}/{obj}: fetched={fetched} new={new} excluded={excluded} {excl or ''}")
        except Exception as e:
            # A failed write can leave the connection in an aborted state;
            # roll back so logging and the remaining jobs can still proceed.
            conn.rollback()
            log_run(conn, activity_date=activity_date, source=source, object_type=obj,
                    started_at=started, finished_at=datetime.now(timezone.utc),
                    rows_fetched=None, rows_new=None, rows_excluded=None,
                    exclusion_breakdown=None, status="error", error=str(e)[:500])
            print(f"  {source}/{obj}: ERROR {e}")
            failures.append((source, obj, str(e)))
    return failures


def main():
    load_env()
    ample_key = require("AMPLEMARKET_API_KEY")
    hs_token = require("HUBSPOT_PRIVATE_APP_TOKEN")
    dsn = require("SUPABASE_DB_URL")

    if len(sys.argv) > 1:
        # explicit date given (manual run / backfill): that single day only
        days = [date.fromisoformat(sys.argv[1])]
    else:
        # scheduled run: yesterday plus a lookback sweep for late arrivals
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        days = [yesterday - timedelta(days=i) for i in range(LOOKBACK_DAYS)]

    conn = psycopg2.connect(dsn, connect_timeout=20)
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()

    all_failures = []
    for activity_date in days:
        print(f"=== CA Activity ingestion for {activity_date} (UTC) ===")
        all_failures += run_day(conn, ample_key, hs_token, activity_date)

    conn.close()
    if all_failures:
        print(f"=== done with {len(all_failures)} FAILED job(s): "
              + ", ".join(f"{s}/{o} ({d})" for s, o, d in
                          [(s, o, str(e)[:60]) for s, o, e in all_failures]) + " ===")
        sys.exit(1)  # every job ran, but surface the failure to the scheduler
    print("=== done ===")


if __name__ == "__main__":
    main()
