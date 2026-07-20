-- Phase 7 groundwork: SAO monthly table + weekly/monthly trend views
-- + the dashboard's read-only role and its deliberate grants.
--
-- PURELY ADDITIVE and READ-ONLY over Phases 0-5: this file only CREATEs new
-- objects and GRANTs read access. Dropping everything here leaves Phases 0-5
-- exactly as they were:
--   drop view rep_weekly_trend, rep_monthly_scorecard;
--   drop table sao_monthly;
--   -- (drop owned/granted privileges with the role if ever needed:)
--   drop owned by dashboard_reader; drop role dashboard_reader;
--
-- Design decisions (PM, 2026-07-20 — see decisions.md + docs/dashboard.md):
--  * The trend views WRAP rep_scorecard() per calendar week / month instead
--    of re-implementing any counting SQL — zero definition drift possible:
--    a weekly cell is literally the verified scorecard run for that week.
--  * Weeks are Monday-start (date_trunc 'week'); activity history begins
--    Mon 2026-07-06, so week 1 is exactly the first tracked week. The
--    current week/month rows are partial by construction — the dashboard
--    labels them; the views don't hide them.
--  * sao_monthly mirrors Ray's "Global CA Performance Tracker" (SAO Monthly
--    Performance tab) verbatim — one row per rep per month, loaded by
--    sao/load_sao.py (full refresh). rep_name is normalized to dim_ca.name
--    for roster reps (alias map lives in the loader); former/inactive reps
--    keep Ray's spelling. We mirror values, we don't reinterpret:
--    quirks in the sheet surface as-is (the loader checksums rep sums
--    against the sheet's own Overall/UK/US totals and warns on drift).
--  * dashboard_reader is created here NOLOGIN so the migration is replayable
--    and secret-free; LOGIN + password are set manually out-of-band. The
--    grants below are the dashboard's ENTIRE visible universe — exposing a
--    new object to it is a deliberate GRANT in a future migration, never
--    automatic.

-- ---------------------------------------------------------------------------
-- SAO monthly (Ray's tracker mirror)
-- ---------------------------------------------------------------------------
create table if not exists sao_monthly (
    rep_name      text not null,           -- dim_ca.name for roster reps; Ray's spelling otherwise
    month         date not null,           -- first day of the month
    saos          numeric,                 -- SAOs Achieved
    sao_target    numeric,                 -- SAOs Target
    saos_inbound  numeric,                 -- Inbound SAOs block
    saos_event    numeric,                 -- Event SAOs block
    pipeline_usd  numeric,                 -- Pipeline Created ($, parsed)
    status        text,                    -- Active / Inactive
    is_ramping    boolean,                 -- "(Ramping)" marker
    team          text,                    -- e.g. PhysAI UK
    start_date    date,
    end_date      date,
    loaded_at     timestamptz not null default now(),
    primary key (rep_name, month)
);

comment on table sao_monthly is
  'Mirror of Ray''s Global CA Performance Tracker (SAO Monthly Performance tab). '
  'Loaded by sao/load_sao.py from a CSV export; full refresh per load. '
  'Outbound SAOs = saos - coalesce(saos_inbound,0) - coalesce(saos_event,0).';

-- ---------------------------------------------------------------------------
-- Trend views: the verified scorecard, evaluated per week / per month
-- ---------------------------------------------------------------------------
create or replace view rep_weekly_trend as
select w.week_start::date                         as week_start,
       (w.week_start + interval '6 days')::date   as week_end,
       s.*
from generate_series(
         date_trunc('week', (select min(activity_date) from activity_flat))::date,
         date_trunc('week', current_date)::date,
         interval '7 days') as w(week_start)
cross join lateral rep_scorecard(
         w.week_start::date, (w.week_start + interval '6 days')::date) as s;

create or replace view rep_monthly_scorecard as
select m.month_start::date                                as month_start,
       (m.month_start + interval '1 month - 1 day')::date as month_end,
       s.*
from generate_series(
         date_trunc('month', (select min(activity_date) from activity_flat))::date,
         date_trunc('month', current_date)::date,
         interval '1 month') as m(month_start)
cross join lateral rep_scorecard(
         m.month_start::date, (m.month_start + interval '1 month - 1 day')::date) as s;

-- ---------------------------------------------------------------------------
-- Dashboard read-only role + its deliberate, complete grant list
-- ---------------------------------------------------------------------------
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'dashboard_reader') then
        create role dashboard_reader nologin;
    end if;
end $$;

grant usage on schema public to dashboard_reader;

grant select on
    activity_flat,
    dim_ca, dim_account, dim_contact,
    rep_scorecard_7d, rep_scorecard_30d, rep_scorecard_alltime,
    rep_account_drilldown_alltime,
    account_contact_drilldown_alltime,
    owned_account_coverage_alltime,
    rep_weekly_trend, rep_monthly_scorecard,
    sao_monthly
to dashboard_reader;

grant execute on function
    rep_scorecard(date, date),
    rep_account_drilldown(date, date),
    account_contact_drilldown(date, date),
    owned_account_coverage(date, date)
to dashboard_reader;

-- Row-level security: several tables have RLS enabled (Supabase posture), and
-- RLS-without-policy reads as EMPTY for non-owner roles. The scorecard/drill
-- functions are SECURITY INVOKER, so when dashboard_reader calls them they
-- read dim_ca as dashboard_reader — it needs an explicit read policy. Same
-- for direct reads of sao_monthly. Deliberately NO policy on the activity
-- fact table: the dashboard reaches activity only through the activity_flat
-- view (which runs as the view's owner) — direct table access stays closed.
alter table sao_monthly enable row level security;

do $$
begin
    if not exists (select 1 from pg_policies where schemaname = 'public'
                   and tablename = 'dim_ca' and policyname = 'dashboard_reader_select') then
        create policy dashboard_reader_select on dim_ca
            for select to dashboard_reader using (true);
    end if;
    if not exists (select 1 from pg_policies where schemaname = 'public'
                   and tablename = 'sao_monthly' and policyname = 'dashboard_reader_select') then
        create policy dashboard_reader_select on sao_monthly
            for select to dashboard_reader using (true);
    end if;
end $$;
