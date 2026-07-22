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
