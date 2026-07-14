# CA Activity Visibility

One central place to see how much and how well each CA (outbound rep) is working their accounts, by merging activity from **AmpleMarket** and **HubSpot** into a single daily-refreshed database with a dashboard for sales leaders.

## Status
- Context, spec, research, and roadmap: **done.**
- Scope: **confirmed by Ray.**
- Build: **not started** — begins at roadmap Phase 0.

## Read the docs in this order
1. **`docs/context.md`** — why this exists, what "good" looks like, who uses it. Start here.
2. **`docs/spec.md`** — the end-state technical design (data model, sources, dashboard).
3. **`docs/research.md`** — verified AmpleMarket + HubSpot API facts.
4. **`docs/roadmap.md`** — the phased build plan, working agreement, and per-phase checks.
5. **`docs/decisions.md`** — every settled decision and its reasoning.

## How this is built
Claude Code builds one phase at a time; the project owner acts as a non-technical PM. The **working agreement** at the top of `roadmap.md` is binding — build one phase, prove it in plain terms, then move on. Technical detail (schemas, API calls) is figured out per phase, not pre-written.

## Ground rules
- Docs-only until Phase 3; no code/schema before then.
- Never commit secrets (keys, tokens, DB credentials) — use environment variables.
- Log real technical choices in `decisions.md` as you go.
