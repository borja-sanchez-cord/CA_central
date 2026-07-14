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

**Build model: Claude Code builds each phase; owner acts as non-technical PM.**
Technical figuring-out (schemas, API calls, pagination, error handling) is Claude's per phase — not pre-written, to avoid over-planning. The roadmap therefore carries a working agreement and a plain per-phase check the PM can personally verify.
