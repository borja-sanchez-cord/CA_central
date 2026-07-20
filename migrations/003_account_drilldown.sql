-- Phase 5: per-account & per-contact drill-down + owned-account neglect view.
--
-- PURELY ADDITIVE and READ-ONLY over Phases 0-4: this file only CREATEs new
-- objects (three functions + three convenience views). It reads activity_flat,
-- dim_ca, dim_account and dim_contact and writes nothing. Dropping everything
-- in this file leaves Phases 0-4 exactly as they were:
--   drop view rep_account_drilldown_alltime, account_contact_drilldown_alltime,
--             owned_account_coverage_alltime;
--   drop function rep_account_drilldown(date,date),
--                 account_contact_drilldown(date,date),
--                 owned_account_coverage(date,date);
--
-- Design decisions (PM, 2026-07-20 — see decisions.md):
--  * MEETINGS ARE EXCLUDED from all three (v1): meetings carry no company, so
--    they cannot be filed under an account. Reconciliation is therefore:
--    sum of a rep's drill-down touchpoints (incl. the no-account bucket)
--    = rep_scorecard.total_counted - meetings_booked.
--  * The NO-ACCOUNT bucket is explicit: activities the source logged without
--    a company (~60%, mostly LinkedIn/calls) appear as one labelled row per
--    rep — '(no account matched)' — so totals reconcile by construction and
--    the gap is visible, never hidden.
--  * The neglect view covers CA-OWNED accounts only (owner_rep_id on the CA
--    roster; ~1,600) and includes ZEROS — an owned account nobody touched is
--    a row, because "top-tier account neglected" is the question it answers.
--    It shows BOTH owner touches and whole-team touches ("owner=0, team=12"
--    reads very differently from "0 and 0").

-- ------------------------------------------------- rep -> account drill-down
create or replace function rep_account_drilldown(p_start date, p_end date)
returns table (
    ca_id            text,
    ca_name          text,
    account_id       text,
    account_name     text,
    account_domain   text,
    icp_tier         text,
    owned_by_rep_id  text,
    owned_by_this_rep boolean,
    touchpoints      bigint,
    people_touched   bigint,
    auto_email       bigint,
    manual_email     bigint,
    calls            bigint,
    linkedin         bigint,
    inbound_replies  bigint,
    other_outreach   bigint,
    first_touch      date,
    last_touch       date
)
language sql stable as $$
select
    a.ca_id,
    a.ca_name,
    a.company_id                                        as account_id,
    coalesce(a.account_name, '(no account matched)')    as account_name,
    a.account_domain,
    a.account_icp_tier_validated                        as icp_tier,
    d.owner_rep_id                                      as owned_by_rep_id,
    (d.owner_rep_id = a.ca_id)                          as owned_by_this_rep,
    count(*)                                            as touchpoints,
    count(distinct a.contact_id)                        as people_touched,
    count(*) filter (where a.channel = 'auto_email')    as auto_email,
    count(*) filter (where a.channel = 'manual_email')  as manual_email,
    count(*) filter (where a.channel = 'call')          as calls,
    count(*) filter (where a.channel like 'li\_%')      as linkedin,
    count(*) filter (where a.channel = 'inbound_email') as inbound_replies,
    count(*) filter (where a.channel in ('whatsapp','sms','other')) as other_outreach,
    min(a.activity_date)                                as first_touch,
    max(a.activity_date)                                as last_touch
from activity_flat a
left join dim_account d on d.account_id = a.company_id
where a.counts
  and a.channel <> 'meeting'          -- meetings can't be tied to accounts yet
  and a.ca_id is not null
  and a.activity_date between p_start and p_end
group by a.ca_id, a.ca_name, a.company_id, a.account_name, a.account_domain,
         a.account_icp_tier_validated, d.owner_rep_id
order by a.ca_name, touchpoints desc
$$;

-- --------------------------------------------- account -> contact drill-down
create or replace function account_contact_drilldown(p_start date, p_end date)
returns table (
    ca_id            text,
    ca_name          text,
    account_id       text,
    account_name     text,
    icp_tier         text,
    contact_id       text,
    contact_name     text,
    contact_email    text,
    jobtitle         text,
    touchpoints      bigint,
    emails           bigint,
    calls            bigint,
    linkedin         bigint,
    inbound_replies  bigint,
    last_touch       date
)
language sql stable as $$
select
    a.ca_id,
    a.ca_name,
    a.company_id                                        as account_id,
    coalesce(a.account_name, '(no account matched)')    as account_name,
    a.account_icp_tier_validated                        as icp_tier,
    a.contact_id,
    nullif(trim(coalesce(a.contact_firstname,'') || ' ' ||
                coalesce(a.contact_lastname,'')), '')   as contact_name,
    a.contact_email,
    a.contact_jobtitle                                  as jobtitle,
    count(*)                                            as touchpoints,
    count(*) filter (where a.channel in ('auto_email','manual_email')) as emails,
    count(*) filter (where a.channel = 'call')          as calls,
    count(*) filter (where a.channel like 'li\_%')      as linkedin,
    count(*) filter (where a.channel = 'inbound_email') as inbound_replies,
    max(a.activity_date)                                as last_touch
from activity_flat a
where a.counts
  and a.channel <> 'meeting'
  and a.ca_id is not null
  and a.contact_id is not null        -- contact level: only rows with a person
  and a.activity_date between p_start and p_end
group by a.ca_id, a.ca_name, a.company_id, a.account_name,
         a.account_icp_tier_validated, a.contact_id,
         a.contact_firstname, a.contact_lastname, a.contact_email,
         a.contact_jobtitle
order by a.ca_name, account_name, touchpoints desc
$$;

-- --------------------------------------- owned accounts incl. ZEROS (neglect)
create or replace function owned_account_coverage(p_start date, p_end date)
returns table (
    owner_ca_id      text,
    owner_name       text,
    account_id       text,
    account_name     text,
    account_domain   text,
    icp_tier         text,
    vertical         text,
    owner_touches    bigint,
    owner_last_touch date,
    team_touches     bigint,
    team_last_touch  date,
    team_reps        bigint
)
language sql stable as $$
with win as (
    select company_id, ca_id, activity_date
    from activity_flat
    where counts
      and channel <> 'meeting'
      and ca_id is not null
      and company_id is not null
      and activity_date between p_start and p_end
)
select
    ca.ca_id                                            as owner_ca_id,
    ca.name                                             as owner_name,
    d.account_id,
    d.name                                              as account_name,
    d.domain                                            as account_domain,
    d.icp_tier_validated                                as icp_tier,
    d.vertical,
    count(w.company_id) filter (where w.ca_id = ca.ca_id)      as owner_touches,
    max(w.activity_date) filter (where w.ca_id = ca.ca_id)     as owner_last_touch,
    count(w.company_id)                                        as team_touches,
    max(w.activity_date)                                       as team_last_touch,
    count(distinct w.ca_id)                                    as team_reps
from dim_account d
join dim_ca ca on ca.ca_id = d.owner_rep_id     -- CA-owned accounts only
left join win w on w.company_id = d.account_id
group by ca.ca_id, ca.name, d.account_id, d.name, d.domain,
         d.icp_tier_validated, d.vertical
order by ca.name, owner_touches, d.icp_tier_validated nulls last
$$;

-- Convenience views over all data ever held (functions take any custom range).
create or replace view rep_account_drilldown_alltime as
    select * from rep_account_drilldown(date '2000-01-01', current_date);

create or replace view account_contact_drilldown_alltime as
    select * from account_contact_drilldown(date '2000-01-01', current_date);

create or replace view owned_account_coverage_alltime as
    select * from owned_account_coverage(date '2000-01-01', current_date);
