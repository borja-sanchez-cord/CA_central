# CA Activity Visibility

One central place to see how much and how well each CA (outbound rep) is working their accounts, by merging activity from **AmpleMarket** and **HubSpot** into a single daily-refreshed database with a dashboard for sales leaders.

## Status
- Context, spec, roadmap, decisions: **done** (spec includes verified API facts in §9).
- Scope: **confirmed by Ray.**
- Build: **Phases 1 + 1.5 + 2 + 3 done.** Phases 1/1.5 live on the daily cron and PM-verified; **Phase 2 (identity resolution) done** (`identity/resolve.py`) — PM-validated + production-audited 2026-07-15. **Phase 3 (unified activity model) built 2026-07-16**: `model/build_activity.py` rebuilds the `activity` fact table daily (dedup + attribution per spec §3), `activity_flat` is THE view everything downstream reads, schema changes are Git migrations (`migrations/`), and every dedup/attribution rule has tests on real validated cases (`tests/`, run on every push). The full daily chain is now ingest → identities → migrations → model, all in `daily-run.yml`.
- **Production audit completed 2026-07-15** (5 parallel agents; fixes applied — see the audit entry in `decisions.md`). Next: **Phase 4 (aggregate rep view — the first output leaders can use).**
- Open inputs (parked, not blockers): tryencord counting decision; whether Apollo-sourced sends count; James-F "any other tools" confirmation.

## Read the docs in this order
1. **`docs/context.md`** — why this exists, what "good" looks like, who uses it. Start here.
2. **`docs/spec.md`** — the end-state technical design (data model, sources, dashboard); includes verified AmpleMarket + HubSpot API facts in §9.
3. **`docs/roadmap.md`** — the phased build plan, working agreement, and per-phase checks.
4. **`docs/decisions.md`** — every settled decision and its reasoning.

## Operations (runbook)
- **Backfill a missed/failed day:** GitHub Actions → *daily-run* workflow → *Run workflow* with a `YYYY-MM-DD` date input (single-day activity backfill; entity sync happens only on scheduled runs). Locally: `python3 ingestion/ingest.py YYYY-MM-DD`.
- **Identity refresh:** `python3 identity/resolve.py` — now also runs daily in the cron. Re-run manually after editing `config/ca_teams.json`, a CA joining/leaving, or the tryencord decision.
- **Model rebuild:** `python3 model/build_activity.py` — full deterministic rebuild of the `activity` fact table; also runs daily in the cron after identities. Re-run after any rule change or identity refresh.
- **Schema change (model layer):** add a numbered `migrations/NNN_*.sql` file and merge — `migrations/apply.py` applies it on the next daily run (or run it locally). Never edit live tables by hand.
- **Tests:** `python3 -m pytest tests/ -q` — every dedup/attribution rule against real validated fixtures; also runs on every push (GitHub Actions *Rule tests*). Change a rule in `model/rules.py` → a test must prove the known-right answers still come out.
- **Secrets:** GitHub Actions secrets `AMPLEMARKET_API_KEY` / `HUBSPOT_PRIVATE_APP_TOKEN` / `SUPABASE_DB_URL` — rotate there + in the local `.env`. (`.env.example`'s `SUPABASE_URL`/`SUPABASE_KEY` are reserved for the future dashboard; the pipeline doesn't use them.)
- **Scheduling:** the GitHub cron is best-effort (observed firing 5h late) — the 5-day lookback absorbs missed days. Failure notification is GitHub's default email only, so glance at `ingestion_runs` weekly.

### Fixing a data problem a rep flags (you fix, you don't undo — this is by design)
When a rep says "that activity is wrong/missing/mis-attributed," the architecture is built so fixes are *change-and-re-run*, never a destructive rollback. Nothing here loses the good data already stored.
1. **Diagnose against the source of truth.** Every raw row keeps the *complete* original API record in its `raw` jsonb column — read it to see exactly what HubSpot/AmpleMarket reported, and/or re-query the API live to compare. If our copy matches the source, the source is what the rep is really disputing.
2. **Wrong attribution / wrong person / double-counted / roster wrong** → it's identity logic. Fix the rule in `identity/resolve.py` or the policy in `config/ca_teams.json`, then re-run `resolve.py` (and then `model/build_activity.py` — the model reads the identities). It **full-rebuilds deterministically** — no patching, no undo; the old snapshot is simply replaced. (Confirm a fix changed only what you intended by snapshotting the output tables' business-column hashes before/after — the pattern used in the 2026-07-15 audit.)
3. **Wrong dedup / wrong channel / a real send missing from the clean table** → it's model logic (Phase 3). Every raw row is in exactly one `activity` row's `source_ids` (with `counts` + `excluded_reason` explaining any exclusion), so first find the raw row's activity row and read why it landed there. Then fix the rule in `model/rules.py`, **add a fixture test for the case** (tests/fixtures pattern), and re-run `model/build_activity.py` — same disposable-rebuild contract as identities.
4. **Missing or mis-captured activity** → it's the raw pull. Fix `ingestion/ingest.py`, then re-pull the affected day(s) — re-runs are idempotent (`ON CONFLICT DO NOTHING`), so this only *adds*. To correct a field on rows already stored (DO-NOTHING won't overwrite them), run a targeted one-time `UPDATE` backfill (precedent: the 2026-07-15 email-body/recipient backfill). Then re-run resolve + build.
5. **The ONE thing you cannot recover:** AmpleMarket tasks/calls that have **aged out of its rolling API feed** — if we never pulled them, they're gone (HubSpot data is fully backfillable; AmpleMarket's feed is not). This is exactly why the lookback is 5 days and the pull runs daily. More compute speeds up diagnosis and writing the fix; it cannot resurrect aged-out AmpleMarket history.

**Rule of thumb:** raw layer = append-only faithful copy (never interpret it); identity + model layers = disposable rebuilds (regenerate them freely). Every fix lives in one of those moves.

## How this is built
Claude Code builds one phase at a time; the project owner acts as a non-technical PM. The **working agreement** at the top of `roadmap.md` is binding — build one phase, prove it in plain terms, then move on. Technical detail (schemas, API calls) is figured out per phase, not pre-written.

## Ground rules
- Build only what the current phase needs; no scaffolding ahead. Code lands from Phase 0 (cron skeleton) and Phase 1 (ingestion). **Raw landing tables** exist from Phase 1; the **final normalized schema** and Git-managed migrations begin at Phase 3.
- Never commit secrets (keys, tokens, DB credentials) — use environment variables / GitHub Secrets. Only `.env.example` (blank) is tracked.
- Log real technical choices — and any reversals — in `decisions.md` as you go.
