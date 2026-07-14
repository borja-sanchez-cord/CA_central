# CA Activity Visibility — Roadmap

**Start here if you're building.** Read `context.md` (why this exists) and `spec.md` (the end-state design, including verified AmpleMarket + HubSpot API facts in §9) before starting. `decisions.md` records every settled choice — don't reverse one without adding a superseding entry.

**8 phases (0–7).** Each is self-contained, ships something verifiable, and depends only on the phases before it. Phases 0–3 build the data foundation, Phase 4 delivers the first output leaders can use, and 5–7 add depth, quality, and the dashboard.

---

## What already exists (read this if you're picking up mid-build)

Phases 0–1 are built and live. Before writing anything, orient here:

- **Code:** all ingestion is in [`ingestion/ingest.py`](../ingestion/ingest.py) (single script, stdlib + `psycopg2`; deps in `ingestion/requirements.txt`). This is the working reference for how both APIs are actually called (pagination, auth, rate limits) — reuse its patterns, don't re-derive them.
- **Runs:** daily via GitHub Actions (`.github/workflows/daily-run.yml`); secrets (API keys, DB URL) live in GitHub Secrets, never in the repo.
- **DB connection:** direct Postgres via the `SUPABASE_DB_URL` transaction-pooler string, read from env or a local `.env` (see `.env.example`; real `.env` is gitignored).
- **Tables already in Supabase (raw landing layer):** `raw_amplemarket_tasks`, `raw_amplemarket_calls`, `raw_hubspot_emails`, `raw_hubspot_meetings`, plus an `ingestion_runs` audit log. Each has a few extracted columns + the full source payload in a `raw` jsonb column. Primary key = source id; re-runs are idempotent (`ON CONFLICT DO NOTHING`). The **normalized** schema (activity fact + dimensions) does **not** exist yet — it begins at Phase 3.
- **The normalized data model, source/dedup rules, and verified API facts** are in `spec.md` (§2, §3, §9). Every settled choice and every correction found during the build is in `decisions.md`. Read both before Phase 2+.

---

## Working agreement (how to build this)

The person owning this project is a **non-technical PM**. The building agent must follow these rules on its own:

1. **One phase at a time.** Do not start a phase until the previous phase's success metric is met and shown to the PM.
2. **Do not scaffold ahead.** Build only what the current phase needs. No tables, code, or dependencies for later phases.
3. **Prove "done" in plain terms.** End every phase by showing the PM the "PM check" for that phase — something a non-technical person can judge (a number that matches the real tool, a screenshot, a short summary). "Done" is not done until the PM can see it.
4. **Ask before anything irreversible.** Deleting data, changing access/permissions, publishing, or spending money → stop and ask the PM first.
5. **Never commit secrets.** API keys, tokens, DB credentials go in environment variables/secrets, never in the repo.
6. **Log as you go.** When you make a real technical choice, add a line to `decisions.md` with the reason.
7. **Figure out the technical detail yourself.** Schemas, exact API calls, pagination, and error handling are yours to determine per phase — they are deliberately not pre-written here.

---

## Phase 0 — Foundations

**Plain terms:** Set up the workspace and keys so we can safely reach both AmpleMarket and HubSpot, and stand up the empty database. Nothing is analysed yet — laying the pipes.

**Work:** obtain HubSpot private-app token + AmpleMarket API key; create the Supabase project (security settings per `decisions.md`); stand up an empty cloud cron that runs but does nothing yet; ensure repo docs are in place.

**Success metric:** both APIs authenticate; the empty database exists; the cron fires on schedule and logs a run.

**PM check:** Claude shows you a run log proving it connected to both tools and the scheduled job fired.

**Depends on:** nothing.

---

## Phase 1 — Daily ingestion (raw)  ✅ DONE (live daily cron; validated with 4+ reps) — 3 follow-up fixes queued (see below)

**Plain terms:** Start automatically copying every rep's raw activity — calls, LinkedIn actions, emails, meetings — out of the two tools into our database, once a day. Just a faithful copy at this stage, not yet cleaned or merged.

**Work:** AmpleMarket daily pull of calls + tasks (per user, **paginating all users**), capturing channel, timestamp, automatic flag, contact, sequence name; HubSpot daily pull of emails + meetings, **keeping all origins tagged (Amplemarket / Apollo / manual Gmail)** and dropping only warmup noise; land both in raw staging; re-runs must not duplicate; respect rate limits (HubSpot Search ~4 req/sec).

**Success metric:** a daily run lands a full day of activity from both sources; re-running the same day creates no duplicates.

**PM check:** Claude shows you a count of activities pulled per rep for one day, and confirms running it twice didn't double the numbers. *(Done — spot-checked itemised days with Yuvi, Andrew, Yianni, James Falconer, Nico; confirmed by 4+ CAs.)*

**Depends on:** Phase 0.

**Corrections logged during build (see `decisions.md`):** paginate AmpleMarket `/users` (was capped at 20); **keep** AmpleMarket-synced HubSpot emails (AmpleMarket exposes email *tasks*, not *sends*, so dropping them undercounted). De-dup/direction rules moved to Phase 3.

**TODO — follow-up fixes found in code review (2026-07-14, not yet applied; small, low-risk, all in `ingestion/ingest.py`). See `decisions.md` for full detail.**
1. **Widen the daily pull to the last ~3–4 days, not just yesterday.** Some HubSpot/AmpleMarket records land a few hours after we've already taken that day's snapshot, so they're silently never captured (confirmed: 13 Jul emails were 1377 captured vs 1379 live the next day; tasks grew 185→304 across re-runs). Safe fix — re-runs are already proven duplicate-free.
2. **Capture HubSpot email recipient + meeting attendees/outcome — currently not pulled at all.** The stored `raw` payload only has the fields we explicitly request (sender, subject, timing, source, owner) — not who an email went to or who was in a meeting. This blocks Phase 3 rules already written (internal-recipient exclusion, invite fan-out, attaching an email to its prospect) and blocks meeting→rep attribution entirely (no attendees, and `owner_id` is proven unreliable). Needs: add the fields to the API pull, then a one-time re-pull of the days already ingested to backfill.
3. **Isolate errors per job + note a scheduling risk.** One failing job (e.g. HubSpot down) currently aborts the whole day's run; make the four jobs (AmpleMarket tasks/calls, HubSpot emails/meetings) fail independently. Separately: GitHub auto-disables scheduled workflows after ~60 days of repo inactivity — worth a periodic check, not a code fix. Mostly covered by fix #1 above (a missed day gets swept up on the next run's lookback).

---

## Phase 2 — Identity resolution + account de-duplication

**Plain terms:** Make sure "the same person," "the same company," **and "the same rep"** are each recognised as one — even across two systems, despite HubSpot sometimes storing a company twice, and despite a rep having more than one login/email. Without this, activity splits across duplicates and the numbers come out wrong.

**Work:**
- **Reps (new — discovered in Phase 1):** agree an **authoritative CA roster** (AmpleMarket's `role` field is unreliable — active reps show as `admin`); **link each rep's multiple accounts + email addresses into one rep identity** (e.g. Nico `@encord.ai`/`@encord.com`, Yuvi ×2, Callum duplicate). Without this, a rep's own emails get misread as inbound and their activity is split.
- **Build the rep-identity map from the AmpleMarket `/users` API.** It returns every rep's internal ID, email, and a `mailboxes` list linking their addresses — **for all reps, including those who logged no tasks.** This is **load-bearing for calls:** an AmpleMarket call record names its rep *only* by internal ID (no name/email), so a call can be attributed to a person **only** via this map — never from the rep's task history. (Validated 2026-07-14: Yuvi logged 0 tasks on 13 Jul, so his two answered calls were invisible until mapped via `/users`, then surfaced correctly and matched what he reported.)
- **People:** match by exact lowercased email (fuzzy name fallback).
- **Companies:** match by domain (fuzzy name fallback, guard free-email domains); **collapse HubSpot duplicate companies by domain (mandatory)**.
- Persist an identity crosswalk for incremental daily joins.

**Success metric:** each contact, company, **and rep** resolves to a single ID; HubSpot company duplicates collapsed; rep accounts/addresses linked; a match rate and an unresolved-records list are produced.

**PM check:** Claude shows you the match rate, the linked rep identities, and a short list of anything it couldn't confidently match, for your eyes.

**Depends on:** Phase 1. The **technical** rep-identity map (which internal ID = which person + addresses) is available **now** from the AmpleMarket `/users` API — *not* blocked on Ray. Ray / sales leadership are needed only to confirm **who counts as a CA** (team membership), which can proceed in parallel.

---

## Phase 3 — Unified activity model + flat view

**Plain terms:** Turn the raw, mismatched copies into one clean table where every row is a single thing a rep did — tagged with who, which company, which channel. The tidy foundation everything else reads from.

**Work:** normalize staging into the activity fact table + account/contact/rep dimensions (per spec); attach each activity to its resolved account + contact; materialize the single flat analytics view; keep the `body` column present but null (v2-ready). Connect Supabase↔GitHub for schema migrations here.

**De-duplication, attribution & direction rules (confirmed during Phase 1 validation — the raw layer keeps everything; these are applied here on read):**
- **Attribute emails by SENDER, not `owner_id`.** Who sent an email = its `hs_email_from_email` address, matched to the rep's linked addresses (from the `/users` map, Phase 2). HubSpot's `hubspot_owner_id` is **not** reliable rep identity: one owner_id is shared across several reps (e.g. `538916758` carried Kamil, Yianni, George Lim, Nico, Ilaria), and one rep's mail spans many owner_ids (Dillon across 6). *Some* reps do have a clean owner_id (Yuvi, Katie, Joe) — but you cannot rely on it, so always attribute by sender.
- **Sent vs received email split.** Direction follows from the sender: sender is one of the rep's addresses → outbound (counts as rep *effort*); otherwise inbound (a prospect reply, tracked separately as an *engagement/outcome* signal). (Validated on Yianni: 3 sent vs 2 received; Yuvi 13 Jul: 3 of 30 were inbound prospect replies.)
- **Same-email cross-tool duplicates.** The same outbound email can be logged into HubSpot by *both* Apollo and AmpleMarket. Treat as one email when **subject + date + time all match** (sender used as an extra safeguard). (Validated on James Falconer: "Re: VLMs in identity", same date/time, logged by both.)
- **AmpleMarket task ↔ send reconciliation.** AmpleMarket exposes email *tasks* but not *sends*; sends appear only as the HubSpot-synced copy. Reconcile so a task and its resulting send aren't counted twice, without dropping sends. (See decisions.md.)
- **Calls: count dial *attempts* and *conversations* separately — do NOT collapse on `task_id`.** A rep chasing one contact fires several call records in a tight window (Joe Turner 13 Jul: 4 calls in 123s to 4 *different numbers* for one contact = 4 attempts, 1 real conversation). `task_id` is **often null** on `/calls` (it was on all 4), so the earlier "collapse via `task_id`" idea does not work in practice → group a conversation by **same contact + tight time window**. A record is a genuine conversation only when `human = true`; an `answered = true` record can still be voicemail/IVR (machine). Surface **both** measures (attempts *and* connects/conversations) — collapsing to one erases real effort, counting all as conversations inflates volume.
- **Calendar-invite fan-out.** One calendar action is logged **once per attendee** (Dillon 13 Jul: 6 identical Apollo "Updated invitation" rows, same timestamp, one per invitee) → collapse to a single activity (same subject + time, differing only by recipient).
- **Exclude internal recipients.** Email/invite activity whose only recipients are internal `@encord.com` people is not outbound CA activity (Dillon's invite attendees were all internal) → don't count it as outreach.

**Success metric:** one flat table returns every activity with account + contact + channel attached; a manual spot-check of one rep's day matches the source tools; no double-counting.

**PM check:** Claude picks one rep + one day, shows the tool's numbers next to ours side by side, and they match.

**Depends on:** Phase 2.

---

## Phase 4 — Aggregate rep view  ← first usable output

**Plain terms:** The first thing leaders can actually look at: per rep, how much they did, across which channels, and whether they're touching all the accounts they own or only some.

**Work:** per-rep totals by channel; accounts touched vs. owned (coverage % from `target account owner`); total contacts and contacts-per-account; time windows (7 days, 30 days, custom).

**Success metric:** per-rep totals and coverage % reconcile against a manual check; coverage correctly reflects owned vs. touched.

**PM check:** you look at one rep's totals + coverage % and it matches what you'd expect / can spot-verify.

**Depends on:** Phase 3.

---

## Phase 5 — Per-account & per-contact drill-down

**Plain terms:** Go deeper. For a rep, see each account and how hard it's worked — how many people, touchpoints, channels — so we can spot reps hammering a few accounts and ignoring the rest.

**Work:** rep → account view (per account: touchpoints, distinct contacts, channel mix); account → contact view (per contact: touchpoints by channel, job title); make concentration visible.

**Success metric:** from any rep you can drill to an account and see its contacts and breakdown, matching the totals above it.

**PM check:** you click a rep, then an account, and see its people and touchpoints — the account numbers add up to the rep's total.

**Depends on:** Phase 4.

---

## Phase 6 — Quality & outcomes lens

**Plain terms:** Add the "is this good activity, not just a lot of activity?" layer — right-tier accounts, senior enough people, personal vs. automated, and what it produced (meetings, replies).

**Work:** enrich accounts with ICP tier + vertical; auto-classify contact seniority from job title (rules-first, LLM fallback, cached); automated vs. manual split (AmpleMarket authoritative, HubSpot proxy); outcomes from `rep_metrics` (meetings booked + rate, interested-not-booked, open/reply rates, completed/overdue tasks). Optional target thresholds / red-amber-green flags if benchmarks are provided.

**Success metric:** activity is sliceable by tier, vertical, seniority; automated/manual split shown with confidence; outcome metrics populate per rep per period.

**PM check:** you filter one rep to a tier or seniority level and the split looks right; outcome numbers (e.g. meetings booked) match the tool.

**Depends on:** Phase 5.

---

## Phase 7 — Dashboard

**Plain terms:** Wrap it all in a screen leaders use themselves — filter to a rep, tier, time window, and click from rep to account to contact. No database or code needed.

**Work:** UI on the flat view (tool TBD: Metabase / Retool / custom); filters (rep, tier, vertical, time); drill path rep → account → contact with quality lens at rep + account level; read-only; export for others (e.g. Falkner).

**Success metric:** a non-technical leader can, unaided, filter to a rep and drill to a contact; exported data matches the dashboard.

**PM check:** you (or Falkner) use it start to finish without help, and it gives the right answer.

**Depends on:** Phase 6.

---

## Sequencing notes

- **First value lands at Phase 4** — the aggregate view the stakeholder called "the first job."
- Phases 0–3 have no user-visible output but are prerequisites; don't skip or reorder.
- Phase 6's threshold/flag behaviour is a config decision, not a blocker — display-only works until benchmarks are set.
- **v2** (message-body / tailored-vs-generic analysis) sits after Phase 7: switch on the AmpleMarket webhook and layer analysis on the already-present `body` field. The `body` field must stay in the schema from Phase 3 so v2 needs no rebuild.
