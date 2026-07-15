# CA Activity Visibility

One central place to see how much and how well each CA (outbound rep) is working their accounts, by merging activity from **AmpleMarket** and **HubSpot** into a single daily-refreshed database with a dashboard for sales leaders.

## Status
- Context, spec, roadmap, decisions: **done** (spec includes verified API facts in §9).
- Scope: **confirmed by Ray.**
- Build: **Phases 1 + 1.5 + 2 done.** Phases 1/1.5 live on the daily cron and PM-verified; **Phase 2 (identity resolution) done** (`identity/resolve.py`) — PM-validated + production-audited 2026-07-15; the **17-CA roster** is resolved from HubSpot teams (PM-confirmed). Carried into Phase 3: wire `resolve.py` into the cron; tryencord counting + James-F tool-list confirmations (both parked, not blockers).
- **Production audit completed 2026-07-15** (5 parallel agents; fixes applied — see the audit entry in `decisions.md`). Next: **Phase 3 (unified activity model).**

## Read the docs in this order
1. **`docs/context.md`** — why this exists, what "good" looks like, who uses it. Start here.
2. **`docs/spec.md`** — the end-state technical design (data model, sources, dashboard); includes verified AmpleMarket + HubSpot API facts in §9.
3. **`docs/roadmap.md`** — the phased build plan, working agreement, and per-phase checks.
4. **`docs/decisions.md`** — every settled decision and its reasoning.

## Operations (runbook)
- **Backfill a missed/failed day:** GitHub Actions → *daily-run* workflow → *Run workflow* with a `YYYY-MM-DD` date input (single-day activity backfill; entity sync happens only on scheduled runs). Locally: `python3 ingestion/ingest.py YYYY-MM-DD`.
- **Identity refresh:** `python3 identity/resolve.py` — manual until wired into the cron after PM sign-off. Re-run after editing `config/ca_teams.json`, a CA joining/leaving, or the tryencord decision.
- **Secrets:** GitHub Actions secrets `AMPLEMARKET_API_KEY` / `HUBSPOT_PRIVATE_APP_TOKEN` / `SUPABASE_DB_URL` — rotate there + in the local `.env`. (`.env.example`'s `SUPABASE_URL`/`SUPABASE_KEY` are reserved for the future dashboard; the pipeline doesn't use them.)
- **Scheduling:** the GitHub cron is best-effort (observed firing 5h late) — the 5-day lookback absorbs missed days. Failure notification is GitHub's default email only, so glance at `ingestion_runs` weekly.

## How this is built
Claude Code builds one phase at a time; the project owner acts as a non-technical PM. The **working agreement** at the top of `roadmap.md` is binding — build one phase, prove it in plain terms, then move on. Technical detail (schemas, API calls) is figured out per phase, not pre-written.

## Ground rules
- Build only what the current phase needs; no scaffolding ahead. Code lands from Phase 0 (cron skeleton) and Phase 1 (ingestion). **Raw landing tables** exist from Phase 1; the **final normalized schema** and Git-managed migrations begin at Phase 3.
- Never commit secrets (keys, tokens, DB credentials) — use environment variables / GitHub Secrets. Only `.env.example` (blank) is tracked.
- Log real technical choices — and any reversals — in `decisions.md` as you go.
