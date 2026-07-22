-- 006: Dillon fix #22 — "new-stakeholder" vs "follow-up" meetings.
--
-- PURELY ADDITIVE and READ-ONLY over Phases 0-7: two views + one function +
-- one preset view + grants. Reads activity (via owner-executed views),
-- raw_hubspot_meeting_contacts (the attendee links ingested by
-- ingestion/ingest_meeting_contacts.py), contact_crosswalk and dim_ca.
-- Writes nothing; rep_scorecard and activity_flat are NOT modified.
-- Dropping everything here restores the pre-006 state exactly:
--   drop view rep_meeting_breakdown_alltime;
--   drop function rep_meeting_breakdown(date, date);
--   drop view meeting_new_stakeholder_flags, meeting_account_map;
--
-- THE RULE (Dillon + Falc, locked 2026-07-21 — see decisions.md):
--   A meeting is NEW-STAKEHOLDER if there was no already-counted meeting for
--   the same ACCOUNT in the previous 60 days. Rolling: each counted meeting
--   resets the account's 60-day clock. Per ACCOUNT, not per person (a
--   colleague met 2 weeks after the VP is assumed downstream of that first
--   conversation), and GLOBAL across CAs (two CAs meeting one account within
--   the window is still one new conversation — the point is that meetings
--   per account must not inflate vs SAOs). A canceled first meeting still
--   holds the slot (outcome plays no part in this rule — deliberately, since
--   only ~20% of meetings get an outcome logged). Meetings we cannot tie to
--   an account always count, in their own visible bucket — nothing silently
--   drops (the audit invariant, in spirit).
--
-- The three buckets are DISJOINT and SUM TO BOOKED by construction (same
-- guardrail as the meetings outcome split in 002):
--   new_stakeholder  account matched, first in its 60-day window
--   follow_up        account matched, inside a counted meeting's window
--   no_account       no attendee resolvable to a known account (counted)
--
-- Scope note: only COUNTED meetings (counts = true, i.e. a CA attended) play,
-- so rep_meeting_breakdown reconciles against rep_scorecard.meetings_booked
-- exactly. History starts 2026-07-06, so early months skew "new" — every
-- account's first meeting in the data opens a window. Label, don't hide.

-- ---------------------------------------------------- meeting -> account map
-- Attendees are read across ALL raw copies of a deduped meeting (source_ids),
-- not just the canonical one — associations can live on either copy.
-- Resolution is two-tier, both tiers reusing the identity layer's OUTPUT
-- (no new matching logic is invented here):
--   Tier 1 — attendee email -> contact_crosswalk -> resolved company. The
--     person-level match; authoritative when it hits.
--   Tier 2 (only for meetings tier 1 leaves empty) — attendee email DOMAIN ->
--     the canonical company carrying that domain in company_crosswalk, and
--     ONLY when the domain has exactly one canonical company (a conflicted /
--     junk domain like google.com has several — stays unmapped rather than
--     guessed). Needed because contact_crosswalk is activity-scoped: a
--     prospect who booked via a link but never emailed (live case: Manish /
--     zelantrix.com, RevenueHero) is absent from the mirror, yet his company
--     is resolvable. Measured 2026-07-21: tier 2 lifts mapping 77% -> ~97%.
--     Guards: free-mail + internal domains never map (lists mirror
--     FREE_EMAIL_DOMAINS in identity/resolve.py and INTERNAL_DOMAINS in
--     ingestion/ingest.py — keep in sync when either changes).
-- Canonical account = the company with the most resolved attendees;
-- deterministic tiebreak on (length, id) = numeric order for digit ids.
create or replace view meeting_account_map as
with attendee as (
    select a.activity_id, mc.contact_email,
           split_part(mc.contact_email, '@', 2) as email_domain
    from activity a
    cross join lateral jsonb_array_elements(a.source_ids) s
    join raw_hubspot_meeting_contacts mc on mc.meeting_id = s.value ->> 'id'
    where a.channel = 'meeting'
      and mc.contact_email is not null
),
tier1 as (
    select att.activity_id,
           cw.resolved_company_id as account_id,
           count(*)               as attendees
    from attendee att
    join contact_crosswalk cw on cw.email = att.contact_email
    group by 1, 2
),
single_canonical_domain as (
    select domain_norm, min(resolved_company_id) as account_id
    from company_crosswalk
    where is_canonical
    group by domain_norm
    having count(*) = 1
),
tier2 as (
    select att.activity_id, d.account_id, count(*) as attendees
    from attendee att
    join single_canonical_domain d on d.domain_norm = att.email_domain
    where att.activity_id not in (select activity_id from tier1)
      and att.email_domain not in (
          -- FREE_EMAIL_DOMAINS (identity/resolve.py)
          'gmail.com','googlemail.com','yahoo.com','yahoo.co.uk','hotmail.com',
          'hotmail.co.uk','outlook.com','live.com','msn.com','aol.com',
          'icloud.com','me.com','mac.com','proton.me','protonmail.com',
          'gmx.com','gmx.de','qq.com','163.com','126.com','yandex.com',
          'mail.com','zoho.com',
          -- INTERNAL_DOMAINS (ingestion/ingest.py)
          'encord.com','encord.ai','tryencord.com')
    group by 1, 2
),
resolved as (
    select * from tier1
    union all
    select * from tier2
)
select distinct on (activity_id)
       activity_id, account_id, attendees
from resolved
order by activity_id, attendees desc, length(account_id), account_id;

-- ------------------------------------------- the 60-day rolling walk (flags)
-- Recursive per-account walk in meeting order: the first meeting counts as
-- new and stamps last_counted; each later meeting is new only when its date
-- is more than 60 days past last_counted (which it then resets). A plain
-- lag() cannot express the reset (a chain of ~40-day gaps would all re-count)
-- — the recursion is the rule, do not "simplify" it back to lag().
-- Ordering ties (same-day rebookings, e.g. one prospect booked twice in a
-- day) break on occurred_at then activity_id: the earlier booking is the new
-- one, the rebooking is a follow-up — churn stays visible, never inflates.
create or replace view meeting_new_stakeholder_flags as
with recursive meetings as (
    select a.activity_id, a.ca_id, a.activity_date, a.occurred_at, a.outcome,
           m.account_id,
           row_number() over (partition by m.account_id
                              order by a.activity_date, a.occurred_at, a.activity_id) as rn
    from activity a
    left join meeting_account_map m on m.activity_id = a.activity_id
    where a.channel = 'meeting' and a.counts
),
walk as (
    -- anchor: each account's first meeting is new-stakeholder
    select activity_id, ca_id, activity_date, occurred_at, outcome, account_id, rn,
           true as is_new, activity_date as last_counted
    from meetings
    where account_id is not null and rn = 1
    union all
    -- step: new only when >60 days past the last COUNTED meeting (then reset)
    select m.activity_id, m.ca_id, m.activity_date, m.occurred_at, m.outcome,
           m.account_id, m.rn,
           m.activity_date > w.last_counted + 60,
           case when m.activity_date > w.last_counted + 60
                then m.activity_date else w.last_counted end
    from walk w
    join meetings m on m.account_id = w.account_id and m.rn = w.rn + 1
)
select activity_id, ca_id, activity_date, occurred_at, outcome, account_id,
       case when is_new then 'new_stakeholder' else 'follow_up' end as bucket
from walk
union all
-- unmapped meetings: counted, flagged, never dropped
select activity_id, ca_id, activity_date, occurred_at, outcome, null,
       'no_account'
from meetings
where account_id is null;

-- ------------------------------------------------------------ per-rep rollup
-- Same conventions as rep_scorecard (002): every roster CA appears (zeros
-- coalesced), buckets sum to booked by construction, meetings credit the
-- primary CA (activity.ca_id).
create or replace function rep_meeting_breakdown(p_start date, p_end date)
returns table (
    ca_id                text,
    ca_name              text,
    meetings_booked      bigint,
    meetings_new_stakeholder bigint,
    meetings_follow_up   bigint,
    meetings_no_account  bigint
)
language sql stable as $$
with win as (
    select * from meeting_new_stakeholder_flags
    where activity_date between p_start and p_end
),
per_rep as (
    select ca_id,
           count(*)                                          as booked,
           count(*) filter (where bucket = 'new_stakeholder') as new_stakeholder,
           count(*) filter (where bucket = 'follow_up')       as follow_up,
           count(*) filter (where bucket = 'no_account')      as no_account
    from win
    group by ca_id
)
select ca.ca_id,
       ca.name,
       coalesce(p.booked, 0),
       coalesce(p.new_stakeholder, 0),
       coalesce(p.follow_up, 0),
       coalesce(p.no_account, 0)
from dim_ca ca
left join per_rep p on p.ca_id = ca.ca_id
order by coalesce(p.booked, 0) desc, ca.name
$$;

create or replace view rep_meeting_breakdown_alltime as
    select * from rep_meeting_breakdown(date '2000-01-01', current_date);

-- ------------------------------------------------------- dashboard exposure
-- Deliberate grants, per the contract in docs/dashboard.md: these objects are
-- the ONLY new things dashboard_reader can see. The raw attendee table gets
-- RLS with NO reader policy — reachable only through the owner-executed views
-- above, exactly the posture 004 takes with the activity fact table.
grant select on meeting_account_map,
                meeting_new_stakeholder_flags,
                rep_meeting_breakdown_alltime
to dashboard_reader;

grant execute on function rep_meeting_breakdown(date, date) to dashboard_reader;

alter table raw_hubspot_meeting_contacts enable row level security;
