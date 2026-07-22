# Dashboard blueprint (Phase 7, v1)

Agreed 2026-07-20 (Borja + build agent), from the leadership ask (Slack + call
transcript): clickable by CA, account-level heat map, SAO data alongside
activity, historic + ongoing trends. This file is the reference for what v1
shows, where every number comes from, and what v1 deliberately does not claim.

## The contract (most important thing in this file)

**The dashboard is a window, not a calculator.** Leadership has manually
verified the activity data; the dashboard must never put that at risk:

1. **It only reads.** It connects with a dedicated read-only database login
   (`dashboard_reader`) that can `SELECT` from the approved surfaces below and
   physically cannot write, delete, or alter anything. Enforced by Postgres,
   not by promise.
2. **It recomputes nothing.** Every number on screen is returned by the same
   views/functions that produced the verified Excel audits. If the dashboard
   ever disagrees with those, the dashboard is wrong — fix the dashboard.
3. **It is disposable.** Everything lives in `dashboard/` (+ the additive
   migration 004). Delete both and Phases 0–5 are untouched.
4. **Terms match [ontology.md](ontology.md) verbatim.** The dashboard invents
   no definitions; key measures carry the ontology one-liner as a tooltip.

**Approved read surfaces** (the only things `dashboard_reader` can see):

| Surface | What it is |
|---|---|
| `activity_flat` | The verified activity fact view — one row per real event (incl. bodies, audit fields). |
| `rep_scorecard(start, end)` + `_7d/_30d/_alltime` | Per-rep rollup (Phase 4). |
| `rep_account_drilldown(start, end)` + `_alltime` | Rep → account touchpoints (Phase 5). |
| `account_contact_drilldown(start, end)` + `_alltime` | Account → person (Phase 5). |
| `owned_account_coverage(start, end)` + `_alltime` | Owned-account coverage / neglect (Phase 5). |
| `rep_weekly_trend` / `rep_monthly_scorecard` | NEW (migration 004): the scorecard evaluated per calendar week / month — *wraps* `rep_scorecard()`, so definitions cannot drift. |
| `sao_monthly` | NEW (migration 004): Ray's tracker, one row per rep per month. |
| `dim_ca`, `dim_account`, `dim_contact` | Names/tiers for display. |
| `rep_meeting_breakdown(start, end)` + `_alltime`, `meeting_new_stakeholder_flags`, `meeting_account_map` | NEW (migration 006, Dillon fix #22): each counted meeting bucketed new-stakeholder / follow-up / no-account (buckets sum to booked). Sits BESIDE `meetings_booked`, never replaces it. The raw attendee table stays closed to the reader. |
| `rep_meeting_breakdown_weekly` / `rep_meeting_breakdown_monthly` | NEW (migration 007): the meeting split evaluated per calendar week / month — *wraps* `rep_meeting_breakdown()` exactly like the 004 trend views wrap `rep_scorecard()`, and joins them on (week/month, ca). |

Anything else (raw tables, activity fact table) is invisible to the dashboard
login. Exposing a new object to the dashboard is a deliberate act (an explicit
`GRANT` in a migration), never automatic.

## Screens

Every tab: one-sentence explainer, in-tab time-window pills (7d / 30d / all
time / custom — timeframes never in titles), live "Last update" pill + active
refresh banner (from `ingestion_runs`, migration 005). Dark theme, two accents
(purple = interactive, lime = outcomes); channels colour-coded by family
(greens = email, reds = phone, blues = LinkedIn, purple = meetings,
amber = inbound) across all tables and charts.

1. **Team overview** (landing) — every CA side by side: KPI cards with
   sub-numbers (auto/manual, conversations, meetings split), family-tinted
   scorecard table, channel mix, meetings **always split**
   booked/held/canceled/scheduled/unknown, plus the new-conversations vs
   follow-ups bar ("Mtg new" also in the table — migration 006).
2. **Per CA** (pick a CA) — KPI cards (incl. "New mtgs" with its
   follow-up/unmatched sub-split), dot-first week-by-week trend, account
   breakdown stacked by channel (unmatched activity shown as an explainer
   pill, still counted — totals reconcile). Pick an account → the people
   touched at it.
3. **Account coverage** — CA × account heat map (the team's most-touched
   accounts), owned-account coverage table, neglected top-tier accounts
   grouped per CA (counts in pills, never titles).
4. **Trends** — weekly dot-first lines for any measure, now incl.
   **New-stakeholder / Follow-up meetings** (migration 007); explicit "Whole
   team" entry plus any CAs for comparison. The "are coaching changes
   working" screen; it gets better every week.
5. **SAO vs activity** (monthly) — activity (purple-tinted columns, incl. a
   **New meetings** column) next to Ray's results (lime-tinted), ramping shown
   as a red `*` in its own column, a **New meetings vs SAOs** bar (net-new
   only — follow-ups excluded, since a follow-up shouldn't count toward the
   meeting→SAO ratio) sitting flush beside the **SAO-attainment** bar (its own
   red scale: stronger red = target hit, kept distinct from the purple/lime
   left chart). Pipeline $ is comma-grouped. **Labelled "directional"** (see
   timing rules).
6. **Raw data** — the live read-only view of `activity_flat`: filter, click
   any row → full detail incl. **email body**, duplicates collapsed, and the
   exact exclusion reason for non-counted rows ("Counted in reports" vs
   "Excluded, kept for audit"). Inbound bodies are prospects' words —
   internal use only.

## SAO integration — source & timing rules (agreed 2026-07-20)

- **Source:** Ray's "Global CA Performance Tracker" (SAO Monthly Performance
  tab), loaded from a CSV export by `python3 sao/load_sao.py <csv>` into
  `sao_monthly`. Monthly data ⇒ a monthly manual drop is fine; automate later
  only if it becomes a chore. The loader checksums rep sums against the
  sheet's own Overall/UK/US rows and reports any drift.
- **Join:** rep name (16/17 exact; alias "Constantin Ertel" → "Constantin
  Victor Beat Ertel" lives in the loader).
- **Timing rule 1 — partial July:** our activity starts **2026-07-06**; July
  is a partial month and is labelled as such wherever activity meets SAOs.
  First cleanly comparable month = **August 2026**.
- **Timing rule 2 — lag:** a July SAO usually comes from earlier outreach.
  v1 shows activity and SAOs **side by side as context**, never as
  same-month cause→effect. Lagged comparison (activity month M vs SAOs M+1)
  becomes possible from ~October 2026 (3+ full months).
- **Fairness:** ramping status + start date (from Ray's sheet) shown wherever
  reps are compared.

## Deliberately NOT in v1

- **No correlation coefficients, no "gold ratio" of activity→SAO.** 17 reps ×
  1 partial month = noise dressed as insight. Unlocks ~Oct–Nov 2026; the data
  accrues at the same speed either way, so building now costs nothing.
- **No quality metrics / benchmarks** (Phase 6) — layered on later.
- **No per-user logins yet** — v1 runs locally / behind a simple password;
  proper viewer logins (email allowlist) come with hosted deployment. End
  users get app logins, never database credentials.

## Running it

- `python3 -m streamlit run dashboard/Team_overview.py` (deps: `dashboard/requirements.txt`).
- Data freshness: through *yesterday*, via the 06:17 UTC daily run — the
  dashboard itself fetches live from the views on every load.
- SAO refresh: re-run the loader whenever Ray's sheet changes (monthly).

## Engineer appendix

- `dashboard/` — Streamlit app: `Team_overview.py` (landing) + `pages/` (one file
  per screen), `db.py` (connection via `DASHBOARD_DB_URL`, read-only role),
  `queries.py` (every SQL statement in one place — nothing outside the
  approved surfaces), `requirements.txt`.
- `migrations/004_sao_and_trends.sql` — `sao_monthly` + `rep_weekly_trend` +
  `rep_monthly_scorecard` (+ grants to `dashboard_reader`). Additive;
  drop-to-revert.
- `sao/load_sao.py` — CSV parser/loader (full refresh, single transaction,
  checksum report). Ray's CSV itself is performance data: **never commit it**
  (same rule as `reports/`).
- `dashboard_reader` role: `SELECT` on approved surfaces + `EXECUTE` on the
  four functions only. Created manually (credentials in `.env` /
  deploy secrets, never in git or chat).
