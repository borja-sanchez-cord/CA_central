#!/usr/bin/env python3
"""
CA Activity Visibility — Phase 1: daily raw ingestion.

Copies raw activity from AmpleMarket (tasks + calls) and HubSpot (emails +
meetings) into raw landing tables in Supabase/Postgres. A scheduled run (no
date argument) sweeps the last LOOKBACK_DAYS days, because both sources keep
surfacing a finished day's records for days afterwards (observed live).
Scheduled runs also sync the HubSpot entity dimensions (Phase 1.5): a full
incremental mirror of companies, an activity-scoped mirror of contacts (only
people who appear in real rep activity — see the entity section below), and a
full mirror of owners + their team membership (the source of truth for the CA
roster; Phase 2 derives the roster from it, subtracting parent teams per the
config_ca_teams policy, which is seeded from config/ca_teams.json each run).

Design notes (see docs/spec.md, docs/decisions.md):
- RAW copy: store the source payload faithfully in a `raw` jsonb column plus a
  few extracted columns for convenience. No normalization here (that's Phase 3).
- Idempotent: primary key = source id. Tasks/calls/emails are append-only
  (ON CONFLICT DO NOTHING — re-running a day adds only genuinely new rows).
  Meetings are the one exception: they update-on-conflict, because a meeting's
  outcome/times mutate after first capture (held / cancelled / rescheduled).
- Faithful raw copy: HubSpot emails are kept regardless of origin (each tagged
  via object_source/detail), because AmpleMarket's API does NOT expose sent
  emails -- the HubSpot copy is the only record of them. Only genuine warmup
  noise is filtered out. Precise task<->send de-duplication is deferred to
  Phase 3, where the full picture is available.
- AmpleMarket ignores date filters, so we page newest-first and stop once a
  WHOLE page falls below the target day (the feed is not perfectly ordered,
  so stopping at the first older record was observed to miss items).
- Jobs fail independently; failures are logged to ingestion_runs and the
  process exits non-zero at the end so the scheduler shows the failure.

Run:  python ingestion/ingest.py [YYYY-MM-DD]   (explicit date: that day only;
      default: yesterday + LOOKBACK_DAYS-1 prior days)
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
    last_err = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                last_err = e
                time.sleep(1.5 * (attempt + 1)); continue
            raise
        except OSError as e:
            # network blip (connection reset / DNS / socket timeout) — retry
            # like a 5xx. (HTTPError is caught above; it subclasses OSError.)
            last_err = e
            time.sleep(1.5 * (attempt + 1)); continue
    raise RuntimeError(f"GET failed after {tries} tries: {url} (last: {last_err})")


def http_post(url, headers, payload, tries=4):
    last_err = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                last_err = e
                time.sleep(1.5 * (attempt + 1)); continue
            raise
        except OSError as e:
            # network blip — retry like a 5xx (HTTPError caught above)
            last_err = e
            time.sleep(1.5 * (attempt + 1)); continue
    raise RuntimeError(f"POST failed after {tries} tries: {url} (last: {last_err})")


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
-- Phase 1.5 (2026-07-15): HubSpot Company/Contact objects. These are
-- DIMENSION tables (current state of an entity), not activity/event tables:
-- no activity_date, not part of the daily date sweep, and they upsert with
-- UPDATE (tiers/owners/titles change over time — we keep the latest state).
-- Sync is incremental by last-modified watermark (see ingest functions).
create table if not exists raw_hubspot_companies (
    id text primary key,
    name text, domain text,
    icp_tier_validated text, icp_tier_new text,
    vertical text,
    target_account_owner text, target_account_tier text, target_account_segment text,
    owner_id text,
    hs_created timestamptz, hs_lastmodified timestamptz,
    raw jsonb not null,
    ingested_at timestamptz not null default now()
);
create table if not exists raw_hubspot_contacts (
    id text primary key,
    email text, firstname text, lastname text, jobtitle text,
    associated_company_id text, lifecyclestage text,
    hs_created timestamptz, hs_lastmodified timestamptz,
    raw jsonb not null,
    ingested_at timestamptz not null default now()
);
-- AmpleMarket users. Dimension mirror, full refresh each scheduled run (~56).
-- Load-bearing for CALL attribution: raw_amplemarket_calls name their rep by
-- internal user id ONLY (no name/email) — this table is the only way to map a
-- call to a person. `mailboxes` links a user's sending addresses. ALL users are
-- kept, incl. deactivated (raw copy; Phase 2 filters by the CA roster, and the
-- `role` field is unreliable — active CAs can show as `admin`).
create table if not exists raw_amplemarket_users (
    id text primary key,
    name text, email text,
    status text, role text,
    mailboxes jsonb,           -- [{id, email}, ...] as returned by AmpleMarket
    raw jsonb not null,
    ingested_at timestamptz not null default now()
);
-- HubSpot owners (reps/users) + their team membership. Dimension mirror, full
-- refresh each scheduled run (small set, ~130). This is the SOURCE OF TRUTH for
-- the CA roster: Phase 2 derives the roster from `teams` here, subtracting each
-- parent team (see config_ca_teams). No roster logic in this file — pure pull.
create table if not exists raw_hubspot_owners (
    id text primary key,
    email text,
    first_name text, last_name text,
    user_id text,
    archived boolean,
    teams jsonb,               -- [{id, name, primary}, ...] as returned by HubSpot
    hs_created timestamptz, hs_lastmodified timestamptz,
    raw jsonb not null,
    ingested_at timestamptz not null default now()
);
-- CA-team POLICY (which teams count as CAs, and the parent team to subtract).
-- Seeded from the version-controlled config/ca_teams.json on every run; the
-- table — never the file — is what queries read. Phase 2 joins owners.teams
-- against this to produce the resolved roster (dim_ca).
create table if not exists config_ca_teams (
    ca_team_name text primary key,
    parent_team_name text not null
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


def ample_pages(path, key, list_field):
    """Yield whole pages (lists of items, newest first), following _links.next."""
    url = AMPLE_BASE + path
    headers = {"Authorization": f"Bearer {key}"}
    pages = 0
    while url and pages < 200:
        data = http_get(url, headers)
        yield data.get(list_field, [])
        nxt = (data.get("_links") or {}).get("next", {}).get("href")
        url = AMPLE_BASE + nxt if nxt else None
        pages += 1
        time.sleep(0.15)  # stay well under 500 req/min


def ample_paged(path, key, list_field):
    """Yield items across cursor pages (newest first)."""
    for page in ample_pages(path, key, list_field):
        for item in page:
            yield item


def ingest_ample_tasks(conn, key, day_start, day_end, activity_date):
    users = ample_users(key)
    rows, fetched, skipped_users = [], 0, 0
    for u in users:
        uid = u.get("id")
        if not uid:
            continue
        try:
            for page in ample_pages(f"/tasks?user_id={uid}&status=completed&page[size]=100", key, "tasks"):
                page_ts = [parse_ts(t.get("finished_on")) for t in page]
                for t, ts in zip(page, page_ts):
                    if ts is None or ts >= day_end or ts < day_start:
                        continue    # outside the target day
                    fetched += 1
                    c = t.get("contact") or {}
                    rows.append((
                        t["id"], t.get("user_id"), t.get("user_email"), t.get("type"),
                        t.get("status"), t.get("automatic"), parse_ts(t.get("due_on")), ts,
                        c.get("id"), c.get("email"), c.get("name"), activity_date, Json(t),
                    ))
                # Stop only once a WHOLE page is older than the day. The feed is
                # roughly newest-first but provably not perfectly ordered (records
                # for a day keep surfacing later, interleaved) — breaking on the
                # first old record was observed to miss items hiding behind it.
                known = [ts for ts in page_ts if ts is not None]
                if known and max(known) < day_start:
                    break
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
    for page in ample_pages("/calls?page[size]=100", key, "calls"):
        page_ts = [parse_ts(c.get("start_date")) for c in page]
        for c, ts in zip(page, page_ts):
            if ts is None or ts >= day_end or ts < day_start:
                continue    # outside the target day
            fetched += 1
            ct = c.get("contact") or {}
            rows.append((
                c["id"], c.get("user_id"), ts, c.get("duration"), c.get("answered"),
                c.get("human"), c.get("external"), c.get("task_id"),
                ct.get("id"), ct.get("email"), ct.get("name"), activity_date, Json(c),
            ))
        # whole-page stop rule — same rationale as tasks (feed not perfectly ordered)
        known = [ts for ts in page_ts if ts is not None]
        if known and max(known) < day_start:
            break
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


# ------------------------------------------------------------- HubSpot entities (Phase 1.5)
# Companies + contacts are dimension data (current state of an entity), synced
# differently from the activity tables:
# - COMPANIES: full mirror of all ~154k records, incremental by last-modified
#   watermark (first run loads everything; daily runs fetch only changes).
#   Full mirror deliberately — filtering on `target_account_owner` would
#   silently drop accounts whose CRM assignment is missing/stale.
# - CONTACTS: ACTIVITY-SCOPED mirror (PM decision 2026-07-15). The full 446k /
#   ~300 MB mirror doesn't fit the Supabase free tier; instead we mirror only
#   contacts that appear in actual rep activity (task/call contacts + email
#   senders/recipients), re-read in full each run so field changes (jobtitle,
#   company) stay fresh. Self-extending: touch a new person -> next run pulls
#   them in. Known trade-off: contacts nobody ever touched are absent, so
#   "untouched contacts per account" (whitespace denominator, Phase 5) needs
#   the full mirror + a plan upgrade later.

# Addresses at these domains are OUR REPS, not prospects. Includes the
# dedicated cold-outreach domain tryencord.com (found 2026-07-15: reps send
# from it, which made their outbound look like inbound from a stranger).
INTERNAL_DOMAINS = ("encord.com", "encord.ai", "tryencord.com")


def _extract_emails(s):
    """Lowercase plain addresses from 'a@b.com' / 'Name <a@b.com>' / '<a@b>',
    semicolon-separated when multiple (HubSpot recipient-string formats)."""
    out = []
    for part in (s or "").split(";"):
        part = part.strip()
        if "<" in part and ">" in part:
            part = part[part.index("<") + 1:part.index(">")]
        part = part.strip().lower()
        if "@" in part and "." in part.rsplit("@", 1)[1]:
            out.append(part)
    return out


def hs_entities_modified_since(obj, token, props, ts_prop, since_ms):
    """Yield every `obj` record modified >= since_ms, oldest-modified first.

    The Search API hard-caps any single query at 10,000 results, so on
    approaching the cap we restart the query from the last-seen watermark.
    GTE + restart re-yields boundary records; the upsert makes that harmless.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    while True:
        after, seen, last_ms, exhausted = None, 0, since_ms, False
        while True:
            payload = {
                "filterGroups": [{"filters": [
                    {"propertyName": ts_prop, "operator": "GTE", "value": str(since_ms)},
                ]}],
                "sorts": [{"propertyName": ts_prop, "direction": "ASCENDING"}],
                "properties": props,
                "limit": 100,
            }
            if after:
                payload["after"] = after
            data = http_post(f"{HS_BASE}/crm/v3/objects/{obj}/search", headers, payload)
            results = data.get("results", [])
            for r in results:
                ts = parse_ts(r["properties"].get(ts_prop))
                if ts:
                    last_ms = int(ts.timestamp() * 1000)
                yield r
            seen += len(results)
            after = (data.get("paging") or {}).get("next", {}).get("after")
            time.sleep(0.3)  # HubSpot Search ~4 req/sec
            if not after:
                exhausted = True
                break
            if seen >= 9500:  # near the 10k cap -> restart from the watermark
                break
        if exhausted:
            break
        if last_ms <= since_ms:
            last_ms = since_ms + 1  # forward-progress guard (theoretical)
        since_ms = last_ms


def _entity_watermark_ms(conn, table):
    """Resume point: newest last-modified we already hold, minus 1h overlap."""
    with conn.cursor() as cur:
        cur.execute(f"select coalesce(extract(epoch from max(hs_lastmodified)) * 1000, 0) from {table}")
        since_ms = int(cur.fetchone()[0])
    return max(0, since_ms - 3_600_000)


def ingest_hs_companies(conn, token):
    props = ["name", "domain", "account_icp_tier_validated", "account_icp__tier_new",
             "vertical__aligned_by_team", "target_account_owner", "target_account_tier",
             "target_account_segment", "hubspot_owner_id", "createdate", "hs_lastmodifieddate"]
    cols = ["id", "name", "domain", "icp_tier_validated", "icp_tier_new", "vertical",
            "target_account_owner", "target_account_tier", "target_account_segment",
            "owner_id", "hs_created", "hs_lastmodified", "raw"]
    update_cols = cols[1:]  # everything but the id refreshes to current state
    since_ms = _entity_watermark_ms(conn, "raw_hubspot_companies")
    rows, fetched, new = [], 0, 0
    for r in hs_entities_modified_since("companies", token, props, "hs_lastmodifieddate", since_ms):
        p = r["properties"]
        fetched += 1
        rows.append((
            r["id"], p.get("name"), p.get("domain"),
            p.get("account_icp_tier_validated"), p.get("account_icp__tier_new"),
            p.get("vertical__aligned_by_team"), p.get("target_account_owner"),
            p.get("target_account_tier"), p.get("target_account_segment"),
            p.get("hubspot_owner_id"), parse_ts(p.get("createdate")),
            parse_ts(p.get("hs_lastmodifieddate")), Json(r),
        ))
        if len(rows) >= 2000:  # flush in batches: bounded memory, resumable watermark
            new += upsert(conn, "raw_hubspot_companies", cols, rows, update_cols=update_cols)
            rows = []
    new += upsert(conn, "raw_hubspot_companies", cols, rows, update_cols=update_cols)
    return fetched, new


def _activity_prospect_emails(conn):
    """Every distinct non-internal email address seen in ingested activity."""
    emails = set()
    with conn.cursor() as cur:
        cur.execute("select distinct contact_email from raw_amplemarket_tasks where contact_email is not null")
        for (e,) in cur.fetchall():
            emails.update(_extract_emails(e))
        cur.execute("select distinct contact_email from raw_amplemarket_calls where contact_email is not null")
        for (e,) in cur.fetchall():
            emails.update(_extract_emails(e))
        cur.execute("select distinct from_email, to_email, cc_email from raw_hubspot_emails")
        for f, t, c in cur.fetchall():
            for s in (f, t, c):
                emails.update(_extract_emails(s))
    return {e for e in emails if e.rsplit("@", 1)[1] not in INTERNAL_DOMAINS}


def ingest_hs_contacts(conn, token):
    """Activity-scoped contact mirror: batch-read from HubSpot, by email, every
    prospect address seen in activity. Re-reads the whole (small) set each run
    so changed fields stay fresh; unknown emails are simply not returned.
    NOTE: contacts' canonical last-modified property is `lastmodifieddate`
    (companies use `hs_lastmodifieddate`)."""
    props = ["email", "firstname", "lastname", "jobtitle", "associatedcompanyid",
             "lifecyclestage", "createdate", "lastmodifieddate"]
    cols = ["id", "email", "firstname", "lastname", "jobtitle", "associated_company_id",
            "lifecyclestage", "hs_created", "hs_lastmodified", "raw"]
    update_cols = cols[1:]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    wanted = sorted(_activity_prospect_emails(conn))
    rows, fetched, new = [], 0, 0
    seen_ids = set()  # two input emails can resolve to ONE contact (primary+alias)
    for i in range(0, len(wanted), 100):  # batch/read caps at 100 inputs
        chunk = wanted[i:i + 100]
        payload = {"idProperty": "email", "properties": props,
                   "inputs": [{"id": e} for e in chunk]}
        data = http_post(f"{HS_BASE}/crm/v3/objects/contacts/batch/read", headers, payload)
        for r in data.get("results", []):
            if r["id"] in seen_ids:
                continue
            seen_ids.add(r["id"])
            p = r["properties"]
            fetched += 1
            rows.append((
                r["id"], p.get("email"), p.get("firstname"), p.get("lastname"),
                p.get("jobtitle"), p.get("associatedcompanyid"), p.get("lifecyclestage"),
                parse_ts(p.get("createdate")), parse_ts(p.get("lastmodifieddate")), Json(r),
            ))
        if len(rows) >= 2000:
            new += upsert(conn, "raw_hubspot_contacts", cols, rows, update_cols=update_cols)
            rows = []
        time.sleep(0.15)
    new += upsert(conn, "raw_hubspot_contacts", cols, rows, update_cols=update_cols)
    print(f"    (contacts scope: {len(wanted)} activity emails -> {fetched} matched in HubSpot)")
    return fetched, new


def ingest_ample_users(conn, key):
    """Full mirror of AmpleMarket users (small set, ~56). The only source that
    maps an AmpleMarket internal user id -> person + sending mailboxes; calls
    reference their rep by this id alone, so call attribution depends on it."""
    users = ample_users(key)
    cols = ["id", "name", "email", "status", "role", "mailboxes", "raw"]
    rows = [(u["id"], u.get("name"), u.get("email"), u.get("status"),
             u.get("role"), Json(u.get("mailboxes") or []), Json(u))
            for u in users if u.get("id")]
    new = upsert(conn, "raw_amplemarket_users", cols, rows, update_cols=cols[1:])
    return len(rows), new


def ingest_hs_owners(conn, token):
    """Full mirror of HubSpot owners + their team membership (small set, ~130).
    The /crm/v3/owners list returns each owner's email and a `teams` array.
    Pure pull: the CA roster is DERIVED from this in Phase 2 (owners in a
    config_ca_teams team, minus that team's parent members) — not here."""
    headers = {"Authorization": f"Bearer {token}"}
    cols = ["id", "email", "first_name", "last_name", "user_id", "archived",
            "teams", "hs_created", "hs_lastmodified", "raw"]
    update_cols = cols[1:]
    url = f"{HS_BASE}/crm/v3/owners?limit=100"
    rows, fetched = [], 0
    while url:
        data = http_get(url, headers)
        for r in data.get("results", []):
            fetched += 1
            uid = r.get("userId")
            rows.append((
                r["id"], r.get("email"), r.get("firstName"), r.get("lastName"),
                str(uid) if uid is not None else None, r.get("archived"),
                Json(r.get("teams") or []),
                parse_ts(r.get("createdAt")), parse_ts(r.get("updatedAt")), Json(r),
            ))
        url = (data.get("paging") or {}).get("next", {}).get("link")
    new = upsert(conn, "raw_hubspot_owners", cols, rows, update_cols=update_cols)
    return fetched, new


def seed_ca_teams(conn):
    """Sync config_ca_teams to the version-controlled config/ca_teams.json.
    Read at build time only — everything else queries the table. Full replace
    so the table mirrors the file exactly (editing/removing a team + re-running
    is reflected). Best-effort: a missing/broken config must not stop the pull."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "ca_teams.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
        rows = [(t["ca_team"], t["parent_team"]) for t in cfg["ca_teams"]]
        with conn.cursor() as cur:
            cur.execute("delete from config_ca_teams")
            execute_values(cur,
                "insert into config_ca_teams (ca_team_name, parent_team_name) values %s", rows)
        conn.commit()
        print(f"  config_ca_teams: seeded {len(rows)} CA teams from config/ca_teams.json")
    except Exception as e:
        conn.rollback()
        print(f"  config_ca_teams: WARNING seed skipped ({e})")


# ----------------------------------------------------------------------------- main
# Each scheduled run re-checks the last LOOKBACK_DAYS days, not just yesterday:
# records keep surfacing in the source APIs for days after they happened
# (observed live: +119 tasks an hour later, +72 more a day later, for one day).
# Re-runs are idempotent, so the sweep is free of dupes.
# IS 3 ENOUGH? Monitor ingestion_runs: if the OLDEST day of the sweep still
# reports rows_new > 0 regularly, records are arriving later than the window
# reaches — widen LOOKBACK_DAYS.
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
    seed_ca_teams(conn)  # sync the CA-team policy table from the repo config

    all_failures = []
    for activity_date in days:
        print(f"=== CA Activity ingestion for {activity_date} (UTC) ===")
        all_failures += run_day(conn, ample_key, hs_token, activity_date)

    # Entity sync (Phase 1.5): companies (+ contacts once enabled). Scheduled
    # runs only — explicit-date runs are activity backfills and skip this.
    if len(sys.argv) <= 1:
        today = datetime.now(timezone.utc).date()
        entity_jobs = [
            ("hubspot", "companies", lambda: ingest_hs_companies(conn, hs_token)),
            ("hubspot", "contacts", lambda: ingest_hs_contacts(conn, hs_token)),
            ("hubspot", "owners", lambda: ingest_hs_owners(conn, hs_token)),
            ("amplemarket", "users", lambda: ingest_ample_users(conn, ample_key)),
        ]
        print(f"=== Entity sync (companies incremental / contacts activity-scoped / owners + ample users full) ===")
        for source, obj, fn in entity_jobs:
            started = datetime.now(timezone.utc)
            try:
                fetched, new = fn()
                log_run(conn, activity_date=today, source=source, object_type=obj,
                        started_at=started, finished_at=datetime.now(timezone.utc),
                        rows_fetched=fetched, rows_new=new, rows_excluded=0,
                        exclusion_breakdown=None, status="ok", error=None)
                print(f"  {source}/{obj}: fetched={fetched} new={new} (rest updated in place)")
            except Exception as e:
                conn.rollback()
                log_run(conn, activity_date=today, source=source, object_type=obj,
                        started_at=started, finished_at=datetime.now(timezone.utc),
                        rows_fetched=None, rows_new=None, rows_excluded=None,
                        exclusion_breakdown=None, status="error", error=str(e)[:500])
                print(f"  {source}/{obj}: ERROR {e}")
                all_failures.append((source, obj, str(e)))

    conn.close()
    if all_failures:
        print(f"=== done with {len(all_failures)} FAILED job(s): "
              + ", ".join(f"{s}/{o} ({d})" for s, o, d in
                          [(s, o, str(e)[:60]) for s, o, e in all_failures]) + " ===")
        sys.exit(1)  # every job ran, but surface the failure to the scheduler
    print("=== done ===")


if __name__ == "__main__":
    main()
