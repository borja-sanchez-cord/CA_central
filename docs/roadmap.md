# CA Activity Visibility — Roadmap

**Start here if you're building.** Read `context.md` (why this exists) and `spec.md` (the end-state design, including verified AmpleMarket + HubSpot API facts in §9) before starting. `decisions.md` records every settled choice — don't reverse one without adding a superseding entry.

**8 phases (0–7), plus a small ingestion add-on (1.5).** Each is self-contained, ships something verifiable, and depends only on the phases before it. Phases 0–3 build the data foundation, Phase 4 delivers the first output leaders can use, and 5–7 add depth, quality, and the dashboard. (Phase 1.5 is a short raw-ingestion step slotted between 1 and 2 — see below.)

---

## What already exists (read this if you're picking up mid-build)

Phases 0–1 are built and live. Before writing anything, orient here:

- **Code:** all ingestion is in [`ingestion/ingest.py`](../ingestion/ingest.py) (single script, stdlib + `psycopg2`; deps in `ingestion/requirements.txt`). This is the working reference for how both APIs are actually called (pagination, auth, rate limits) — reuse its patterns, don't re-derive them.
- **Runs:** daily via GitHub Actions (`.github/workflows/daily-run.yml`); secrets (API keys, DB URL) live in GitHub Secrets, never in the repo.
- **DB connection:** direct Postgres via the `SUPABASE_DB_URL` transaction-pooler string, read from env or a local `.env` (see `.env.example`; real `.env` is gitignored).
- **Tables already in Supabase (raw landing layer):** `raw_amplemarket_tasks`, `raw_amplemarket_calls`, `raw_hubspot_emails`, `raw_hubspot_meetings`, plus an `ingestion_runs` audit log. Each has a few extracted columns + the full source payload in a `raw` jsonb column. Primary key = source id; re-runs are idempotent (`ON CONFLICT DO NOTHING`). The **normalized** schema (activity fact + dimensions) does **not** exist yet — it begins at Phase 3.
- **Company/Contact objects (Phase 1.5, live):** `raw_hubspot_companies` = full mirror (~154k, incremental by last-modified). `raw_hubspot_contacts` = **activity-scoped** mirror — only prospects appearing in real activity, re-read fresh each run, self-extending as reps touch new people. Deliberate trade-off: never-touched contacts absent (Phase 5 whitespace denominator needs a Supabase upgrade + full mirror). Rep domains excluded from "prospect": `encord.com`, `encord.ai`, **`tryencord.com`** (dedicated outreach domain — its senders are OUR reps).
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

## Phase 1 — Daily ingestion (raw)  ✅ DONE (live daily cron; validated with 4+ reps; code-review fixes applied)

**Plain terms:** Start automatically copying every rep's raw activity — calls, LinkedIn actions, emails, meetings — out of the two tools into our database, once a day. Just a faithful copy at this stage, not yet cleaned or merged.

**Work:** AmpleMarket daily pull of calls + tasks (per user, **paginating all users**), capturing channel, timestamp, automatic flag, contact, sequence name; HubSpot daily pull of emails + meetings, **keeping all origins tagged (Amplemarket / Apollo / manual Gmail / CRM_UI)** and dropping only warmup noise; land both in raw staging; re-runs must not duplicate; respect rate limits (HubSpot Search ~4 req/sec).

**Success metric:** a daily run lands a full day of activity from both sources; re-running the same day creates no duplicates.

**PM check:** Claude shows you a count of activities pulled per rep for one day, and confirms running it twice didn't double the numbers. *(Done — spot-checked itemised days with Yuvi, Andrew, Yianni, James Falconer, Nico; confirmed by 4+ CAs.)*

**Depends on:** Phase 0.

**As built (full history + evidence in `decisions.md`; design facts in `spec.md`):** beyond the base pull, Phase 1 also captures email **bodies + recipients** and meeting **outcome/attendees** (meetings update-on-conflict; the other tables are append-only); each scheduled run **sweeps the last 3 days** because the source APIs keep surfacing a finished day's records late — if `ingestion_runs` shows the oldest sweep day still finding new rows, widen `LOOKBACK_DAYS`; jobs **fail independently** and failures are logged. Known limit: external (prospect) meeting attendees aren't retrievable via this pull.

---

## Phase 1.5 — Ingest HubSpot Company + Contact objects (raw)

**Plain terms:** So far we've only copied *activity* (emails, calls, meetings, tasks). We've never copied the actual **company** and **contact** records themselves — the account's industry/tier/owner, the person's job title and which company they belong to. The next phases need those, so this short step copies them in, the same faithful way Phase 1 copies activity.

**Why it's here (gap found 2026-07-15):** Phase 1 never needed these objects, so they were never pulled — but the spec's `account`/`contact` dimensions (§2) and Phase 4's coverage assume their fields exist. Without this step, Phase 2 has nothing to resolve/dedup *against* and Phase 4 coverage can't be computed. Split out as its own step (not folded into Phase 2) because it's plain ingestion work, same shape as Phase 1 — not identity logic.

**Work — these are DIMENSION tables, not event tables (built differently from Phase 1):** two landing tables (`raw_hubspot_companies`, `raw_hubspot_contacts`) holding the **current state** of each entity: **update-on-conflict** (tiers/owners/titles change — keep latest), **no `activity_date`**, not part of the daily date sweep. Two different sync strategies, deliberately:
- **Companies — FULL mirror (~154k), incremental by last-modified watermark** (first run = full load; daily runs fetch only changes; HubSpot's 10k-per-search cap is beaten by restarting from the watermark). Full, not filtered on `target_account_owner`, because CRM assignment can be missing/stale — filtering would silently drop accounts reps genuinely work. Captured: name, `domain`, `account_icp_tier_validated`, `account_icp__tier_new`, `vertical__aligned_by_team`, `target_account_owner` (an **owner id**; only ~2.8k companies have one — the assigned-target universe), tier/segment.
- **Contacts — ACTIVITY-SCOPED mirror (PM decision 2026-07-15):** the full 446k/~300 MB mirror doesn't fit the Supabase free tier, so we mirror **only contacts that appear in real rep activity** (AmpleMarket task/call contacts + HubSpot email senders/recipients, internal rep domains excluded), fetched by email via HubSpot's `batch/read` (`idProperty: email`), fully re-read each run so jobtitle/company changes stay fresh. **Self-extending:** touch a new person → next run pulls them in. **Known trade-off (accepted):** contacts *nobody ever touched* are absent — so "untouched contacts per account" (the Phase 5 whitespace denominator) needs the full mirror + a Supabase upgrade later; nothing else is lost. Captured: `email`, `jobtitle`, `associatedcompanyid`, names, lifecycle stage (contacts' last-modified property is `lastmodifieddate`, not `hs_lastmodifieddate`).

No normalization/dedup here — that's Phase 2/3.

**Success metric:** companies table populated and refreshing daily; contacts table holds every activity-touched prospect and stays fresh; counts sane against HubSpot.

**PM check:** Claude shows you row counts + sample companies (domain, tier, vertical, target owner) and sample contacts (jobtitle, company) matching what you see in HubSpot.

**Depends on:** Phase 1. (Independent of the rep-roster work — can be built in parallel.)

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

**Depends on:** Phase 1.5 (needs the company/contact objects to resolve against). The **technical** rep-identity map (which internal ID = which person + addresses) is available **now** from the AmpleMarket `/users` API — *not* blocked on Ray. Ray / sales leadership are needed only to confirm **who counts as a CA** (team membership), which can proceed in parallel.

---

## Phase 3 — Unified activity model + flat view

**Plain terms:** Turn the raw, mismatched copies into one clean table where every row is a single thing a rep did — tagged with who, which company, which channel. The tidy foundation everything else reads from.

**Work:** normalize staging into the activity fact table + account/contact/rep dimensions (per spec); attach each activity to its resolved account + contact; materialize the single flat analytics view; carry the HubSpot email `body` through from raw into the activity fact (captured from Phase 1 — see spec §7; AmpleMarket message text stays null until the v2 webhook). Connect Supabase↔GitHub for schema migrations here.

**Code health — do this here, not later (found in code review, 2026-07-14).** `ingestion/ingest.py` is a single ~500-line script with no tests — fine for Phase 1's "pull, don't think" job, verified by eyeballing the numbers. Phase 3 is different: it's real decision-making (dedup rules below, sender-matching, attempts-vs-conversations) — exactly the kind of logic that breaks silently without a test catching it. Before piling this logic into `ingest.py`:
- **Split it out** — dedup/attribution logic as its own module(s), separate from the raw-pull code, not bolted onto Phase 1's script.
- **Add tests** for each dedup/attribution rule below, using real validated cases as fixtures (e.g. James Falconer's Apollo+AmpleMarket duplicate should collapse to 1; Dillon's 6-attendee invite should collapse to 1; George Lim's owner_id `538916758` must NOT be trusted for attribution; **Andrew Bell 2026-07-14 — 17 raw email rows must collapse to ~6 real emails + recognize ~7 invite/notification rows as meetings, exercising the prefix-strip + time-window dedup and the invite↔meeting cross-channel rule**). These are cheap to write now because we already know the right answer for each — much cheaper than debugging a silent regression after the dashboard is live.

**De-duplication, attribution & direction rules — implement ALL of these.** This is the checklist; the **authoritative, corrected statements live in `spec.md` §3** (evidence + history in `decisions.md`). Do not code from memory of these one-liners — read §3 first:
1. Attribute emails by **sender address**, never `hubspot_owner_id` (proven unreliable both directions).
2. **Sent vs received** split: sender ∈ rep's addresses ⇒ outbound effort; else inbound engagement signal.
3. **Same-email cross-tool duplicates** are N-way across varying sources; exact "subject+time" match does NOT work — key is normalized subject (strip tool prefixes) + sender + ±60s window, with `body` as the strongest signal.
4. **Four** email sources incl. `CRM_UI`; unknown source ⇒ treat as manual, never drop.
5. **Calendar invite (email) ↔ meeting object = one event** — never count in both channels.
6. **Calendar-invite fan-out** (one row per attendee) → collapse to one activity.
7. **AmpleMarket email task ↔ HubSpot send** reconciliation (tasks aren't sends).
8. **Calls: attempts vs conversations** counted separately; never collapse on `task_id` (usually null); conversation = same contact + tight window; real conversation only when `human = true`.
9. **Exclude internal-only-recipient** activity from outreach counts.

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

**Full data transparency / auditability (requirement).** Every number must be clickable down to the individual raw activity rows behind it — source tool, timestamp, subject/body, contact — so a user can manually verify any figure and catch mistakes. No aggregate is a black box. This is deliberate: the dedup/attribution rules (Phase 3) are non-trivial, so the dashboard must surface the raw evidence for any count, keeping errors visible instead of hidden in a rollup.

**Success metric:** a non-technical leader can, unaided, filter to a rep and drill to a contact; exported data matches the dashboard.

**PM check:** you (or Falkner) use it start to finish without help, and it gives the right answer.

**Depends on:** Phase 6.

---

## Sequencing notes

- **First value lands at Phase 4** — the aggregate view the stakeholder called "the first job."
- Phases 0–3 (incl. 1.5) have no user-visible output but are prerequisites; don't skip or reorder.
- Phase 6's threshold/flag behaviour is a config decision, not a blocker — display-only works until benchmarks are set.
- **v2** (message-body / tailored-vs-generic analysis) sits after Phase 7: switch on the AmpleMarket webhook and layer analysis on the already-present `body` field. The `body` field must stay in the schema from Phase 3 so v2 needs no rebuild.
