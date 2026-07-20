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
    select owner_name, account_name, icp_tier, vertical, owner_touches,
           owner_last_touch, team_touches, team_last_touch, team_reps
    from owned_account_coverage(%s, %s)
    order by owner_name, (icp_tier is null), icp_tier, team_touches, account_name
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
REPS = "select name from dim_ca order by 1"
