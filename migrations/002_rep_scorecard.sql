-- Phase 4: the aggregate rep view ("scorecard").
--
-- PURELY ADDITIVE and READ-ONLY over Phase 3: this file only CREATEs new
-- objects (one function + three preset views). It reads activity_flat,
-- dim_ca and dim_account and writes nothing. Dropping everything in this
-- file leaves Phases 0-3 exactly as they were:
--   drop view rep_scorecard_7d, rep_scorecard_30d, rep_scorecard_alltime;
--   drop function rep_scorecard(date, date);
--
-- Design notes (decisions.md 2026-07-19):
--  * Every CA on the roster appears, even with zero activity in the window.
--  * Meetings follow the Phase 4 guardrail: never one number. booked =
--    held + canceled + scheduled + unknown, where unknown = no outcome
--    logged OR an outcome value we don't recognise (never folded into held).
--  * Meetings credit the PRIMARY CA only (activity.ca_id) - same attribution
--    every validated audit workbook used. Multi-CA meetings are 4 of 162.
--  * Pursuits = distinct call_group_id within the window (a dial burst that
--    crossed midnight counts once here; per-day sums could count it twice -
--    always derive pursuit counts from this function, not from day slices).
--  * Coverage = of the accounts THIS rep owns (dim_account.owner_rep_id),
--    how many did THIS rep touch in the window. Someone else touching your
--    account does not count as your coverage.
--  * accounts_touched/contacts_touched skip activities with no matched
--    company/person (~60% of rows - mostly LinkedIn/calls; meetings never
--    carry a company yet). They understate true breadth; trend > level.

create or replace function rep_scorecard(p_start date, p_end date)
returns table (
    ca_id               text,
    ca_name             text,
    auto_email          bigint,
    manual_email        bigint,
    emails              bigint,
    dials               bigint,
    pursuits            bigint,
    conversations       bigint,
    linkedin            bigint,
    li_connect          bigint,
    li_message          bigint,
    li_other            bigint,
    other_outreach      bigint,
    inbound_replies     bigint,
    meetings_booked     bigint,
    meetings_held       bigint,
    meetings_canceled   bigint,
    meetings_scheduled  bigint,
    meetings_unknown    bigint,
    total_counted       bigint,
    accounts_touched    bigint,
    contacts_touched    bigint,
    contacts_per_account numeric,
    accounts_owned      bigint,
    owned_touched       bigint,
    coverage_pct        numeric
)
language sql stable as $$
with counted as (
    select *
    from activity_flat
    where counts
      and activity_date between p_start and p_end
      and ca_id is not null
),
per_rep as (
    select
        c.ca_id,
        count(*) filter (where channel = 'auto_email')                    as auto_email,
        count(*) filter (where channel = 'manual_email')                  as manual_email,
        count(*) filter (where channel in ('auto_email','manual_email'))  as emails,
        count(*) filter (where channel = 'call')                          as dials,
        count(distinct call_group_id) filter (where channel = 'call')     as pursuits,
        count(*) filter (where channel = 'call' and is_conversation)      as conversations,
        count(*) filter (where channel like 'li\_%')                      as linkedin,
        count(*) filter (where channel = 'li_connect')                    as li_connect,
        count(*) filter (where channel = 'li_message')                    as li_message,
        count(*) filter (where channel = 'li_other')                      as li_other,
        count(*) filter (where channel in ('whatsapp','sms','other'))     as other_outreach,
        count(*) filter (where channel = 'inbound_email')                 as inbound_replies,
        count(*) filter (where channel = 'meeting')                       as meetings_booked,
        count(*) filter (where channel = 'meeting'
                           and outcome = 'COMPLETED')                     as meetings_held,
        count(*) filter (where channel = 'meeting'
                           and outcome = 'CANCELED')                      as meetings_canceled,
        count(*) filter (where channel = 'meeting'
                           and outcome in ('SCHEDULED','RESCHEDULED'))    as meetings_scheduled,
        count(*)                                                          as total_counted,
        count(distinct company_id)                                        as accounts_touched,
        count(distinct contact_id)                                        as contacts_touched
    from counted c
    group by c.ca_id
),
owned as (
    select owner_rep_id as ca_id, count(*) as accounts_owned
    from dim_account
    where owner_rep_id is not null
    group by owner_rep_id
),
owned_touched as (
    select c.ca_id, count(distinct c.company_id) as owned_touched
    from counted c
    join dim_account d
      on d.account_id = c.company_id
     and d.owner_rep_id = c.ca_id
    group by c.ca_id
)
select
    ca.ca_id,
    ca.name                                            as ca_name,
    coalesce(p.auto_email, 0)                          as auto_email,
    coalesce(p.manual_email, 0)                        as manual_email,
    coalesce(p.emails, 0)                              as emails,
    coalesce(p.dials, 0)                               as dials,
    coalesce(p.pursuits, 0)                            as pursuits,
    coalesce(p.conversations, 0)                       as conversations,
    coalesce(p.linkedin, 0)                            as linkedin,
    coalesce(p.li_connect, 0)                          as li_connect,
    coalesce(p.li_message, 0)                          as li_message,
    coalesce(p.li_other, 0)                            as li_other,
    coalesce(p.other_outreach, 0)                      as other_outreach,
    coalesce(p.inbound_replies, 0)                     as inbound_replies,
    coalesce(p.meetings_booked, 0)                     as meetings_booked,
    coalesce(p.meetings_held, 0)                       as meetings_held,
    coalesce(p.meetings_canceled, 0)                   as meetings_canceled,
    coalesce(p.meetings_scheduled, 0)                  as meetings_scheduled,
    coalesce(p.meetings_booked, 0)
      - coalesce(p.meetings_held, 0)
      - coalesce(p.meetings_canceled, 0)
      - coalesce(p.meetings_scheduled, 0)              as meetings_unknown,
    coalesce(p.total_counted, 0)                       as total_counted,
    coalesce(p.accounts_touched, 0)                    as accounts_touched,
    coalesce(p.contacts_touched, 0)                    as contacts_touched,
    round(p.contacts_touched::numeric
          / nullif(p.accounts_touched, 0), 1)          as contacts_per_account,
    coalesce(o.accounts_owned, 0)                      as accounts_owned,
    coalesce(t.owned_touched, 0)                       as owned_touched,
    round(100.0 * coalesce(t.owned_touched, 0)
          / nullif(o.accounts_owned, 0), 1)            as coverage_pct
from dim_ca ca
left join per_rep       p on p.ca_id = ca.ca_id
left join owned         o on o.ca_id = ca.ca_id
left join owned_touched t on t.ca_id = ca.ca_id
order by coalesce(p.total_counted, 0) desc, ca.name
$$;

-- Rolling presets (current_date is evaluated when queried, so these are
-- always-fresh windows; the dashboard can also call rep_scorecard directly
-- with any custom range).
create or replace view rep_scorecard_7d as
    select * from rep_scorecard(current_date - 7, current_date - 1);

create or replace view rep_scorecard_30d as
    select * from rep_scorecard(current_date - 30, current_date - 1);

-- All data ever ingested (the tool went live 2026-07-11; backfilled to 07-06).
create or replace view rep_scorecard_alltime as
    select * from rep_scorecard(date '2000-01-01', current_date);
