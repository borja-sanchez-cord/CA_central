# CA Activity Visibility — Roadmap

**8 phases (0–7).** Each phase is self-contained, ships something verifiable, and depends only on phases before it. Phases 0–3 build the data foundation, Phase 4 delivers the first usable output, and 5–7 add depth, quality, and the dashboard.

Each phase has: a plain-English summary (what it does and why it matters), the technical work, a success metric, and its dependency.

---

## Phase 0 — Foundations

**In plain terms:** Set up the workspace and keys so we can safely reach both AmpleMarket and HubSpot, and stand up the empty database the data will live in. Nothing is analysed yet — this is laying the pipes.

**Work:**
- Obtain HubSpot private-app token + AmpleMarket API key (Ray sourcing).
- Create the Supabase (Postgres) project.
- Stand up an empty cloud cron skeleton (scheduled function that runs but does nothing yet).
- Repo docs in place (spec, research, decisions, this roadmap).

**Success metric:** Both APIs authenticate successfully; the empty database exists; the cron fires on schedule and logs a run.

**Depends on:** nothing.

---

## Phase 1 — Daily ingestion (raw)

**In plain terms:** Start automatically copying every rep's raw activity — calls, LinkedIn actions, emails, meetings — out of the two tools and into our database, once a day. At this stage it's just a faithful copy, not yet cleaned or merged.

**Work:**
- AmpleMarket: daily REST pull of `/calls` and `/tasks` (per `user_id`), capturing channel, timestamp, `automatic` flag, contact.
- HubSpot: daily pull of `emails` and `meetings` objects only (per source-split rule); exclude `lemwarmup` and `amplemarket`-sourced records.
- Land both into raw staging tables. Re-runs must not create duplicates.
- Respect rate limits (HubSpot Search ~4 req/sec is the constraint).

**Success metric:** A daily run lands a full day of activity from both sources into staging; re-running the same day produces no duplicate rows.

**Depends on:** Phase 0.

---

## Phase 2 — Identity resolution + account de-duplication

**In plain terms:** Make sure "the same person" and "the same company" are recognised as one, even though they appear in two systems and HubSpot sometimes stores a company twice. Without this, one account's activity could be split across duplicates and the coverage numbers would be wrong.

**Work:**
- Person match: lowercased email (exact); fuzzy name+company only as fallback.
- Company match: normalized web/email domain; fuzzy name only as fallback; guard free-email domains.
- **Collapse HubSpot duplicate company records by domain** (Ray-confirmed issue) — mandatory, upstream of all coverage.
- Persist an `identity_crosswalk` so daily runs are incremental joins, not re-resolution.

**Success metric:** Each contact and company resolves to a single ID; HubSpot company duplicates are collapsed; report a match rate and a short list of unresolved records for review.

**Depends on:** Phase 1.

---

## Phase 3 — Unified activity model + flat view

**In plain terms:** Turn the raw, mismatched copies into one clean, consistent table where every row is a single thing a rep did, tagged with who, which company, which channel. This is the tidy foundation everything else reads from.

**Work:**
- Normalize staging into the `activity` fact table + `account` / `contact` / `rep` dimensions (per spec).
- Apply the source-of-truth split so AmpleMarket-synced activity isn't double-counted with HubSpot.
- Attach each activity to its resolved account + contact (per-object association for HubSpot).
- Materialize the single flat analytics view (join of all tables) for querying.
- `body` column present but null (v2-ready).

**Success metric:** One flat table returns every activity with account + contact + channel attached; a manual spot-check of one rep's day matches the source tools; no double-counted records.

**Depends on:** Phase 2.

---

## Phase 4 — Aggregate rep view  ← first usable output

**In plain terms:** The first thing leaders can actually look at: for each rep, how much did they do, across which channels, and are they touching all the accounts they own or only some. This answers the original "are they doing enough, and spreading across their territory?" question.

**Work:**
- Per-rep totals by channel (calls, LI messages, LI connects, manual/auto emails, meetings).
- Accounts touched vs. accounts owned (`target account owner` → coverage %).
- Total contacts and contacts-per-account.
- Time windows: last 7 days, last 30 days, custom.

**Success metric:** Per-rep totals and coverage % reconcile against a manual check; coverage correctly reflects owned vs. touched accounts.

**Depends on:** Phase 3.

---

## Phase 5 — Per-account & per-contact drill-down

**In plain terms:** Go one level deeper. For a given rep, see each of their accounts and how hard each one is being worked — how many people, how many touchpoints, which channels — so we can spot reps hammering a few accounts and ignoring the rest.

**Work:**
- Rep → account view: per account, touchpoints, distinct contacts, channel mix.
- Account → contact view: per contact, touchpoints by channel, job title.
- Distribution visibility (e.g. is activity concentrated in a handful of accounts).

**Success metric:** From any rep you can drill to a specific account and see its contacts and touchpoint breakdown, matching the aggregate totals above it.

**Depends on:** Phase 4.

---

## Phase 6 — Quality & outcomes lens

**In plain terms:** Add the "is this good activity, not just a lot of activity?" layer — whether reps are hitting the right-tier accounts and senior enough people, how much is personal vs. automated, and what it's producing (meetings, replies). This turns volume into a judgement of quality.

**Work:**
- Enrich accounts with ICP tier (validated + new) and vertical.
- Auto-classify contact seniority from job title (rules-first, LLM fallback, cached).
- Automated vs. manual split (AmpleMarket flag authoritative; HubSpot proxy, lower confidence).
- Outcomes from `rep_metrics`: meetings booked + rate, interested-not-booked, email open/reply rates, completed/overdue tasks.
- **(Config decision)** optional target thresholds / RAG flags if benchmarks are provided.

**Success metric:** Activity is sliceable by tier, vertical, and seniority; automated/manual split shown with confidence; outcome metrics populate per rep per period.

**Depends on:** Phase 5.

---

## Phase 7 — Dashboard

**In plain terms:** Wrap all of the above in a screen sales leaders can use themselves — filter to a rep, a tier, a time window, and click down from rep to account to contact — no database or code needed.

**Work:**
- UI on the flat view (tool TBD: Metabase / Retool / custom).
- Filters: rep, ICP tier, vertical, time window.
- Drill path: rep → account → contact, with quality lens at rep + account level.
- Read-only; export so others (e.g. Falkner) can pull data.

**Success metric:** A non-technical leader can, unaided, filter to a rep and drill to a contact; exported data matches the dashboard.

**Depends on:** Phase 6.

---

## Sequencing notes

- **First value lands at Phase 4** — the aggregate view the transcript called "the first job."
- Phases 0–3 have no user-visible output but are prerequisites; don't skip or reorder.
- Phase 6's threshold/RAG behaviour is a config decision, not a blocker — display-only works until benchmarks are set.
- v2 (message-body / tailored-vs-generic analysis) sits after Phase 7: switch on the AmpleMarket webhook and layer analysis on the already-present `body` field.
