# CA Activity Visibility — Roadmap

**Start here if you're building.** Read `context.md` (why this exists) and `spec.md` (the end-state design, including verified AmpleMarket + HubSpot API facts in §9) before starting. `decisions.md` records every settled choice — don't reverse one without adding a superseding entry.

**8 phases (0–7).** Each is self-contained, ships something verifiable, and depends only on the phases before it. Phases 0–3 build the data foundation, Phase 4 delivers the first output leaders can use, and 5–7 add depth, quality, and the dashboard.

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

## Phase 1 — Daily ingestion (raw)

**Plain terms:** Start automatically copying every rep's raw activity — calls, LinkedIn actions, emails, meetings — out of the two tools into our database, once a day. Just a faithful copy at this stage, not yet cleaned or merged.

**Work:** AmpleMarket daily pull of calls + tasks (per user), capturing channel, timestamp, automatic flag, contact; HubSpot daily pull of emails + meetings only, excluding `lemwarmup` and `amplemarket`-sourced records; land both in raw staging; re-runs must not duplicate; respect rate limits (HubSpot Search ~4 req/sec is the constraint).

**Success metric:** a daily run lands a full day of activity from both sources; re-running the same day creates no duplicates.

**PM check:** Claude shows you a count of activities pulled per rep for one day, and confirms running it twice didn't double the numbers.

**Depends on:** Phase 0.

---

## Phase 2 — Identity resolution + account de-duplication

**Plain terms:** Make sure "the same person" and "the same company" are recognised as one, even across two systems and despite HubSpot sometimes storing a company twice. Without this, an account's activity could split across duplicates and coverage numbers would be wrong.

**Work:** match people by exact lowercased email (fuzzy name fallback); match companies by domain (fuzzy name fallback, guard free-email domains); collapse HubSpot duplicate companies by domain (mandatory); persist an identity crosswalk for incremental daily joins.

**Success metric:** each contact and company resolves to a single ID; HubSpot company duplicates collapsed; a match rate and an unresolved-records list are produced.

**PM check:** Claude shows you the match rate and a short list of anything it couldn't confidently match, for your eyes.

**Depends on:** Phase 1.

---

## Phase 3 — Unified activity model + flat view

**Plain terms:** Turn the raw, mismatched copies into one clean table where every row is a single thing a rep did — tagged with who, which company, which channel. The tidy foundation everything else reads from.

**Work:** normalize staging into the activity fact table + account/contact/rep dimensions (per spec); attach each activity to its resolved account + contact; materialize the single flat analytics view; keep the `body` column present but null (v2-ready). Connect Supabase↔GitHub for schema migrations here.

**De-duplication & direction rules (confirmed during Phase 1 validation — the raw layer keeps everything; these are applied here on read):**
- **Sent vs received email split.** Some HubSpot emails are inbound (sent *by the prospect*). Only outbound (sender = the rep) counts as rep *effort*; inbound replies are tracked separately as an *engagement/outcome* signal. (Validated on Yianni: 3 sent vs 2 received.)
- **Same-email cross-tool duplicates.** The same outbound email can be logged into HubSpot by *both* Apollo and AmpleMarket. Treat as one email when **subject + date + time all match** (sender used as an extra safeguard). (Validated on James Falconer: "Re: VLMs in identity", same date/time, logged by both.)
- **AmpleMarket task ↔ send reconciliation.** AmpleMarket exposes email *tasks* but not *sends*; sends appear only as the HubSpot-synced copy. Reconcile so a task and its resulting send aren't counted twice, without dropping sends. (See decisions.md.)
- **Call ↔ task overlap.** A `phone_call` task and its `/calls` record are the same call; collapse via `task_id`.

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
