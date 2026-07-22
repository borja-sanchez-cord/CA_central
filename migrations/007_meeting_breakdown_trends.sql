-- 007: the new-stakeholder meeting split, sliced per calendar week / month —
-- so the Trends and SAO-vs-activity screens can show it next to the measures
-- they already carry (Dillon fix #22 follow-through, agreed with PM 2026-07-21).
--
-- PURELY ADDITIVE and READ-ONLY: two views that WRAP rep_meeting_breakdown()
-- (migration 006) exactly the way migration 004's rep_weekly_trend /
-- rep_monthly_scorecard wrap rep_scorecard() — zero definition drift possible:
-- a weekly cell IS the verified breakdown run for that week. Dropping both
-- views restores the pre-007 state exactly:
--   drop view rep_meeting_breakdown_weekly, rep_meeting_breakdown_monthly;
--
-- Correctness note (the reason this is safe to slice): the 60-day walk in
-- meeting_new_stakeholder_flags is computed over FULL history and only THEN
-- filtered to the window — so an August follow-up whose "new" happened in
-- July stays a follow-up in the August slice. A per-window recomputation
-- would misclassify it; do not "optimise" the function that way.
--
-- Weeks are Monday-start, months calendar — identical to 004, so these join
-- rep_weekly_trend / rep_monthly_scorecard on (week_start|month_start, ca_id)
-- with no translation. Current week/month rows are partial by construction;
-- the dashboard labels them (004 note applies unchanged).

create or replace view rep_meeting_breakdown_weekly as
select w.week_start::date                         as week_start,
       (w.week_start + interval '6 days')::date   as week_end,
       b.*
from generate_series(
         date_trunc('week', (select min(activity_date) from activity_flat))::date,
         date_trunc('week', current_date)::date,
         interval '7 days') as w(week_start)
cross join lateral rep_meeting_breakdown(
         w.week_start::date, (w.week_start + interval '6 days')::date) as b;

create or replace view rep_meeting_breakdown_monthly as
select m.month_start::date                                as month_start,
       (m.month_start + interval '1 month - 1 day')::date as month_end,
       b.*
from generate_series(
         date_trunc('month', (select min(activity_date) from activity_flat))::date,
         date_trunc('month', current_date)::date,
         interval '1 month') as m(month_start)
cross join lateral rep_meeting_breakdown(
         m.month_start::date, (m.month_start + interval '1 month - 1 day')::date) as b;

-- Deliberate exposure (docs/dashboard.md contract): these two views are the
-- only new objects the dashboard login can see from this migration.
grant select on rep_meeting_breakdown_weekly,
                rep_meeting_breakdown_monthly
to dashboard_reader;
