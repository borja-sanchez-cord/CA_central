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

**Phase 1 raw ingestion: land faithful per-source copies, keyed by source id, idempotent.**
Four raw landing tables (`raw_amplemarket_tasks`, `raw_amplemarket_calls`, `raw_hubspot_emails`, `raw_hubspot_meetings`) plus an `ingestion_runs` audit log. Each row stores a few extracted columns for convenience plus the full source payload in a `raw` jsonb column, so nothing is lost before Phase 3 normalization. Primary key = source id; `INSERT ... ON CONFLICT DO NOTHING`, so re-running a day inserts zero duplicates. AmpleMarket ignores date filters, so we page newest-first and stop once we cross below the target day (tasks keyed on `finished_on` with `status=completed`; calls on `start_date`).

**HubSpot email exclusions at ingestion (source-of-truth split).**
Skip emails whose `hs_object_source_detail_1` = `Amplemarket` (already counted in AmpleMarket — prevents double-counting) and warmup noise (subject contains `lemwarmup`/`lemwarm`/`amplemarketwarmup`/`warmupemail`). Everything else is kept raw, including manual Gmail (`hs_object_source` = EMAIL) and Apollo-sourced emails. **Open question for the counting phase:** Apollo is a second outreach tool present in HubSpot but not in the plan — decide later whether Apollo emails count as rep activity. Exclusion counts are recorded per run in `ingestion_runs`.

**Supabase access for ingestion: direct Postgres connection (transaction pooler).**
The ingestion job connects with the `SUPABASE_DB_URL` transaction-pooler connection string (needed for creating tables + bulk writes, which the REST API can't do). The earlier `SUPABASE_KEY` (service key) remains for the dashboard/REST side later. Both stored as secrets, never committed.

**Cloud cron platform: GitHub Actions.**
The daily scheduled job runs as a GitHub Actions workflow (`.github/workflows/daily-run.yml`). Chosen over Supabase scheduled functions / external schedulers because the repo already lives on GitHub, it's free at this usage, requires no new account or infra, and its encrypted Secrets store holds the API keys for later phases (never committed). Runs in the cloud so nothing depends on a local machine. Phase 0 version logs a run only; real ingestion is added in Phase 1.

**Build model: Claude Code builds each phase; owner acts as non-technical PM.**
Technical figuring-out (schemas, API calls, pagination, error handling) is Claude's per phase — not pre-written, to avoid over-planning. The roadmap therefore carries a working agreement and a plain per-phase check the PM can personally verify.
