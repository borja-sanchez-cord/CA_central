-- 001: Phase 3 — the unified activity model.
--
-- The normalized schema (roadmap Phase 3) starts here: from this file on,
-- every schema change to the MODEL layer is a numbered migration in this
-- folder, applied by migrations/apply.py (which runs in the daily workflow).
-- The raw landing tables and identity tables keep their existing in-script
-- DDL (ingestion/ingest.py, identity/resolve.py) — they predate migrations
-- and are append-only/full-rebuild layers with their own recovery story.
--
-- Prerequisites: the raw tables (ingest.py) and identity tables (resolve.py)
-- must exist — in any real environment they do, because the pipeline runs
-- ingest -> resolve -> apply migrations -> build model.

-- One row = one real activity event, after dedup/attribution (spec §2 grain).
-- Every raw activity row lands in EXACTLY ONE activity row's source_ids —
-- nothing is dropped, non-countable rows are kept with counts=false and an
-- excluded_reason, so any number is auditable down to the raw records (§6).
create table if not exists activity (
    activity_id text primary key,        -- '<kind>:<canonical raw id>', deterministic
    source text not null,                -- hubspot | amplemarket
    channel text not null,               -- call | meeting | manual_email | auto_email |
                                         -- inbound_email | li_message | li_connect | li_other |
                                         -- whatsapp | sms | email_task | call_task | other
    direction text not null,             -- outbound (rep effort) | inbound (engagement)
    ca_id text,                          -- resolved CA (null = not attributable to a CA)
    ca_ids text[],                       -- meetings only: ALL attending CAs (ca_id = first)
    contact_id text,                     -- resolved HubSpot contact id
    company_id text,                     -- resolved (canonical) HubSpot company id
    contact_email text,                  -- the matched person email (audit convenience)
    occurred_at timestamptz,
    activity_date date not null,
    is_automated boolean,
    automated_confidence text,           -- high (AmpleMarket flag) | low (HubSpot source proxy)
    subject text,                        -- as logged (canonical copy)
    subject_norm text,                   -- tool+reply prefixes stripped (dedup/debug aid)
    body_preview text,
    body_html text,                      -- carried from raw (spec §7; v2 reads this)
    outcome text,                        -- meetings: hs outcome; calls: conversation | attempt
    counts boolean not null,             -- true = counted CA activity/engagement
    excluded_reason text,                -- why counts=false (see build_activity.py header)
    dup_count integer not null default 1,-- raw rows collapsed into this event
    source_ids jsonb not null,           -- [{"table":..,"id":..,"detail":..}] full audit trail
    logged_by text[],                    -- distinct sources that logged a copy
    call_group_id text,                  -- calls: dial-group key (same contact, tight window)
    is_conversation boolean,             -- calls: human=true on any dial in ROW (per-dial rows)
    built_at timestamptz not null default now()
);

create index if not exists activity_date_idx on activity (activity_date);
create index if not exists activity_ca_idx on activity (ca_id, activity_date);
create index if not exists activity_channel_idx on activity (channel, activity_date);
create index if not exists activity_company_idx on activity (company_id);

-- Dimension VIEWS (spec §2 account/contact/rep): zero-copy over the raw
-- mirrors + identity crosswalks — always as fresh as the layers below, no
-- second copy of 154k companies to keep in sync.
create or replace view dim_account as
select c.id                        as account_id,
       c.name,
       c.domain,
       c.icp_tier_validated,
       c.icp_tier_new,
       c.vertical,
       nullif(c.target_account_owner, '') as owner_rep_id,
       (nullif(c.target_account_owner, '') is not null) as is_target
from raw_hubspot_companies c
join company_crosswalk x on x.hubspot_company_id = c.id and x.is_canonical
where x.resolved_company_id = c.id;

create or replace view dim_contact as
select ct.id                       as contact_id,
       lower(ct.email)             as email,
       ct.firstname, ct.lastname, ct.jobtitle, ct.lifecyclestage,
       cw.resolved_company_id      as account_id
from raw_hubspot_contacts ct
left join contact_crosswalk cw on cw.hubspot_contact_id = ct.id
                              and cw.email = lower(ct.email);

-- THE flat analytics view (spec §1): one row per activity with rep, contact
-- and account attached. Everything downstream (Phase 4+) reads this.
create or replace view activity_flat as
select a.*,
       ca.name              as ca_name,
       ca.primary_email     as ca_email,
       dc.firstname         as contact_firstname,
       dc.lastname          as contact_lastname,
       dc.jobtitle          as contact_jobtitle,
       acc.name             as account_name,
       acc.domain           as account_domain,
       acc.icp_tier_validated as account_icp_tier_validated,
       acc.icp_tier_new     as account_icp_tier_new,
       acc.vertical         as account_vertical,
       acc.is_target        as account_is_target
from activity a
left join dim_ca ca        on ca.ca_id = a.ca_id
left join dim_contact dc   on dc.contact_id = a.contact_id
left join dim_account acc  on acc.account_id = a.company_id;
