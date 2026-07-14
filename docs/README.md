# CA Activity Visibility

One central place to see how much and how well each CA (outbound rep) is working their accounts, by merging activity from **AmpleMarket** and **HubSpot** into a single daily-refreshed database with a dashboard for sales leaders.

## Status
- Context, spec, roadmap, decisions: **done** (spec includes verified API facts in §9).
- Scope: **confirmed by Ray.**
- Build: **Phase 0 (foundations) and Phase 1 (daily raw ingestion) built, live on a daily cloud schedule, and validated against real reps.** Next: Phase 2 (identity resolution).
- Open items feeding Phase 2: an authoritative CA roster (AmpleMarket's role field is unreliable) and the Apollo decision (see `decisions.md`).

## Read the docs in this order
1. **`docs/context.md`** — why this exists, what "good" looks like, who uses it. Start here.
2. **`docs/spec.md`** — the end-state technical design (data model, sources, dashboard); includes verified AmpleMarket + HubSpot API facts in §9.
3. **`docs/roadmap.md`** — the phased build plan, working agreement, and per-phase checks.
4. **`docs/decisions.md`** — every settled decision and its reasoning.

## How this is built
Claude Code builds one phase at a time; the project owner acts as a non-technical PM. The **working agreement** at the top of `roadmap.md` is binding — build one phase, prove it in plain terms, then move on. Technical detail (schemas, API calls) is figured out per phase, not pre-written.

## Ground rules
- Build only what the current phase needs; no scaffolding ahead. Code lands from Phase 0 (cron skeleton) and Phase 1 (ingestion). **Raw landing tables** exist from Phase 1; the **final normalized schema** and Git-managed migrations begin at Phase 3.
- Never commit secrets (keys, tokens, DB credentials) — use environment variables / GitHub Secrets. Only `.env.example` (blank) is tracked.
- Log real technical choices — and any reversals — in `decisions.md` as you go.
