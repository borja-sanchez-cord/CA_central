"""
Every SQL statement the dashboard runs, in one place.

Nothing here may reference anything outside the approved read surfaces
(docs/dashboard.md): activity_flat, the rep_scorecard / drill-down functions
and their preset views, rep_weekly_trend, rep_monthly_scorecard, sao_monthly,
dim_ca / dim_account / dim_contact. The dashboard_reader role enforces this,
but keeping all SQL in one reviewable file is the point.
"""

DATA_RANGE = """
    select min(activity_date) as first_day, max(activity_date) as last_day
    from activity_flat where counts
"""

SCORECARD = "select * from rep_scorecard(%s, %s) order by total_counted desc"

WEEKLY_TREND = """
    select * from rep_weekly_trend
    where week_start <= (select max(activity_date) from activity_flat where counts)
    order by week_start, ca_name
"""

MONTHLY_SCORECARD = "select * from rep_monthly_scorecard order by month_start, ca_name"

SAO_MONTHLY = "select * from sao_monthly order by month, rep_name"

REP_ACCOUNTS = """
    select account_name, icp_tier, owned_by_this_rep, touchpoints, people_touched,
           auto_email, manual_email, calls, linkedin, inbound_replies,
           other_outreach, first_touch, last_touch
    from rep_account_drilldown(%s, %s)
    where ca_name = %s
    order by touchpoints desc, account_name
"""

REP_ACCOUNTS_ALL = """
    select ca_name, account_name, icp_tier, touchpoints
    from rep_account_drilldown(%s, %s)
"""

ACCOUNT_CONTACTS = """
    select contact_name, contact_email, jobtitle, touchpoints, emails, calls,
           linkedin, inbound_replies, last_touch
    from account_contact_drilldown(%s, %s)
    where ca_name = %s and account_name = %s
    order by touchpoints desc
"""

OWNED_COVERAGE = """
    select owner_name, account_id, account_name, icp_tier, vertical, owner_touches,
           owner_last_touch, team_touches, team_last_touch, team_reps
    from owned_account_coverage(%s, %s)
    order by owner_name, (icp_tier is null), icp_tier, team_touches, account_name
"""

# Deal-derived neglect shields (migration 008, Dillon fix #24+#25). One row per
# account that has deals; joined onto OWNED_COVERAGE display-side — the
# coverage numbers themselves are never recomputed.
ACCOUNT_DEAL_STATUS = """
    select account_id, is_customer, has_open_deal, open_deals,
           oldest_open_deal_days, last_churned_date, last_lost_date, shield
    from account_deal_status
"""

AUDIT_ROWS = """
    select activity_date, occurred_at, ca_name, channel, direction, is_automated,
           subject, account_name, contact_email, counts, excluded_reason,
           dup_count, logged_by, source, activity_id
    from activity_flat
    where activity_date between %s and %s
      and (%s = '(all)' or ca_name = %s)
      and (%s = '(all)' or channel = %s)
      and (%s = 'all'
           or (%s = 'counted' and counts)
           or (%s = 'excluded' and not counts))
      and (%s = '' or subject ilike %s or account_name ilike %s)
    order by occurred_at desc
    limit 500
"""

AUDIT_COUNT = """
    select count(*) filter (where counts)    as counted,
           count(*) filter (where not counts) as excluded
    from activity_flat
    where activity_date between %s and %s
      and (%s = '(all)' or ca_name = %s)
      and (%s = '(all)' or channel = %s)
"""

AUDIT_DETAIL = """
    select * from activity_flat where activity_id = %s
"""

CHANNELS = "select distinct channel from activity_flat order by 1"
# Only ACTIVE CAs populate the roster dropdowns / lists — departed CAs are
# retained in dim_ca (is_active=false) so their history stays attributed in the
# model, but the live dashboard view is the current team. INACTIVE_REPS drives
# the display-only filter in ui.active_only().
REPS = "select name from dim_ca where is_active order by name"
INACTIVE_REPS = "select name from dim_ca where not is_active order by 1"

# meetings auto-booked by the Revenue Hero scheduler (visible split — still
# counted; excluding them is a definition change pending leadership sign-off)
MEETINGS_RH = """
    select ca_name, count(*) as rh
    from activity_flat
    where counts and channel = 'meeting'
      and 'RevenueHero' = any(logged_by)
      and activity_date between %s and %s
    group by ca_name
"""

# Dillon fix #22 (migration 006): each counted meeting bucketed as
# new_stakeholder (first meeting with that ACCOUNT in a rolling 60 days) /
# follow_up / no_account. Buckets are disjoint and sum to meetings_booked —
# reconciled exactly against rep_scorecard on build day (decisions.md).
MEETING_BREAKDOWN = "select * from rep_meeting_breakdown(%s, %s)"

# --- chart drill-through (click a bar/dot -> peek at the underlying rows) ----
# The measure->filter maps live HERE (pure constants, importable without
# streamlit) so tests/test_drill_reconciles.py can assert, on every push, that
# each mapping counts exactly what the scorecard counts — the two are written
# separately and this is what keeps them from drifting apart silently.

# chart measure/column -> the activity_flat channel set it draws (the full
# list mirrors the model's channel vocabulary guard)
ALL_CHANNELS = ["auto_email", "manual_email", "call", "li_connect", "li_message",
                "li_other", "inbound_email", "meeting", "whatsapp", "sms", "other"]
DRILL_CHANNELS = {
    "total_counted": ALL_CHANNELS,
    "emails": ["auto_email", "manual_email"],
    "auto_email": ["auto_email"], "manual_email": ["manual_email"],
    "dials": ["call"], "calls": ["call"],
    "linkedin": ["li_connect", "li_message", "li_other"],
    "inbound_replies": ["inbound_email"],
    "meetings_booked": ["meeting"],
    "other_outreach": ["whatsapp", "sms", "other"],
}
# meetings-outcome bar -> the outcome selector DRILL_ROWS understands
OUTCOME_PARAM = {"held": "COMPLETED", "canceled": "CANCELED",
                 "scheduled": "scheduled", "unknown": "unknown"}
# Same source of truth as everything else (activity_flat, counted rows only),
# filtered to exactly the slice the clicked mark drew: window, CA, channel set,
# optional account, optional meeting-outcome bucket (the outcome vocabulary
# mirrors rep_scorecard/002: held=COMPLETED, canceled=CANCELED,
# scheduled=SCHEDULED/RESCHEDULED, unknown=anything else). `total` rides along
# via a window count so the card can say "latest 8 of 47" in one round trip.
# Params: start, end, rep, rep, channels[], account, account, o, o, o, o
DRILL_ROWS = """
    select activity_date, ca_name, channel, account_name, subject,
           contact_email, occurred_at, count(*) over () as total
    from activity_flat
    where counts
      and activity_date between %s and %s
      -- active CAs only, like every chart (a departed CA's rows would make
      -- the card total disagree with the clicked mark)
      and ca_name in (select name from dim_ca where is_active)
      and (%s = '(all)' or ca_name = %s)
      and channel = any(%s)
      and (%s = '(all)' or coalesce(account_name, '(no account matched)') = %s)
      and (%s = 'all'
           or (%s = 'unknown' and coalesce(outcome, '?')
               not in ('COMPLETED','CANCELED','SCHEDULED','RESCHEDULED'))
           or (%s = 'scheduled' and coalesce(outcome, '?')
               in ('SCHEDULED','RESCHEDULED'))
           or coalesce(outcome, '?') = %s)
    order by occurred_at desc
    limit 8
"""

# Same peek for the new-stakeholder/follow-up/no-account meeting buckets —
# rides the migration-006 flags view (already reader-approved), nothing new.
# Params: start, end, rep, rep, bucket
DRILL_MEETING_ROWS = """
    select af.activity_date, af.ca_name, af.channel, af.account_name,
           af.subject, af.contact_email, af.occurred_at,
           count(*) over () as total
    from activity_flat af
    join meeting_new_stakeholder_flags f on f.activity_id = af.activity_id
    where af.activity_date between %s and %s
      and af.ca_name in (select name from dim_ca where is_active)
      and (%s = '(all)' or af.ca_name = %s)
      and f.bucket = %s
    order by af.occurred_at desc
    limit 8
"""

# the same split per calendar week / month (migration 007 — wraps the function,
# so definitions can't drift; joins the 004 trend views on week/month + ca).
# Weekly gets the same not-yet-started-week filter as WEEKLY_TREND.
MEETING_BREAKDOWN_WEEKLY = """
    select * from rep_meeting_breakdown_weekly
    where week_start <= (select max(activity_date) from activity_flat where counts)
    order by week_start, ca_name
"""
MEETING_BREAKDOWN_MONTHLY = "select * from rep_meeting_breakdown_monthly order by month_start, ca_name"

# run-status indicators (metadata only; grant in migration 005)
LAST_RUN = """
    select max(finished_at) as finished_at
    from ingestion_runs where status = 'ok'
"""
# the daily GitHub Action is the only thing that syncs the entity mirrors
# (companies/contacts/owners/users); one-off activity backfills don't — so this
# isolates the real scheduled run time from manual backfills.
LAST_DAILY_RUN = """
    select max(finished_at) as finished_at
    from ingestion_runs
    where status = 'ok'
      and object_type in ('companies', 'contacts', 'owners', 'users')
"""
RUN_ACTIVE = """
    select min(started_at) as started_at
    from ingestion_runs
    where finished_at is null
      and started_at > now() - interval '2 hours'
    having count(*) > 0
"""
