#!/usr/bin/env python3
"""
CA Activity Visibility — HubSpot DEALS mirror (Dillon fix #24 + #25 groundwork).

Brings in the one source the neglect view was missing: whether an account is a
customer, mid-deal, or recently closed-lost/churned. Feasibility verified live
2026-07-22 (read-only probe, decisions.md): 4 pipelines, ~6,093 deals, company
associations present, and stage class is derivable from HubSpot's own stage
metadata (isClosed + probability) — no hardcoded stage ids anywhere.

Design (agreed with PM 2026-07-22 — mirrors the #22 additive pattern):
- ADDITIVE AND SEPARATE. Three new tables only. ingest.py, identity/resolve.py
  and model/build_activity.py are not touched; `activity`/`activity_flat` are
  proven byte-identical before/after this script's first run. Everything that
  reads these tables lives in migrations/008 (a droppable, read-only layer).
- Full sweep every run: ALL deals are re-read each time (~61 paged calls —
  minutes), because deals MUTATE (stage moves, close dates land). These are
  entity mirrors like raw_hubspot_companies, not append-only event tables:
  the upsert refreshes every mutable column on conflict, and the plain paging
  endpoint (not Search) is used so the sweep never hits Search's 10k cap and
  never trusts a lagging index (the watermark gotcha of 2026-07-20).
- ONE sweep timestamp per run, stamped on every row seen, in a single
  transaction per table (upsert = one commit). Deals deleted in HubSpot are
  never deleted here (raw-layer posture) but stop receiving the stamp, so
  migration 008 reads only the latest completed sweep — a deleted open deal
  cannot shield an account forever.
- The pipeline/stage SCHEMA is mirrored too (raw_hubspot_deal_stages), so the
  won/lost/open classification lives in data, self-maintains when stages are
  renamed or added, and is auditable in SQL.
- Logged to ingestion_runs (source=hubspot, object_type=deals) — the audit
  rule: anything writing a raw table must log there.

Run:  python ingestion/ingest_deals.py     (no arguments)
"""
import sys
import time
from datetime import datetime, timezone, date

import psycopg2

from ingest import load_env, require, http_get, upsert, log_run, parse_ts, HS_BASE

PROPS = ["dealname", "pipeline", "dealstage", "closedate", "createdate",
         "amount", "hubspot_owner_id"]

DDL = """
create table if not exists raw_hubspot_deals (
    id                text primary key,          -- HubSpot deal id
    dealname          text,
    pipeline          text,                      -- pipeline id (see deal_stages)
    dealstage         text,                      -- stage id (see deal_stages)
    closedate         timestamptz,               -- null while open / never set
    createdate        timestamptz,
    amount            numeric,
    hubspot_owner_id  text,
    last_seen_at      timestamptz not null,      -- sweep stamp; see module doc
    ingested_at       timestamptz not null default now()
);
create table if not exists raw_hubspot_deal_companies (
    id            text primary key,              -- '<deal_id>:<company_id>'
    deal_id       text not null,
    company_id    text not null,                 -- raw HubSpot company id
    last_seen_at  timestamptz not null,
    ingested_at   timestamptz not null default now()
);
create index if not exists rhdc_deal_idx on raw_hubspot_deal_companies (deal_id);
create table if not exists raw_hubspot_deal_stages (
    id              text primary key,            -- '<pipeline_id>:<stage_id>'
    pipeline_id     text not null,
    pipeline_label  text,
    stage_id        text not null,
    stage_label     text,
    display_order   int,
    is_closed       boolean,                     -- HubSpot stage metadata
    probability     numeric,                     -- 1 = won, 0 = lost
    last_seen_at    timestamptz not null,
    ingested_at     timestamptz not null default now()
);
"""


def fetch_stage_schema(token):
    """Every pipeline's stages with the metadata that classifies them."""
    headers = {"Authorization": f"Bearer {token}"}
    data = http_get(f"{HS_BASE}/crm/v3/pipelines/deals", headers)
    rows = []
    for p in data.get("results", []):
        for s in p.get("stages", []):
            meta = s.get("metadata", {})
            prob = meta.get("probability")
            rows.append((f"{p['id']}:{s['id']}", p["id"], p.get("label"),
                         s["id"], s.get("label"), s.get("displayOrder"),
                         meta.get("isClosed") == "true",
                         float(prob) if prob not in (None, "") else None))
    return rows


def fetch_all_deals(token):
    """All deals + their company associations via the plain paging endpoint
    (NOT Search: no 10k result cap, no lagging index; associations come back
    inline so no extra batch calls are needed)."""
    headers = {"Authorization": f"Bearer {token}"}
    url = (f"{HS_BASE}/crm/v3/objects/deals?limit=100&archived=false"
           f"&properties={','.join(PROPS)}&associations=companies")
    deals, links, after = [], [], None
    while True:
        data = http_get(url + (f"&after={after}" if after else ""), headers)
        for r in data.get("results", []):
            p = r.get("properties", {})
            amt = p.get("amount")
            deals.append((r["id"], p.get("dealname"), p.get("pipeline"),
                          p.get("dealstage"), parse_ts(p.get("closedate")),
                          parse_ts(p.get("createdate")),
                          float(amt) if amt not in (None, "") else None,
                          p.get("hubspot_owner_id")))
            for c in (r.get("associations", {}).get("companies", {})
                       .get("results", [])):
                links.append((f"{r['id']}:{c['id']}", r["id"], str(c["id"])))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        time.sleep(0.15)  # ~61 pages; stay far under HubSpot's rate limits
        if not after:
            break
    # inline associations repeat a company per association TYPE — dedup on id
    links = list({l[0]: l for l in links}.values())
    return deals, links


def main():
    load_env()
    token = require("HUBSPOT_PRIVATE_APP_TOKEN")
    conn = psycopg2.connect(require("SUPABASE_DB_URL"), connect_timeout=20)
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()

    started = datetime.now(timezone.utc)
    sweep = started  # ONE stamp for the whole run — 008 keys on max(last_seen_at)
    try:
        stages = fetch_stage_schema(token)
        deals, links = fetch_all_deals(token)

        # order matters for the (accepted, self-healing) crash window: stages
        # and links land BEFORE deals, so max(deals.last_seen_at) — the gate
        # migration 008 reads through — only advances once its inputs are in.
        upsert(conn, "raw_hubspot_deal_stages",
               ["id", "pipeline_id", "pipeline_label", "stage_id", "stage_label",
                "display_order", "is_closed", "probability", "last_seen_at"],
               [r + (sweep,) for r in stages],
               update_cols=["pipeline_label", "stage_label", "display_order",
                            "is_closed", "probability", "last_seen_at"])
        upsert(conn, "raw_hubspot_deal_companies",
               ["id", "deal_id", "company_id", "last_seen_at"],
               [r + (sweep,) for r in links], update_cols=["last_seen_at"])
        new = upsert(conn, "raw_hubspot_deals",
                     ["id", "dealname", "pipeline", "dealstage", "closedate",
                      "createdate", "amount", "hubspot_owner_id", "last_seen_at"],
                     [r + (sweep,) for r in deals],
                     update_cols=["dealname", "pipeline", "dealstage", "closedate",
                                  "createdate", "amount", "hubspot_owner_id",
                                  "last_seen_at"])

        print(f"deals: {len(deals)} swept ({new} new), {len(links)} company links, "
              f"{len(stages)} pipeline stages, "
              f"{sum(1 for d in deals if d[2] is None or d[3] is None)} without pipeline/stage")
        log_run(conn, activity_date=date.today(), source="hubspot",
                object_type="deals", started_at=started,
                finished_at=datetime.now(timezone.utc), rows_fetched=len(deals),
                rows_new=new, rows_excluded=0, exclusion_breakdown=None,
                status="ok", error=None)
    except Exception as e:
        conn.rollback()  # a failed write leaves the connection aborted — clear it
        log_run(conn, activity_date=date.today(), source="hubspot",
                object_type="deals", started_at=started,
                finished_at=datetime.now(timezone.utc), rows_fetched=None,
                rows_new=None, rows_excluded=None, exclusion_breakdown=None,
                status="error", error=str(e)[:500])
        sys.exit(f"deals FAILED: {e}")
    conn.close()


if __name__ == "__main__":
    main()
