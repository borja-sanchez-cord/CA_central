#!/usr/bin/env python3
"""
CA Activity Visibility — Phase 3: build the unified activity model.

Reads the RAW landing tables + the IDENTITY tables (never the source APIs)
and full-rebuilds the `activity` fact table: one row = one real activity
event, deduplicated and attributed per docs/spec.md §3. The flat analytics
view (activity_flat) and the dimension views are defined by migrations/ and
sit on top of this table — nothing downstream reads raw directly.

Same design contract as identity/resolve.py:
- FULL REBUILD, deterministically, in ONE commit. Safe to re-run any time; a
  crash mid-run leaves the previous snapshot intact. Fixing a rule = edit +
  re-run, never patch rows.
- All judgment lives in model/rules.py (pure functions, covered by tests on
  real validated fixtures). This file is plumbing: load, apply, write, report.

THE INVARIANT this build enforces and verifies at the end of every run: every
raw activity row lands in EXACTLY ONE activity row's source_ids. Nothing is
dropped — rows that must not be counted stay present with counts=false and an
excluded_reason, so any dashboard number can be audited down to the raw
records (spec §6). A broken invariant fails the run loudly.

excluded_reason values:
  invite_email             calendar invite/notification (belongs to the meeting
                           channel; the meeting OBJECT is the counted record)
  email_task_shadow        AmpleMarket email task — the HubSpot-synced send is
                           the counted record (tasks are to-dos, not sends)
  call_task_shadow         AmpleMarket phone_call task — /calls records are the
                           counted dials (task_id can't link them; usually null)
  non_ca_sender            outbound email from an internal, non-CA address
                           (AEs, marketing, parked tryencord senders)
  non_ca_user              AmpleMarket task/call by a non-CA user
  non_ca_meeting           meeting with no CA among its internal attendees
  internal_only_recipients outbound with no external address in to ∪ cc ∪ bcc
  noise_auto_generated     inbound bounce/auto-reply/platform notification
  inbound_no_ca_recipient  inbound where no CA address is among the recipients
  no_sender                email row whose sender is missing/unparseable

Run:  python model/build_activity.py
"""
import os
import sys

import psycopg2
from psycopg2.extras import Json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ingestion"))
sys.path.insert(0, os.path.join(_HERE, "..", "identity"))
sys.path.insert(0, _HERE)
from ingest import load_env, require, _extract_emails
from resolve import rebuild  # the shared delete+insert-no-commit rebuild helper
import rules
import vocab  # the controlled label vocabulary (channels / excluded_reasons)

COLUMNS = ["activity_id", "source", "channel", "direction", "ca_id", "ca_ids",
           "contact_id", "company_id", "contact_email", "occurred_at",
           "activity_date", "is_automated", "automated_confidence", "subject",
           "subject_norm", "body_preview", "body_html", "outcome", "counts",
           "excluded_reason", "dup_count", "source_ids", "logged_by",
           "call_group_id", "is_conversation"]


def load_identity(conn):
    """The Phase 2 outputs every attribution decision reads."""
    with conn.cursor() as cur:
        cur.execute("select address, ca_id from dim_ca_address")
        ca_addresses = dict(cur.fetchall())
        cur.execute("select ca_id from dim_ca")
        ca_roster = {r[0] for r in cur.fetchall()}
        cur.execute("select amplemarket_user_id, ca_id from ca_amplemarket_user")
        ample_user_ca = dict(cur.fetchall())
        cur.execute("select email, hubspot_contact_id, resolved_company_id from contact_crosswalk")
        contact_by_email = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        cur.execute("""select amplemarket_contact_id, email, hubspot_contact_id
                       from amplemarket_contact_map""")
        ample_contact = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    if not ca_roster:
        sys.exit("FATAL: dim_ca is empty — run identity/resolve.py before the model build.")
    return ca_addresses, ca_roster, ample_user_ca, contact_by_email, ample_contact


def _contact_fields(email, contact_by_email):
    """email -> (contact_id, company_id, email) via the contact crosswalk."""
    if not email:
        return None, None, None
    hit = contact_by_email.get(email)
    return (hit[0], hit[1], email) if hit else (None, None, email)


# -------------------------------------------------------------------- emails
def build_emails(conn, ca_addresses, contact_by_email):
    """Raw HubSpot email rows -> deduplicated, attributed activity rows.
    body_html is fetched afterwards for canonical rows only (it's the heavy
    column; only the surviving copy carries it into the fact table)."""
    with conn.cursor() as cur:
        cur.execute("""select id, hs_timestamp, subject, object_source,
                              object_source_detail, from_email, to_email,
                              cc_email, bcc_email, body_preview, activity_date
                       from raw_hubspot_emails""")
        cols = [d[0] for d in cur.description]
        prepped = [rules.prep_email(dict(zip(cols, r))) for r in cur.fetchall()]

    rows = []
    for cluster in rules.cluster_emails(prepped):
        s = rules.summarize_email_cluster(cluster, ca_addresses)  # all judgment (pure, tested)
        can = s["canonical"]
        source_ids = [{"table": "raw_hubspot_emails", "id": r["id"],
                       "detail": r["object_source_detail"] or r["object_source"]}
                      for r in sorted(cluster, key=lambda r: rules.id_key(r["id"]))]
        logged_by = sorted({r["object_source_detail"] or r["object_source"] or "unknown"
                            for r in cluster})
        contact_id, company_id, contact_email = _contact_fields(s["contact_email"],
                                                                contact_by_email)
        rows.append([
            f"hs_email:{can['id']}", "hubspot", s["channel"], s["direction"], s["ca_id"], None,
            contact_id, company_id, contact_email, can["ts"], can["activity_date"],
            s["is_automated"], s["automated_confidence"], can["subject"], can["subject_norm"],
            can["body_preview"], None,  # body_html backfilled below
            None, s["counts"], s["excluded_reason"], len(cluster), Json(source_ids), logged_by,
            None, None,
        ])

    # attach body_html to canonical rows only, in chunks (heavy column)
    by_canonical = {r[0].split(":", 1)[1]: r for r in rows}
    ids = list(by_canonical)
    with conn.cursor() as cur:
        for i in range(0, len(ids), 500):
            cur.execute("select id, body_html from raw_hubspot_emails where id = any(%s)",
                        (ids[i:i + 500],))
            for rid, body in cur.fetchall():
                by_canonical[rid][16] = body
    return rows


# ------------------------------------------------------------------ meetings
def build_meetings(conn, ca_roster):
    with conn.cursor() as cur:
        cur.execute("""select id, hs_timestamp, title, object_source,
                              object_source_detail, owner_id, outcome,
                              attendee_owner_ids, start_time, activity_date
                       from raw_hubspot_meetings""")
        cols = [d[0] for d in cur.description]
        meetings = [dict(zip(cols, r)) for r in cur.fetchall()]

    rows = []
    for group in rules.dedupe_meetings(meetings):
        can = rules.pick_canonical_meeting(group)
        cas = sorted({c for m in group for c in rules.meeting_ca_ids(m, ca_roster)},
                     key=rules.id_key)
        source_ids = [{"table": "raw_hubspot_meetings", "id": m["id"],
                       "detail": m["object_source_detail"] or m["object_source"]}
                      for m in sorted(group, key=lambda m: rules.id_key(m["id"]))]
        logged_by = sorted({m["object_source_detail"] or m["object_source"] or "unknown"
                            for m in group})
        rows.append([
            f"hs_meeting:{can['id']}", "hubspot", "meeting", "outbound",
            cas[0] if cas else None, cas or None, None, None, None,
            can["start_time"] or can["hs_timestamp"], can["activity_date"],
            None, None, can["title"], None, None, None,
            can["outcome"], bool(cas), None if cas else "non_ca_meeting",
            len(group), Json(source_ids), logged_by, None, None,
        ])
    return rows


# --------------------------------------------------------------------- calls
def build_calls(conn, ample_user_ca, ample_contact, contact_by_email):
    with conn.cursor() as cur:
        cur.execute("""select id, user_id, start_date, duration, answered, human,
                              contact_id, contact_email, activity_date
                       from raw_amplemarket_calls""")
        cols = [d[0] for d in cur.description]
        calls = [dict(zip(cols, r)) for r in cur.fetchall()]

    groups = rules.group_calls(calls)
    rows = []
    for c in calls:
        ca_id = ample_user_ca.get(c["user_id"])
        email = None
        if c["contact_id"] and c["contact_id"] in ample_contact:
            email = ample_contact[c["contact_id"]][0]
        elif c["contact_email"]:
            hits = _extract_emails(c["contact_email"])
            email = hits[0] if hits else None
        contact_id, company_id, email = _contact_fields(email, contact_by_email)
        human = bool(c["human"])
        rows.append([
            f"am_call:{c['id']}", "amplemarket", "call", "outbound", ca_id, None,
            contact_id, company_id, email, c["start_date"], c["activity_date"],
            False, "high", None, None, None, None,
            "conversation" if human else "attempt",
            ca_id is not None, None if ca_id else "non_ca_user",
            1, Json([{"table": "raw_amplemarket_calls", "id": c["id"]}]),
            ["amplemarket"], groups[c["id"]], human,
        ])
    return rows


# --------------------------------------------------------------------- tasks
def build_tasks(conn, ample_user_ca, ample_contact, contact_by_email):
    with conn.cursor() as cur:
        cur.execute("""select id, user_id, type, automatic, finished_on,
                              contact_id, contact_email, activity_date
                       from raw_amplemarket_tasks""")
        cols = [d[0] for d in cur.description]
        tasks = [dict(zip(cols, r)) for r in cur.fetchall()]

    rows = []
    for t in tasks:
        channel = rules.task_channel(t["type"])
        ca_id = ample_user_ca.get(t["user_id"])
        email = None
        if t["contact_id"] and t["contact_id"] in ample_contact:
            email = ample_contact[t["contact_id"]][0]
        elif t["contact_email"]:
            hits = _extract_emails(t["contact_email"])
            email = hits[0] if hits else None
        contact_id, company_id, email = _contact_fields(email, contact_by_email)

        if channel == "email_task":
            counts, reason = False, "email_task_shadow"
        elif channel == "call_task":
            counts, reason = False, "call_task_shadow"
        elif ca_id is None:
            counts, reason = False, "non_ca_user"
        else:
            counts, reason = True, None
        rows.append([
            f"am_task:{t['id']}", "amplemarket", channel, "outbound", ca_id, None,
            contact_id, company_id, email, t["finished_on"], t["activity_date"],
            bool(t["automatic"]), "high", None, None, None, None, None,
            counts, reason, 1, Json([{"table": "raw_amplemarket_tasks", "id": t["id"]}]),
            ["amplemarket"], None, None,
        ])
    return rows


# ------------------------------------------------------------------- verify
def verify_invariant(conn, rows):
    """Every raw activity row is in exactly one activity row's source_ids."""
    per_table = {}
    for r in rows:
        for s in r[21].adapted:  # source_ids Json payload
            per_table[s["table"]] = per_table.get(s["table"], 0) + 1
    problems = []
    with conn.cursor() as cur:
        for table, referenced in sorted(per_table.items()):
            cur.execute(f"select count(*) from {table}")
            raw = cur.fetchone()[0]
            status = "OK" if raw == referenced else "MISMATCH"
            print(f"  invariant {table}: raw={raw} referenced={referenced} {status}")
            if raw != referenced:
                problems.append(table)
    if problems:
        sys.exit(f"FATAL: raw rows lost or double-referenced in: {', '.join(problems)} "
                 "— the build would miscount; nothing was committed.")


def verify_vocab(rows):
    """Every emitted channel / excluded_reason must be a declared label
    (model/vocab.py). Catches a typo or a new, undeclared category before it
    ships and silently drops out of the scorecard counts (which filter on
    these exact strings). Column positions are looked up from COLUMNS, so this
    keeps working if the column order ever changes."""
    ci = COLUMNS.index("channel")
    ri = COLUMNS.index("excluded_reason")
    bad_ch = sorted({r[ci] for r in rows if r[ci] not in vocab.CHANNELS})
    bad_rs = sorted({r[ri] for r in rows
                     if r[ri] is not None and r[ri] not in vocab.EXCLUDED_REASONS})
    if bad_ch or bad_rs:
        sys.exit(f"FATAL: undeclared labels — channels={bad_ch} reasons={bad_rs}. "
                 "If intentional, add them to model/vocab.py; nothing was committed.")
    print(f"  vocab: {len(vocab.CHANNELS)} channels / {len(vocab.EXCLUDED_REASONS)} "
          "reasons declared — all emitted values recognised OK")


def main():
    load_env()
    conn = psycopg2.connect(require("SUPABASE_DB_URL"), connect_timeout=20)

    ca_addresses, ca_roster, ample_user_ca, contact_by_email, ample_contact = load_identity(conn)

    rows = (build_emails(conn, ca_addresses, contact_by_email)
            + build_meetings(conn, ca_roster)
            + build_calls(conn, ample_user_ca, ample_contact, contact_by_email)
            + build_tasks(conn, ample_user_ca, ample_contact, contact_by_email))

    verify_invariant(conn, rows)  # before any write: a bad build never lands
    verify_vocab(rows)            # and every label must be a declared one

    rebuild(conn, "activity", COLUMNS, [tuple(r) for r in rows])
    conn.commit()  # the single data commit — all-or-nothing snapshot swap

    # ---- report (plain, PM-readable) ----
    counted = [r for r in rows if r[18]]
    excluded = [r for r in rows if not r[18]]
    print(f"=== Phase 3 activity model ===")
    print(f"activity rows: {len(rows)}  (counted: {len(counted)}, kept-but-excluded: {len(excluded)})")
    by_channel = {}
    for r in counted:
        key = (r[2], r[3])
        by_channel[key] = by_channel.get(key, 0) + 1
    print("counted, by channel:")
    for (ch, d), n in sorted(by_channel.items(), key=lambda kv: -kv[1]):
        print(f"   {ch:14} {d:9} {n}")
    by_reason = {}
    for r in excluded:
        by_reason[r[19]] = by_reason.get(r[19], 0) + 1
    print("excluded (kept for audit), by reason:")
    for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"   {reason:26} {n}")
    dups = sum(r[20] - 1 for r in rows)
    print(f"duplicate raw copies collapsed: {dups}")
    print("=== done ===")
    conn.close()


if __name__ == "__main__":
    main()
