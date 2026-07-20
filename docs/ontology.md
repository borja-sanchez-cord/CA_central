# Ontology — what every number means

One page for anyone reading a CA activity report or (later) the dashboard. Every
definition below is how the pipeline actually computes the value (file
references in brackets for engineers); nothing here is aspirational. One row in
the data = one real event; the same event logged by several tools is collapsed
into one. Only rows marked **counted** appear in reports — everything else is
kept but labeled with why it was excluded (see appendix).

## Activity types (the "channel" filter)

| Term | Definition | Watch out |
|---|---|---|
| **Automated email** | An email a sequence tool (AmpleMarket/Apollo) sent on the rep's behalf, to at least one person outside Encord. | Personalised-looking; templates fill in name/company. Volume ≠ effort — sequences keep sending during PTO. |
| **Manual email** | An email a human actually wrote and sent (from Gmail or typed in HubSpot), to at least one person outside Encord. | The automated/manual split for emails is a best-effort inference from *which tool logged it* — good, not perfect (**† see note below**). |
| **Inbound reply** | An email a prospect sent to a CA. Bounces, out-of-office auto-replies and platform notifications are removed first (they'd roughly double the number). | Counts replies *received in the window*, not replies *to* that window's sends. |
| **Call (dial)** | One phone dial from AmpleMarket's dialer. Every dial is kept, answered or not. | Calls made outside the AmpleMarket dialer are invisible to us. |
| **LinkedIn** | A LinkedIn step (connect / message / visit / like) completed inside an AmpleMarket sequence. Split as: **connect** = connection request, **message** = direct/voice/video message, **other** = profile visit, follow, post like. | **AmpleMarket-only.** LinkedIn done directly in the LinkedIn app/website is not captured, so this undercounts reps who work LinkedIn natively. |
| **Meeting** | A meeting object in HubSpot with at least one CA attending. Duplicate objects for the same meeting are collapsed; the calendar-invite emails around it are folded in here, not counted as emails. | **Booked, not held.** Outcome is logged on only ~20% of meetings, so "meeting" ≈ "invite went out". Internal meetings are ~3% pollution today (CAs don't log them in HubSpot) — by habit, not by a rule. |
| **Other (custom tasks)** | An AmpleMarket sequence step of a type we don't recognise (e.g. "custom task" — could be WhatsApp, research, anything). Counted and shown honestly rather than dropped. | The tool only records that the step was ticked done, not what it was. |

WhatsApp and SMS steps have defined slots but none have appeared in the data yet.

**† A note on the automated vs manual limitation.** We decide it from *which tool logged the email*, not by reading it. Reliable at the ends — tool-only → automated; sent from Gmail or typed in HubSpot → manual. The gap is the middle: **a rep who writes a personal email but sends it *through* AmpleMarket is labelled automated**, because an AmpleMarket send looks identical whether templated or hand-written. So the label means *"was a sequencing tool involved"* (how the reps themselves think of it), not *"did they personally write it."* Uncommon, small bias — and since we store the full email **body**, any rep's emails can be read to check (Phase 6; available now for email, not yet for LinkedIn).

## Call terms

| Term | Definition |
|---|---|
| **Dial** | One phone-dial attempt, from AmpleMarket's call log. |
| **Pursuit** | One *person* a rep chased by phone: repeated dials at the same person within 30 minutes bundle into a single pursuit (mobile, then office line, then redial = 1 pursuit). |
| **Conversation** | A dial where a real human answered — AmpleMarket's own "human answered" flag, not our inference. Voicemail and phone menus are attempts, not conversations. |

Always: dials ≥ pursuits ≥ conversations. (~8–9 dials per conversation is normal.)

## Who / where / when (the other filters)

| Term | Definition | Watch out |
|---|---|---|
| **CA / rep** | One of the 17 Customer Associates on the resolved roster. Emails are attributed by **sender address** (all known addresses per rep, incl. encord.ai), never by HubSpot's "owner" field (proven unreliable). | |
| **Direction** | **Outbound** = rep effort. **Inbound** = prospect engagement (replies). | |
| **Automated vs manual** | Whether a machine or a human performed the action. For AmpleMarket steps it's the tool's own flag (reliable). For emails it's inferred from the logging source (weaker). | Meetings and inbound replies are "not classifiable" — neither bucket. |
| **Account** | The HubSpot company an activity is tied to, after de-duplicating companies sharing a domain. | ~60% of counted activities have **no account** — mostly LinkedIn/calls where the source logged no contact. Meetings can't be tied to accounts at all yet (attendee emails not exposed). |
| **ICP tier** | The account's fit tier from HubSpot (`Tier 0` best → `Tier 4`, `DQ`). We show the *validated* tier field. | Most activity lands on untiered accounts — that reflects HubSpot tiering coverage, not a data fault. |
| **Target account** | The account has a target-account owner assigned in HubSpot. | |
| **Date** | The UTC day the activity happened (not when it synced). | UK afternoon and US morning share a UTC day, but US evening calls can land on the "next" UTC day. |

## Scorecard measures (the per-rep rollup — Phase 4)

These are computed *per rep, per time window* by the `rep_scorecard()` view in
Supabase; the dashboard reads them directly. Everything above is a raw count of
activities; these are the derived numbers built on top.

| Measure | Definition | Watch out |
|---|---|---|
| **Emails** | Automated + manual emails added together. | |
| **Meetings booked** | Every meeting in the window (see Meeting above). | The headline meeting number — always shown split into the four below, never alone. |
| **Meetings held** | Meetings the rep marked `COMPLETED`. | Only ~20% of meetings get any outcome logged, so this *undercounts* real held meetings — it's a floor, not the truth. |
| **Meetings canceled** | Meetings marked `CANCELED`. | |
| **Meetings scheduled** | Upcoming, marked `SCHEDULED`/`RESCHEDULED`. | |
| **Meetings unknown** | Booked minus held minus canceled minus scheduled — i.e. no outcome was logged. | This is usually the biggest bucket. "Unknown" means *not logged*, **never** assume held. Any new outcome value HubSpot invents also lands here, not in held. |
| **Total counted** | Every counted activity for the rep, each counted once. | |
| **Accounts touched** | Distinct companies the rep did at least one counted activity against, in the window. | Skips the ~60% of activities with no matched company (LinkedIn/calls/meetings) — understates true breadth. Trend over level. |
| **Contacts touched** | Distinct people the rep did at least one counted activity against. | Same ~60% caveat. |
| **Contacts per account** | Contacts touched ÷ accounts touched. | A depth signal (are they multi-threading?), blunted by the same missing-company gap. |
| **Accounts owned** | Companies where this rep is the HubSpot target-account owner. | One owner per account; ~1,600 accounts are owned in total. Untiered/unassigned accounts a rep works but doesn't formally own are not in this denominator. |
| **Owned touched** | Of the rep's *owned* accounts, how many they personally touched in the window. | A colleague touching your account does **not** count here. |
| **Coverage %** | Owned touched ÷ accounts owned. "Of the accounts I'm responsible for, what share did I work this period." | Likely a slight *under*-count (missing-company gap can only hide touches, never invent them). Compare reps to each other, watch the trend. |

## Drill-down measures (rep → account → person — Phase 5)

Computed per time window by the `rep_account_drilldown()` / `account_contact_drilldown()` / `owned_account_coverage()` views. **Meetings are not in these** (they can't be tied to an account yet — see Meeting above); they stay in the rep scorecard.

| Measure | Definition | Watch out |
|---|---|---|
| **Touchpoints** | Counted activities (emails, calls, LinkedIn, inbound) filed under that account or person. | Excludes meetings; the ~60% of activity with no matched company sits in an explicit **"(no account matched)"** row per rep — visible, so rep totals still reconcile. |
| **People touched** | Distinct people the rep reached at that account. | |
| **Owner touches vs team touches** | On the neglect view: activity on an owned account by its owner, vs by any CA. "Owner 0, team 12" ≠ "0 and 0". | |
| **Neglect view** | Every CA-owned account **including zeros** — an owned account nobody touched is still a row. | Touch counts are a floor (missing-company gap): zero means "no *recorded* touch". |

## Appendix — audit fields (for trust, not for reading)

Every row also carries its full audit trail: the raw tool records behind it
(`source_ids`), how many duplicate copies were collapsed (`dup_count`), which
tools logged a copy (`logged_by`), and — for non-counted rows — the exact
exclusion reason (`excluded_reason`: non-CA sender, sequence to-do shadow,
bounce/auto-reply noise, calendar invite, internal-only recipients, …). Nothing
is deleted; every raw record lands in exactly one row, and the build aborts if
that ever breaks.

*Sources: `model/rules.py` + `model/build_activity.py` (activity rules),
`migrations/001_activity_model.sql` (activity fields), `migrations/002_rep_scorecard.sql`
(scorecard measures), `docs/decisions.md` (evidence for each caveat). Activity
counts come from `activity_flat` and scorecard measures from `rep_scorecard()` —
the same views every report and the dashboard read, so these definitions cannot
drift between reports.*
