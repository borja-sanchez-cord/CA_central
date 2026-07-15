# Decisions Log

Every settled decision and the reason behind it, so no one re-litigates them. Add new entries as decisions are made; don't silently reverse an entry — add a new one that supersedes it.

---

**Store: Supabase (Postgres).**
Chosen over Neon. Both are Postgres; Supabase adds a built-in data API and access controls that make exposing data to a non-technical dashboard easier, saving plumbing we'd otherwise build ourselves.

**Runtime: daily batch pull via REST, on a cloud cron.**
Not a webhook, not the AmpleMarket MCP. A daily batch satisfies the "keep it up to date daily" requirement. Cloud cron so nothing depends on anyone's machine being on. MCP was rejected because research showed it just wraps the same REST data — no extra capability, worse fit for a scheduled job.

**Message body: field exists in schema now, stays null until v2.**
Reading actual message text (to judge tailored vs. generic outreach) is explicitly a v2 goal. Capturing it needs an always-on webhook listener, which we won't run before any analysis needs it. Putting the `body` field in the schema now means v2 is "switch on the listener," not "re-architect." Caveat: the webhook can only capture messages from when it's switched on — no backfill.

**Source of truth split: AmpleMarket for its channels; HubSpot for manual emails + meetings only.**
AmpleMarket activity syncs into HubSpot, so counting both would double-count. AmpleMarket owns LinkedIn, calls, and sequenced emails; HubSpot contributes only the non-AmpleMarket activity (manual Gmail emails, meetings). HubSpot ingestion excludes `lemwarmup` and `amplemarket`-sourced records.

**Deals excluded — model activities against company + contact only.**
Activity attaches to contacts and companies. Deals are a separate pipeline/revenue object and answer a different question (did activity create pipeline?). Out of scope for v1; possible future attribution layer.

**Coverage source: HubSpot `target account owner`.**
Confirmed by Ray as the field telling us which rep owns each account. One owner per account.

**Account de-duplication by domain is mandatory, upstream of coverage.**
Ray confirmed HubSpot sometimes stores duplicate company records. Left unhandled, one account's activity splits across copies and coverage/depth numbers distort. Collapse duplicates by normalized domain before computing any coverage metric.

**Identity resolution: deterministic-first, fuzzy fallback, cached.**
Person = exact lowercased email; company = normalized domain; fuzzy name matching only for records that fail exact keys; never collapse companies on free-email domains. Cached in a crosswalk so daily runs are incremental joins, not re-resolution.

**Seniority from job title: rules-first, LLM fallback, cached.**
Dictionary/regex handles common titles; only unmatched/ambiguous titles hit an LLM; every (raw title → bucket) mapping is cached so a title is classified once ever. Raw title preserved, never overwritten.

**Automated-vs-manual flag: AmpleMarket authoritative, HubSpot proxy.**
AmpleMarket exposes an explicit `automatic` flag (high confidence). HubSpot has no clean sequence flag, so its activities get a lower-confidence inferred label.

**Supabase security settings.**
Data API on (needed for the dashboard); auto-expose new tables OFF (data is sensitive; expose deliberately); automatic RLS ON (no table accidentally wide-open). Sensitive internal rep + prospect data justifies the cautious defaults.

**DB schema managed via Git migrations from Phase 3 onward.**
Connect Supabase↔GitHub at Phase 3 (when tables first exist), not before — nothing to sync until then. Keeps every schema change tracked and reversible instead of manual UI edits. Credentials/keys never committed; use secrets/env vars.

**Phase 3 de-duplication & direction rules (confirmed, discovered during Phase 1 rep validation).**
The raw layer deliberately keeps everything; these are applied when building the unified activity model:
1. **Sent vs received emails** — only outbound (sender = rep) counts as rep *effort*; inbound prospect replies are tracked separately as an engagement/outcome signal. (Yianni 2026-07-13: 3 sent, 2 received.)
2. **Same-email cross-tool duplicates** — the same outbound email can be logged into HubSpot by both Apollo and AmpleMarket; treat as one when **subject + date + time all match** (sender as an extra safeguard). (James Falconer 2026-07-13: "Re: VLMs in identity", identical date/time, double-logged.) Ties into the open Apollo decision.
3. **AmpleMarket task↔send reconciliation** — AmpleMarket exposes email tasks but not sends (sends only via the HubSpot copy); reconcile so a task and its send aren't double-counted, without dropping sends.
4. **Call↔task overlap** — a `phone_call` task and its `/calls` record are the same call; collapse via `task_id`.
Deferred here (not in raw) because it needs identity resolution (Phase 2) and the unified model to do correctly.

**Phase 3 rules — additions & one correction (found 2026-07-14 cross-checking Joe Turner, Dillon, Kamil, George Lim). Supersedes rule 4 above.**
- **Attribute emails by SENDER address, not `hubspot_owner_id`.** owner_id is NOT reliable rep identity: it is sometimes shared by several reps at once (owner_id `538916758` carried sent mail from Kamil, Yianni, George Lim, Nico, Ilaria) and one rep's mail can span many owner_ids (Dillon across 6). Determine who sent an email from `hs_email_from_email`, matched to the rep's linked addresses (the `/users` map). Direction (sent/received) then follows: sender ∈ rep's addresses ⇒ outbound effort; else inbound reply. Some reps happen to have a clean owner_id (Yuvi, Katie, Joe) but you must not depend on it.
- **Calls — count attempts vs conversations separately; `task_id` is usually null so DO NOT collapse on it (this corrects rule 4).** On `/calls`, `task_id` was null for all of Joe's calls, so the "collapse via task_id" method fails in practice. A rep pursuing one contact logs several call records seconds apart, often to *different phone numbers* (Joe 13 Jul: 4 calls in 123s to 4 different numbers for the same contact Iyad Ahmad = 4 genuine dial attempts, 1 real conversation — rep confirmed). Group a "conversation" by **same contact + tight time window**, not `task_id`. A record is a real human conversation only when `human = true`; `answered = true` alone can be voicemail/IVR. Report both **dial attempts** and **connects/conversations** — collapsing to one hides effort; treating every record as a conversation inflates volume. (The original rule 4 — that a `phone_call` task and its `/calls` record are one call — still holds *when* a task_id is present; it just cannot be the primary key for de-dup.)
- **Calendar-invite fan-out — collapse.** One calendar action is written once per attendee (Dillon 13 Jul: 6 identical Apollo "Updated invitation" rows, same timestamp, one per invitee). Collapse to a single activity keyed on subject + time (differs only by recipient), or it 6×-inflates.
- **Exclude internal-only recipients.** Activity addressed solely to internal `@encord.com` people is not outbound CA activity (Dillon's invite attendees were all internal) — don't count it as outreach.

**Rep identity: authoritative CA roster required; link each rep's multiple accounts/addresses. (OPEN — needed for Phase 2.)**
AmpleMarket's `role` field cannot define the CA team — active reps appear as `admin` (e.g. Yuvi, Joe Turner, James Falconer), and some reps have duplicate/multiple accounts. A rep can also use different email domains across systems (Nico: `@encord.ai` in AmpleMarket vs `@encord.com` in HubSpot). Decision: obtain an **authoritative CA roster from Ray / sales leadership** (cross-referenced with HubSpot `target account owner`), and in Phase 2 **link each rep's accounts + email addresses into one rep identity**. Without this, a rep's own emails are misclassified as inbound and their activity is split across duplicates. Discovered during Phase 1 rep validation.

**Rep identity — the technical ID→person map comes from AmpleMarket `/users`; only "who is a CA" needs Ray. (Refines the entry above; found 2026-07-14.)**
Two separate questions were being conflated into one "roster" blocker: (1) *which AmpleMarket internal ID belongs to which person, and which email addresses are theirs* — a technical mapping; and (2) *which of those people count as CAs* — a team-membership call. Finding: `/users` answers (1) directly and completely — it returns each rep's `id`, `email`, and a `mailboxes` array that links their multiple addresses (e.g. Yuvi's `@encord.com` + `@encord.ai`), and it lists reps **regardless of whether they logged any tasks.** So the technical map is buildable today; only (2) needs Ray / sales leadership. **Why this is critical:** AmpleMarket `/calls` records identify their rep by internal ID only — no name or email — so calls cannot be attributed to a person by any means *except* this map. The earlier stopgap (inferring a rep's ID from their completed tasks) silently dropped every call for any rep with no tasks that day. Evidence: Yuvi had 0 tasks on 13 Jul, so his 2 answered calls (Mediaire, Siemens Healthineers) were invisible until mapped via `/users`, then matched exactly what he reported. Note also: a rep can have >1 `/users` account (Yuvi has a dormant `@encord.ai` account plus his active `@encord.com` one) — link them, but attribute activity to the account that actually carries it.

**Apollo is a third activity source in HubSpot. (OPEN — decide before finalizing counts.)**
Beyond AmpleMarket and manual Gmail, HubSpot contains substantial **Apollo**-sourced emails (a second outreach tool, tagged `hs_object_source_detail_1 = Apollo Integration`). The original plan named only AmpleMarket. Kept raw for now (tagged by origin). Decide whether Apollo activity counts as CA activity; note the same email can be logged by *both* Apollo and AmpleMarket (collapse via subject+date+time — see Phase 3 rules).

**Phase 1 raw ingestion: land faithful per-source copies, keyed by source id, idempotent.**
Four raw landing tables (`raw_amplemarket_tasks`, `raw_amplemarket_calls`, `raw_hubspot_emails`, `raw_hubspot_meetings`) plus an `ingestion_runs` audit log. Each row stores a few extracted columns for convenience plus the full source payload in a `raw` jsonb column, so nothing is lost before Phase 3 normalization. Primary key = source id; `INSERT ... ON CONFLICT DO NOTHING`, so re-running a day inserts zero duplicates. AmpleMarket ignores date filters, so we page newest-first and stop once we cross below the target day (tasks keyed on `finished_on` with `status=completed`; calls on `start_date`).

**HubSpot email exclusions at ingestion — warmup noise only. (SUPERSEDES the original "exclude AmpleMarket-sourced" rule below.)**
Keep every HubSpot email regardless of origin (each tagged via `object_source` / `object_source_detail_1` = Amplemarket / Apollo Integration / manual Gmail). Skip only warmup noise (subject contains `lemwarmup`/`lemwarm`/`amplemarketwarmup`/`warmupemail`). Exclusion counts recorded per run in `ingestion_runs`.
**Why the reversal:** testing showed AmpleMarket's REST API exposes email *tasks* but NOT the emails actually *sent* (automated/sequenced sends never appear as tasks). The only record of a sent AmpleMarket email is the copy that syncs into HubSpot. The original rule deleted those copies as "duplicates," but they are the sole record — deleting them undercounted real rep emails. Evidence: Yuvi Ajoomal over 30 days had 31 AmpleMarket email *tasks* (0 automated) but 143 emails actually *sent by him*, visible only in HubSpot, that were being dropped. A raw layer must not discard data it cannot recreate; precise task↔send de-duplication is deferred to Phase 3.
**Superseded rule (kept for history):** originally we skipped emails whose `hs_object_source_detail_1` = `Amplemarket` on the assumption AmpleMarket already counted them.
**Still open (counting phase):** whether Apollo-sourced emails count as CA activity.

**Supabase access for ingestion: direct Postgres connection (transaction pooler).**
The ingestion job connects with the `SUPABASE_DB_URL` transaction-pooler connection string (needed for creating tables + bulk writes, which the REST API can't do). The earlier `SUPABASE_KEY` (service key) remains for the dashboard/REST side later. Both stored as secrets, never committed.

**Cloud cron platform: GitHub Actions.**
The daily scheduled job runs as a GitHub Actions workflow (`.github/workflows/daily-run.yml`). Chosen over Supabase scheduled functions / external schedulers because the repo already lives on GitHub, it's free at this usage, requires no new account or infra, and its encrypted Secrets store holds the API keys for later phases (never committed). Runs in the cloud so nothing depends on a local machine. Phase 0 version logs a run only; real ingestion is added in Phase 1.

**Build model: Claude Code builds each phase; owner acts as non-technical PM.**
Technical figuring-out (schemas, API calls, pagination, error handling) is Claude's per phase — not pre-written, to avoid over-planning. The roadmap therefore carries a working agreement and a plain per-phase check the PM can personally verify.

**Phase 1 ingestion script — 3 follow-up fixes found in code review (2026-07-14, queued as TODO in roadmap Phase 1, not yet applied).**
1. **Lookback window too short — late-arriving records are silently lost forever.** The script only ever pulls "yesterday," but some HubSpot/AmpleMarket records land hours after that snapshot is taken. Evidence: querying HubSpot live on 14 Jul for 13 Jul returned 1,379 emails vs the 1,377 we captured; the `ingestion_runs` log shows 13 Jul AmpleMarket tasks grew from 185 → 304 fetched across successive re-runs of the *same* day. Fix: widen each daily run to re-check the last ~3–4 days, not just yesterday — safe because re-runs are proven idempotent (0 duplicate inserts observed across every repeat run in the log).
2. **We never capture who an email was sent to, or meeting attendees/outcome.** Inspecting the actual stored `raw` jsonb confirms it only contains the specific properties requested (`hs_email_from_email`, subject, timestamp, source, owner) — not a recipient field for emails, nor an attendee/outcome field for meetings. Concretely: a "booked meeting" today only means one was scheduled — we don't know if it was actually held, cancelled, or a no-show. This blocks Phase 3 rules already committed in this doc and the roadmap: internal-recipient exclusion and invite-fan-out collapsing both need the recipient list; attaching an outbound email to the prospect it went to needs it too; and meeting→rep attribution has no path at all without attendees, since `owner_id` is separately proven unreliable (see rep-identity entries above). Fix: add the missing properties to `ingest_hs_emails`/`ingest_hs_meetings`'s requested `props`, then re-run ingestion once for the days already captured (11–13 Jul) to backfill — cheap, since HubSpot still has the data; every day this isn't fixed is a day added to that backfill.
3. **One job's failure aborts the whole day; plus a dormant-repo scheduling risk.** `main()`'s job loop re-raises on the first exception, so e.g. a HubSpot outage stops the AmpleMarket jobs from running too, and that day never gets a second attempt (partly mitigated by fix #1's lookback). Fix: let each of the 4 jobs (AmpleMarket tasks/calls, HubSpot emails/meetings) fail independently and keep going. Separately (not a code fix): GitHub Actions auto-disables scheduled workflows after ~60 days with no repository activity — worth a periodic manual check if the project goes quiet.
Lower-priority, noted but not queued: the AmpleMarket pager's 200-page safety cap would silently truncate a large backfill rather than erroring; retry logic is only 4 attempts with short backoff; meetings are insert-only (`ON CONFLICT DO NOTHING`) so a rescheduled/cancelled meeting never updates after first capture.
