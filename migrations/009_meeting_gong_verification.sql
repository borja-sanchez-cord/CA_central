-- 009: Gong-verified meeting outcomes (Ray ask #1 — close the unknown gap).
--
-- PURELY ADDITIVE and READ-ONLY over Phases 0-7: one view + grants. Reads the
-- Gong call-recording mirror (raw_hubspot_gong_calls / _gong_call_contacts,
-- ingested by ingestion/ingest_gong_calls.py) plus two approved surfaces
-- (activity_flat, raw_hubspot_meeting_contacts from fix #22). Writes nothing;
-- activity_flat, rep_scorecard and all counting are NOT modified — the
-- dashboard joins this label on top (display-only join, the 006/008 pattern).
-- Dropping everything here restores the pre-009 state exactly:
--   drop view meeting_gong_verification;
--
-- THE RULE (strict by design, agreed with PM 2026-07-23):
-- a counted CA meeting is "Gong-verified held" only when ALL three hold:
--   1. a Gong call record with call_status = 'COMPLETED' exists — Gong
--      actually processed a recording. QUEUED records prove NOTHING: Gong
--      also logs future/scheduled calls, and a never-happened meeting sits
--      at QUEUED forever (probed live 2026-07-23);
--   2. that recording's time falls inside the booked slot (start - 15 min
--      .. end + 30 min — meetings start late and run over, never a day off);
--   3. the recording and the meeting share at least one CONTACT — same raw
--      HubSpot contact id on both association tables. Time alone is not
--      enough: ~400 company-wide recordings in 3 weeks make slot-only
--      coincidences common (72 of 163 slot matches failed the contact test).
-- The recording is usually owned by the AE who tagged along — ownership is
-- deliberately NOT part of the rule; the CA attribution comes from the
-- meeting row itself, which is already counted and displayed.
--
-- What this is used for: DISPLAY ONLY. A meeting with an unknown outcome
-- gets the label "held (Gong-verified)" next to — never instead of — the
-- rep-entered outcome vocabulary. meetings_booked and every counted number
-- are untouched. Gong rows themselves are never counted as activities.
-- Verified impact on build day: 91 of 173 unknown-outcome meetings verified
-- (unknown rate 81% -> ~38% of past meetings).
--
-- Safe defaults:
--   * only recordings in the LATEST completed sweep play (last_seen_at gate)
--     -> a call record deleted in HubSpot stops verifying on the next sweep;
--   * a meeting with no start_time or no contact links simply never matches
--     (stays unknown — the honest default);
--   * one row per meeting (the earliest matching recording wins; ties are
--     irrelevant — the label is boolean evidence, not a join to browse).

create or replace view meeting_gong_verification as
with live_recordings as (
    -- COMPLETED only + latest sweep: see the rule above
    select id, title, call_time
    from raw_hubspot_gong_calls
    where last_seen_at = (select max(last_seen_at) from raw_hubspot_gong_calls)
      and call_status = 'COMPLETED'
      and call_time is not null
),
counted_meetings as (
    select af.activity_id,
           m.id                                          as meeting_id,
           af.ca_name,
           af.activity_date,
           af.outcome,
           m.start_time,
           coalesce(m.end_time,
                    m.start_time + interval '30 minutes') as end_time
    from activity_flat af
    join raw_hubspot_meetings m on 'hs_meeting:' || m.id = af.activity_id
    where af.counts and af.channel = 'meeting'
      and m.start_time is not null
)
select distinct on (mtg.activity_id)
       mtg.activity_id,
       mtg.ca_name,
       mtg.activity_date,
       mtg.outcome,                     -- rep-entered (usually null) — kept
                                        -- visible so display can show both
       g.id        as gong_call_id,
       g.title     as gong_call_title,
       g.call_time as gong_call_time
from counted_meetings mtg
join live_recordings g
  on g.call_time between mtg.start_time - interval '15 minutes'
                     and mtg.end_time   + interval '30 minutes'
-- the shared-contact test: same raw HubSpot contact id on both sides
join raw_hubspot_gong_call_contacts gc
  on gc.call_id = g.id
join raw_hubspot_meeting_contacts mc
  on mc.meeting_id = mtg.meeting_id
 and mc.contact_id = gc.contact_id
order by mtg.activity_id, g.call_time;

-- ------------------------------------------------------- dashboard exposure
-- Deliberate grants, per the contract in docs/dashboard.md: this view is the
-- ONLY new thing dashboard_reader can see. The two raw Gong tables get RLS
-- with NO reader policy — reachable only through the owner-executed view
-- above, exactly the posture 004/006/008 take.
grant select on meeting_gong_verification to dashboard_reader;

alter table raw_hubspot_gong_calls         enable row level security;
alter table raw_hubspot_gong_call_contacts enable row level security;
