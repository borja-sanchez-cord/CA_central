-- 008: Dillon fix #24 + #25 — deal-aware neglect labels.
--
-- PURELY ADDITIVE and READ-ONLY over Phases 0-7: two views + grants. Reads
-- the deals mirror (raw_hubspot_deals / _deal_companies / _deal_stages,
-- ingested by ingestion/ingest_deals.py) and company_crosswalk. Writes
-- nothing; activity_flat, owned_account_coverage and all counting are NOT
-- modified — the dashboard joins these labels on top (display-only join of
-- two approved surfaces, the same pattern as 006/007).
-- Dropping everything here restores the pre-008 state exactly:
--   drop view account_deal_status, deal_stage_class;
--
-- THE RULES (Dillon, answered 2026-07-22 — see decisions.md):
-- an owned account with zero touches is NOT neglected when any of these hold
-- (priority order; the account gets a LABEL, it is never hidden or dropped):
--   customer      a Closed-won deal (any pipeline) NOT superseded by a later
--                 churn — never flagged while it holds. "Superseded": churn
--                 closing on/after the last won deal ends customer status;
--                 otherwise every churned account (which by nature has an old
--                 won deal) would be shielded forever and the 9-month churn
--                 rule below would be dead code — caught live 2026-07-22
--                 (latest churn 2026-04-15 produced zero churn labels).
--   open deal     any deal in an open stage, any pipeline — never flagged
--                 while open. Caveat (Dillon, Ray owns the process fix):
--                 stale open deals would hide real neglect, so the open
--                 deal's AGE is surfaced next to the label — a zombie deal
--                 is auditable, not invisible.
--   churned       Opportunity "Churned" stage OR a lost Renewal (a lost
--                 renewal is a churn) — suppressed ~9 months from close,
--                 then flags again (churns don't re-engage quickly).
--   closed lost   any other lost NEW-BUSINESS deal (Opportunity Closed Lost;
--                 Partnerships closed-lost defaulted here) — suppressed 60
--                 days from close, then flags again.
-- Expansion pipeline = AM remit: its deals never start a lost-clock for CAs
-- (those accounts are existing customers — the customer rule covers them).
--
-- Classification comes from HubSpot's own stage METADATA (isClosed +
-- probability: 1 = won, 0 = lost), never hardcoded stage ids — verified live
-- 2026-07-22 to split all 27 stages across the 4 pipelines correctly. The
-- churn/renewal/expansion distinctions match on pipeline/stage LABELS
-- (ilike), the only place HubSpot expresses them; a rename degrades a churn
-- into the safer 60-day bucket, never into silence.
--
-- Safe defaults (all verified against the live mirror, 2026-07-22):
--   * a lost deal with NO closedate runs no clock -> account stays flaggable
--     (275 old closed deals lack closedate; being flaggable is the honest
--     default for stale data). A WON deal with no closedate still makes a
--     customer — unless a dated churn exists, which then supersedes it (the
--     dated signal outranks the undated one);
--   * 6 pre-pipeline deals with null pipeline/stage classify as nothing;
--   * only deals present in the LATEST completed sweep play (last_seen_at
--     gate) -> a deal deleted in HubSpot stops shielding on the next sweep.

-- ------------------------------------------------ stage id -> won/lost/open
create or replace view deal_stage_class as
select s.pipeline_id,
       s.pipeline_label,
       s.stage_id,
       s.stage_label,
       case when s.is_closed and s.probability >= 1 then 'won'
            when s.is_closed                        then 'lost'
            else 'open' end                           as class,
       case when s.is_closed and s.probability < 1 then
            case when s.pipeline_label ilike '%renew%'
                   or s.stage_label    ilike '%churn%' then 'churn'
                 else 'new_business' end
       end                                            as loss_kind,
       s.pipeline_label ilike '%expansion%'           as is_am_pipeline
from raw_hubspot_deal_stages s;

-- --------------------------------------------- per-account deal status flags
-- One row per canonical account that has at least one deal. Deals reach the
-- account through company_crosswalk (raw HubSpot company id -> resolved
-- canonical id), the identity layer's OUTPUT — no new matching logic here.
-- A deal linked to several companies shields them all (conservative).
create or replace view account_deal_status as
with live as (
    -- only the latest completed sweep: see ingest_deals.py module doc
    select * from raw_hubspot_deals
    where last_seen_at = (select max(last_seen_at) from raw_hubspot_deals)
),
deal_on_account as (
    select cw.resolved_company_id as account_id,
           d.closedate, d.createdate,
           c.class, c.loss_kind, c.is_am_pipeline
    from live d
    join raw_hubspot_deal_companies l on l.deal_id = d.id
    join company_crosswalk cw on cw.hubspot_company_id = l.company_id
    join deal_stage_class c on c.pipeline_id = d.pipeline
                           and c.stage_id = d.dealstage
),
per_account as (
    select account_id,
           bool_or(class = 'won')                       as has_won,
           max(closedate) filter (where class = 'won')  as last_won_at,
           bool_or(class = 'open')                      as has_open_deal,
           count(*) filter (where class = 'open')       as open_deals,
           min(createdate) filter (where class = 'open') as oldest_open_created,
           max(closedate) filter (where loss_kind = 'churn'
                                    and not is_am_pipeline) as last_churned_at,
           max(closedate) filter (where loss_kind = 'new_business'
                                    and not is_am_pipeline) as last_lost_at
    from deal_on_account
    group by account_id
),
flags as (
    select *,
           -- CURRENT customer: won, and not churned since. A churn dated
           -- on/after the last won (or a churn against an undated won)
           -- ends customer status — see the header rationale.
           (has_won and not (last_churned_at is not null
                             and (last_won_at is null
                                  or last_churned_at >= last_won_at)))
                                                        as is_customer
    from per_account
)
select account_id,
       is_customer,
       has_open_deal,
       open_deals,
       (current_date - oldest_open_created::date)       as oldest_open_deal_days,
       last_churned_at::date                            as last_churned_date,
       last_lost_at::date                               as last_lost_date,
       -- the label, highest-priority rule first; null = no shield, the
       -- account is flaggable as neglected like before
       case when is_customer                            then 'customer'
            when has_open_deal                          then 'open_deal'
            when last_churned_at >= current_date - interval '9 months'
                                                        then 'churned_recently'
            when last_lost_at    >= current_date - interval '60 days'
                                                        then 'lost_recently'
       end                                              as shield
from flags;

-- ------------------------------------------------------- dashboard exposure
-- Deliberate grants, per the contract in docs/dashboard.md: these two views
-- are the ONLY new things dashboard_reader can see. The three raw deal
-- tables get RLS with NO reader policy — reachable only through the
-- owner-executed views above, exactly the posture 004 and 006 take.
grant select on deal_stage_class, account_deal_status to dashboard_reader;

alter table raw_hubspot_deals          enable row level security;
alter table raw_hubspot_deal_companies enable row level security;
alter table raw_hubspot_deal_stages    enable row level security;
