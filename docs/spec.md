# CA Activity Visibility — Technical Product Spec (End State)

**Status:** v1 spec, updated post API-research. Body-ready, batch-only runtime (Option A).
**Purpose:** Single source of truth for how much and how well each CA works their target accounts, by merging AmpleMarket and HubSpot activity into one unified, queryable model.

---

## 1. System Overview

```
[HubSpot REST]          [AmpleMarket REST]
        \                      /
        Ingestion (daily batch)
                   |
   Unified store (Postgres / Supabase)  ── normalized tables (source of truth)
                   |
        Flat analytics view (materialized join of all tables)
                   |
        Dashboard (filter + drill-down: rep → account → contact)
```

- **Ingestion:** scheduled daily REST pulls from both sources, normalized into one schema.
- **Store:** normalized relational tables (clean dependencies) = source of truth. A single **flat view** is materialized on top for all analytics — store tidy, read flat.
- **Dashboard:** read-only UI on the flat view. Filter by rep / tier / vertical / time; drill rep → account → contact.
- **Runtime is batch-only.** No always-on component in v1. (See §7 for the v2 body path.)

---

## 2. Data Model

### Grain
**One row = one activity event.** Everything aggregates up from this. No pre-aggregated writes; rollups computed on read.

### Fact table: `activity`

| Field | Type | Notes |
|---|---|---|
| activity_id | string (PK) | source-unique id |
| source | enum | `hubspot` \| `amplemarket` |
| rep_id | FK → rep | who performed it |
| account_id | FK → account | resolved via contact domain (Ample has no company field) |
| contact_id | FK → contact | resolved person |
| channel | enum | normalized: `call` \| `meeting` \| `manual_email` \| `auto_email` \| `li_message` \| `li_connect` \| `li_other` \| `whatsapp` \| `sms`. Raw layer stores the native AmpleMarket type verbatim (`email`, `phone_call`, `linkedin_visit`/`follow`/`like_last_post`/`message`/`voice_message`/`video_message`/`connect`, `whatsapp`, `sms`, `custom_task`); Phase 3 maps to this enum |
| is_automated | bool | AmpleMarket `automatic` flag (authoritative); HubSpot = proxy, lower confidence |
| is_automated_confidence | enum | `high` (Ample) \| `low` (HubSpot proxy) |
| occurred_at | timestamp | activity date/time |
| email_subject | string (nullable) | for lemwarmup / source filtering |
| **body** | text (nullable) | **HubSpot email bodies ARE captured from Phase 1 (via REST — see §7), going-forward only. AmpleMarket LinkedIn/email message text still needs the v2 webhook. Field present in schema from day one; partially populated in v1.** |
| ingested_at | timestamp | load audit |

**As built (Phase 3, 2026-07-16 — `model/build_activity.py`, schema in `migrations/001_activity_model.sql`):** the `activity` table implements this design plus the audit columns the transparency requirement (§6) needs: `direction` (outbound/inbound by sender), `counts` + `excluded_reason` (non-countable rows are KEPT and labeled, never dropped), `dup_count` + `source_ids` jsonb + `logged_by` (every raw row that merged into the event — the invariant is that each raw activity row appears in exactly one activity row's `source_ids`), `ca_ids` (meetings: all attending CAs), `call_group_id` + `is_conversation` (calls: attempts vs conversations), `subject_norm`, `outcome`. `account`/`contact`/`rep` dimensions are zero-copy **views** (`dim_account`, `dim_contact`; rep = Phase 2's `dim_ca` table), and the flat analytics view is **`activity_flat`**. Full rebuild each run, single commit, deterministic (byte-identical re-runs verified) — same contract as the identity layer. Channels as built: `manual_email`/`auto_email`/`inbound_email`, `meeting`, `call`, `li_message`/`li_connect`/`li_other`, `whatsapp`, `sms`, plus non-counted shadows `email_task`/`call_task` (§3) and `other` for unknown task types.

### Dimension: `account`

| Field | Source field | Notes |
|---|---|---|
| account_id | — | PK |
| name | `name` | |
| domain | derived | normalized web/email domain — primary match key |
| icp_tier_validated | `account_icp_tier_validated` | quality lens |
| icp_tier_new | `account_icp__tier_new` | quality lens |
| vertical | `vertical__aligned_by_team` | coverage lens |
| owner_rep_id | FK → rep | for coverage |
| is_target | bool | in rep's assigned territory |

### Dimension: `contact`

| Field | Source field | Notes |
|---|---|---|
| contact_id | — | PK |
| account_id | FK → account | |
| email | — | primary person match key |
| jobtitle | `jobtitle` | raw free-text — never overwritten |
| seniority_bucket | derived | normalized (see §4) |

### Dimension: `rep`

| Field | Notes |
|---|---|
| rep_id | PK |
| name | |
| target_account_count | denominator for coverage % |

**CA roster source (built 2026-07-15):** who counts as a rep/CA is **not** hard-coded — it's derived from `raw_hubspot_owners` (full mirror of HubSpot owners + their `teams`) filtered by `config_ca_teams` (the policy: 8 CA teams, each minus its parent Sales pod). `config_ca_teams` is seeded from the version-controlled `config/ca_teams.json`; queries read the table, never the file. Phase 2 materializes the derived roster (17 CAs, PM-confirmed) into `dim_ca` and links each CA's sending addresses across `encord.com` / `encord.ai` / `tryencord.com`. Full rationale + the derivation query in `decisions.md`.

### Table: `rep_metrics` (AmpleMarket funnel/outcome summaries)

Outcome metrics AmpleMarket computes as aggregates — not individual events, so they live here, not in `activity`. One row per rep per period.

| Field | Notes |
|---|---|
| rep_id | FK → rep |
| period_start / period_end | window |
| new_leads, leads_interested, leads_interest_rate | funnel |
| meetings_booked, meetings_booked_rate, interested_not_booked | outcomes |
| email_open_rate, email_reply_rate | engagement |
| completed_tasks, overdue_tasks | workload / discipline |
| source | which system populated (blank where unavailable) |

Full target column set; **each source fills what it exposes, blanks expected** (Ample fills most; HubSpot fills raw activity + meetings only).

### Identity tables (Phase 2 — built as `identity/resolve.py`)

Phase 2 **full-rebuilds** the identity layer on every run, deterministically, from the raw tables alone: `dim_ca` (the 17-CA roster), `dim_ca_address` (each CA's sending addresses across the three domains), `ca_amplemarket_user` (CA ↔ AmpleMarket accounts, many-to-one), `company_crosswalk`, `contact_crosswalk`, `amplemarket_contact_map`, and `identity_unresolved` (the human-review list). **No incremental cache** — deterministic rebuild *is* the design: safe to re-run any time, and guards refuse to shrink the roster silently. *(Superseded the cached/incremental `identity_crosswalk` plan — see the Phase 2 entry in `decisions.md`.)*

---

## 3. Source Rules (dedup — load-bearing)

AmpleMarket activity **syncs into HubSpot**, so naive merging double-counts. **Key correction (validated against live data):** AmpleMarket's REST API exposes email **tasks** (to-dos), *not* the emails actually **sent**. The only record of a sent AmpleMarket email is the copy that syncs into HubSpot. So HubSpot is *not* safely discardable for AmpleMarket emails.

**Raw layer (Phase 1) — keep everything, faithfully:**
- Pull AmpleMarket `/tasks` (per `user_id`, `status=completed`) + `/calls`; capture channel, `automatic` flag, timestamp, contact, sequence name.
- Pull HubSpot `emails` + `meetings`; keep **all** origins. An email's origin is tagged by `hs_object_source` / `hs_object_source_detail_1`, and there are **four** values seen live, not three: `INTEGRATION`+`Amplemarket`, `INTEGRATION`+`Apollo Integration`, `EMAIL` (manual Gmail via the HubSpot Sales extension), and **`CRM_UI`** (sent from inside the HubSpot web UI). Any new `object_source` value must be treated as manual until proven otherwise, never dropped.
- Only genuine **warmup** noise is dropped at ingestion (subject markers). *Nothing else is discarded — a raw layer must not throw away data it cannot recreate.*

**Unified model (Phase 3) — de-duplicate on read, once the full picture exists:**
- **Attribute emails by sender, not `owner_id`:** who sent it = `hs_email_from_email` matched to the rep's linked addresses (`/users` map). `hubspot_owner_id` is **unreliable** rep identity — shared across reps and split across many per rep (see §9). Direction follows: sender ∈ rep's addresses ⇒ outbound; else inbound.
- **Same-email cross-tool duplicate — corrected key (2026-07-15 audit; supersedes both the "subject + date + time" idea and the ±60s normalized-subject key).** One outbound email is logged into HubSpot by *several* tools at once (seen: Apollo + Gmail; Apollo + `CRM_UI`; AmpleMarket + Gmail; up to 3 copies — **N-way across a varying set of sources**, not a fixed pair). **Dedup key: same sender + non-empty overlap of extracted external recipients + whitespace-normalized body prefix (~150 chars) + timestamps within ±180s. Subject — tool- AND reply-prefix stripped — is a tiebreak only, never primary.** Validated against all 139 live cross-tool duplicate groups (2026-07-15): merges all, loses none. Why each component, all measured live 2026-07-15:
  1. **Recipient overlap is mandatory.** Sequence blasts send the same subject+body to N prospects within seconds/minutes — without a recipient component, 40 CA-sender clusters falsely merge 170 distinct sends = **7.8% of ALL CA outbound in 5 days** (worst: Will Sawyer 94/261 = 36%; "Internal resource allocation" went to 49 distinct recipients in a 34-min window). Body does NOT rescue it — templated bodies are **byte-identical across recipients** (59 distinct sends measured). **Recipient-less collapse (subject+time) is FORBIDDEN for general sends** — it applies ONLY to rows classified as calendar-invite/notification (below). Copies of ONE send also carry *different* recipient lists (Apollo's copy drops internal/self recipients; formats vary between `a@b.com` and `Name <a@b.com>`) — so compare the **extracted external addresses** (parsed + normalized per §9) for *non-empty overlap*, never set-equality on raw strings.
  2. **Subject mutates across copies — strip tool AND reply prefixes, then only tiebreak.** Apollo prepends `[Apollo] [Email] [<<] `; AmpleMarket's HubSpot-synced copy ADDS `Re: ` to the very same send (verified pair: "Real-time video effects" `EMAIL` 23:00:00 vs "Re: Real-time video effects" `Amplemarket` 23:00:08 — same recipient, identical body). 9/139 true-duplicate groups (6.5%) mutate the subject beyond the Apollo prefix → strip reply prefixes too (`Re:`/`RE:`/`Aw:`/`Fwd:`/localized variants), and never make subject primary.
  3. **Copies land seconds-to-minutes apart, never on the same instant** — 4/139 groups (2.9%) span >60s (max 160s measured) → the window is **±180s**, not ±60s. And raw `body_preview` drifts across copies (whitespace/nbsp) in 23% of groups → **normalize whitespace before comparing bodies**.
  Group all copies of one send into a single activity; keep a note of which tools logged it.
  **Two body-normalization refinements found on the first live build (2026-07-16, both tested on the exact live strings):** (a) mail clients inject **banners** into one tool's copy but not the other's (Gmail's "This is the first time you're receiving an email from this person…" made a prospect reply's two copies look like different emails — live Lucid Bots pair) → strip known banners before taking the body prefix; (b) one copy's preview can carry a **trailing signature block** the other's stops before ("try now." vs "try now. -- Kind regards …") → two normalized bodies also match when one is a prefix of the other (min 20 chars, so a short "Thanks!" can't falsely match a longer real reply). And one inbound nuance: **for INBOUND rows the recipient-overlap component is computed over ALL recipients** (a prospect reply's recipients are our own reps — internal-only — so the external-recipients rule would never find overlap); outbound keeps external-only (a rep self-cc'd on every blast send must not create overlap).
- **AmpleMarket task ↔ send:** reconcile the email *task* with its HubSpot-synced *send* so they aren't counted twice, without dropping the send.
- **Calls — attempts vs conversations; do NOT collapse on `task_id`:** `task_id` is usually null on `/calls`. A rep pursuing one contact logs several records seconds apart, often to different numbers (validated: 4 dials → 1 conversation). Group a conversation by **same contact + tight time window**; a real conversation requires `human = true` (`answered` alone may be voicemail/IVR). Report both dial *attempts* and *connects/conversations*. (A `phone_call` task and its `/calls` record are still one call *when* a `task_id` is present — it just can't be the de-dup key.) **~25% of `human = true` conversations carry a NULL `contact`** (measured 2026-07-15) — real conversations that cannot be attributed to a person; Phase 4+ must surface them honestly (an unattributed bucket), never drop them.
- **Calendar-invite rows dedupe by the SAME corrected key above; per-attendee fan-out is rare (measured 2026-07-15).** The old shape ("one calendar action logged once per attendee") is mostly wrong: 49 of 71 invite rows pack ALL attendees into one `to_email`; only **1** true per-attendee fan-out group appeared in 5 days. The real invite inflation is **cross-tool copies** (131 Apollo-prefixed invite copies) — handled by the corrected key. Residual per-attendee fan-out collapses via **invite-classified subject+time** — the ONLY rows where recipient-less collapse is permitted (see the key rule above).
- **Calendar invite (email object) ↔ meeting (meeting object): a CLASSIFICATION rule, not a join.** Emails whose subject matches invite patterns (`Invitation:`, `Updated invitation:`, `Accepted:`, `Declined:`, …) belong to the **meeting channel** and are **excluded from email counts** — otherwise every tool-booked meeting inflates both buckets (validated Andrew Bell 2026-07-14: the "Encord x Bosch | Sync" invite email and the Bosch meeting object are one event). Do **NOT** attempt row-level invite↔meeting joins — infeasible, measured on 164 meetings (2026-07-15): 11 clean matches, 7 ambiguous, 144 none.
- **Duplicate meeting OBJECTS exist — dedupe meetings too (found 2026-07-15):** the same meeting can appear under different ids with identical title + start_time (3 verified cases in 5 days ≈ 2% inflation: "Boston Dynamics – Encord | Weekly", "Encord & TFH | Weekly", "Global Sales Enablement Bi-Weekly"). Meetings dedup key: **(title, start_time, owner)**.
- **Sent vs received:** only outbound emails (sender = rep) count as rep *effort*; inbound prospect replies are tracked separately as an engagement/outcome signal. **Inbound needs the same dedup + noise filtering (measured 2026-07-15):** 31 prospect replies were logged twice (Apollo + Gmail copies) in 5 days — apply the same corrected key to inbound; and filter **bounces/auto-replies/notification senders** out of engagement metrics (287 automated-sender rows — mailer-daemon, no-reply, notifications@, gong — plus 22 auto-replies/OOO incl. localized "Respuesta automática:", "Automatische Antwort:"), or reply/engagement metrics inflate ~2×.
- **Exclude internal-only recipients — computed over to ∪ cc ∪ bcc:** activity whose *combined* recipient set contains no external address is not outbound CA activity. `to` alone is NOT sufficient — 3 live CA outbound emails have ONLY internal addresses in `to` with the prospect on cc (verified 2026-07-15). `bcc_email` is captured from 2026-07-15 (column added; going-forward only).
- **Apollo (open decision):** Apollo is a *second* outreach tool present in HubSpot but not in the original plan. Decide whether Apollo activity counts as CA activity before finalizing counts.

---

## 4. Derived Fields

- **is_automated** — AmpleMarket `automatic` boolean (high confidence). HubSpot has no clean sequence flag → infer from `hs_object_source` / `hs_object_source_detail_1`, tagged `low` confidence: tool-synced `Amplemarket` / `Apollo Integration` lean automated; manual Gmail `EMAIL` and `CRM_UI` (sent by hand from the HubSpot UI) lean manual. Note `Apollo Integration` is itself a *sequencer*, so Apollo-sourced ≠ manual. Any unrecognized `object_source` defaults to manual/`low`.
- **seniority_bucket** — **rules-first, LLM fallback, cached.** Dictionary/regex handles the common 80–90% (CxO→C-level, VP/SVP/EVP→VP, Director/Head-of→Director, Manager→Manager, else IC). Only unmatched titles hit an LLM. **Every (raw title → bucket) mapping is cached — classified once, ever.** Raw title preserved separately.
- **account coverage** — accounts touched vs. `target_account_count`.

---

## 5. Metrics & Views (end state)

| View | Grain | Key measures |
|---|---|---|
| Rep aggregate | per rep | totals by channel; accounts touched vs. owned; contacts; contacts/account |
| Rep → Account | per rep × account | touchpoints, distinct contacts, channel mix, ICP tier, vertical |
| Account → Contact | per account × contact | touchpoints by channel, jobtitle, seniority |
| Quality lens | per rep | % automated vs. manual; activity by ICP tier; activity by seniority |
| Outcomes | per rep | funnel + engagement from `rep_metrics` |

Time windows: **7 days, 30 days, custom**, plus longer-range trend.

---

## 6. Dashboard Requirements

- Filters: rep, ICP tier, vertical, time window.
- Drill: **rep → account → contact**; quality lens at rep + account level.
- Read-only, refreshed daily, exportable so others (e.g. Falkner) can pull independently.
- **Full data transparency / auditability (requirement).** Every aggregate number must be traceable down to the individual raw activity records behind it — a user can click any count and see the underlying rows (with source tool, timestamp, subject/body, contact) to manually verify it and catch mistakes. No number should be a black box. This is what lets us trust the dedup/attribution logic in production: because the rules above are non-trivial (normalized-subject dedup, sender attribution, attempts-vs-conversations), the dashboard must expose the raw evidence for any figure so errors are visible, not hidden inside a rollup.

---

## 7. Message Body — partly in v1, rest in v2

**Update (2026-07-15): HubSpot email bodies are now captured in v1** via the daily REST pull — HubSpot's `hs_email_html` (full) + `hs_body_preview` (snippet) come back on the existing Search API with no new scope, stored in `raw_hubspot_emails.body_html` / `body_preview`. This was cheap and unlocks two things immediately: (a) the strongest dedup signal for same-send duplicates (§3), and (b) the raw material for the tailored-vs-generic quality lens. **Going-forward only — no backfill** (older rows stay null); HubSpot retains bodies, so a backfill is possible later if ever needed.

**Still v2 (needs the webhook):** AmpleMarket LinkedIn/email *message text* — REST exposes email *tasks*, not send bodies (§9), so the AmpleMarket Sequence Stage webhook is still required for outreach text originating in AmpleMarket.

- **The only new v2 piece** is an always-on webhook listener — kept out of v1 deliberately (no 24/7 infra before analysis needs it).
- **Caveat:** the webhook only captures messages **from when it's switched on** — no backfill. Same going-forward-only caveat as the HubSpot body capture above.

---

## 8. Non-Functional

- **Freshness:** daily refresh (satisfied by batch REST).
- **History:** retain raw activity indefinitely for trend (never overwrite).
- **Single source:** all reporting reads the store, never live APIs.
- **Rate limits to respect:** HubSpot Search API ~4 req/sec (real bottleneck; shared across objects); AmpleMarket 500 req/min default, tighter per-endpoint caps. Schedule pulls accordingly.

---

## 9. Resolved Technical Facts (verified against live APIs, Phase 1)

- **AmpleMarket auth/base:** `https://api.amplemarket.com`, `Authorization: Bearer <key>`. Cursor pagination via `_links.next`; follow it — page sizes are capped (~20) regardless of `page[size]`.
- **AmpleMarket `/users`:** **paginated (20/page) — must follow the cursor** (56 users live 2026-07-15, not 20). Do not assume one page. `role` field is **unreliable for identifying CAs** (active reps appear as `admin`; some have duplicate accounts) — see rep-identity note below. Returns each user's `id`, `email`, and a **`mailboxes` array that links that rep's multiple addresses** (e.g. Yuvi `@encord.com` + `@encord.ai`), and lists reps **regardless of task activity** — this is the authoritative source for the internal-ID → person → addresses map (load-bearing for call attribution, below).
- **AmpleMarket `/tasks`:** requires `user_id`; pass `status=completed` to get *done* activity. **No server-side date filter** (date params are ignored) → page newest-first and stop once past the target day. Each task carries `type` (channel), `automatic`, `finished_on`, `sequence_name`, and `contact` (id/name/email).
- **AmpleMarket `/calls`:** global list, also accepts `user_id` filter; no date filter → page newest-first. Carries `duration`, `answered`, `human` (human vs machine/voicemail), `task_id`, `contact`. **A `phone_call` task and its `/calls` record are the same call** (link via `task_id`) — **but `task_id` is frequently null** (it was on all 4 of Joe Turner's 13 Jul calls), so it cannot be the primary de-dup key; group repeated dials by **same contact + tight time window** instead. A call also carries `to`/`from` numbers: a rep may dial one contact across **several different numbers** in seconds (4 dials → 1 conversation, validated). Treat `human = true` as a real conversation (`answered` alone can be voicemail/IVR). **A call record identifies its rep only by internal `user_id` — no name or email** — so attributing a call to a person requires the `/users` map (above), *never* the rep's task history. (A rep with no tasks that day would otherwise have all their calls silently dropped — validated on Yuvi, 13 Jul.) **A call's `contact` can also be null** even for a real conversation (Andrew Bell 2026-07-14: his one `human=true` 104s connect had no contact attached) → **~25% of `human=true` conversations** (measured 2026-07-15) cannot be tied to an account/contact, so Phase 4 coverage must tolerate unattributable-but-real calls rather than dropping them.
- **AmpleMarket exposes email *tasks*, not *sends*.** Automated/sequence email sends never appear as tasks — their only REST-accessible record is the HubSpot-synced copy (§3). No company field → join via contact email domain. Bodies only via webhook (v2). MCP just wraps REST — not used.
- **HubSpot:** calls/meetings/emails are separate objects; Search API filters by `hs_timestamp` (GTE/LT), sort + paginate via `paging.next.after`. **`hs_object_source` / `hs_object_source_detail_1` distinguish the tool — FOUR values seen live:** `INTEGRATION`+`Amplemarket`, `INTEGRATION`+`Apollo Integration`, `EMAIL` (blank detail = manual Gmail via the Sales extension), and **`CRM_UI`** (blank detail = sent by hand from the HubSpot web UI). Treat any unlisted value as manual, never drop it. Emails can be **inbound** — check sender (`hs_email_from_email`) vs the rep's addresses. **Recipients captured (2026-07-15):** `hs_email_to_email`/`hs_email_cc_email` → `to_email`/`cc_email` columns; multiple recipients come **semicolon-separated in one string**, entries vary between `a@b.com` / `Name <a@b.com>` / `<a@b.com>` → parse + normalize before matching, and expect the rep's own address among recipients (self-cc). **Meetings capture `outcome` + `attendee_owner_ids` (2026-07-15):** `attendee_owner_ids` (semicolon-separated internal owner ids) is the usable meeting→rep attribution path — not the single unreliable `owner_id`. `outcome` (COMPLETED/CANCELED/SCHEDULED/RESCHEDULED/no-show) is set only when logged — **null = unknown, not "didn't happen."** External (prospect) attendees are associations, not properties — unavailable via the Search pull. Meetings rows update on re-run (outcome mutates after first capture); the other raw tables are append-only. **`hubspot_owner_id` is NOT reliable rep identity** — it is shared across several reps (owner_id `538916758` carried Kamil, Yianni, George Lim, Nico, Ilaria) and one rep spans many owner_ids (Dillon across 6); **attribute a sent email by its sender address, never by owner_id.** Private-app token auth; Search ~4 req/sec; **10,000-record cap per Search query** (fine per-day; chunk large backfills).
- **HubSpot Company/Contact objects (Phase 1.5, verified live 2026-07-15):** ~154k companies, ~446k contacts. These are **dimension** data (update-on-conflict, no activity_date). **Companies:** full mirror, incremental on last-modified (sort ASCENDING, restart from the last-seen watermark to beat the 10k Search cap; GTE overlap deduped by the upsert). **Contacts:** activity-scoped mirror — only emails seen in real activity, fetched via **`batch/read` with `idProperty: email`** (100 inputs/call; unknown emails silently absent from results; two alias emails can resolve to ONE contact id → dedupe before upsert; 99% of activity emails matched on first run). Property facts: companies' last-modified is `hs_lastmodifieddate` but contacts' is **`lastmodifieddate`**; `target_account_owner` holds an **owner id** (same id-space as `hubspot_owner_id` / meeting `attendee_owner_ids`) and only **~1.5k companies have one** (1,549 live 2026-07-15; live-checkable: companies with non-empty `target_account_owner`) — the assigned-target universe; contact→company association is the plain contact property `associatedcompanyid` (no associations call needed for the primary company). Useful company fields confirmed: `domain`, `account_icp_tier_validated`, `account_icp__tier_new` (automated tiering), `vertical__aligned_by_team`, `target_account_tier`, `target_account_segment`; several similarly-named ICP fields are marked deprecated — use these, not those.
- **Rep sending domains: `encord.com`, `encord.ai`, and the dedicated outreach domain `tryencord.com`** (found 2026-07-15: reps annabel/george/tom.kennedy send cold outreach from `@tryencord.com`; unknown to the attribution logic it made their outbound look inbound — Ray's reported direction anomaly). Phase 2 rep identity must include all three domains, and these outreach addresses likely do NOT appear in AmpleMarket `/users` mailboxes — the `/users` map alone is insufficient. `INTERNAL_DOMAINS` in `ingest.py` is the canonical list; watch for new domains.
- **Rep identity spans multiple accounts/addresses — in TWO distinct shapes, both must collapse to one person.** (1) *One account, multiple mailboxes* — Katie Mannion is a single `/users` id with a `mailboxes` array of `katie@encord.com` + `katie@encord.ai`. (2) *Multiple separate accounts, one mailbox each* — Yuvi Ajoomal is **two different `/users` ids** (`…7508…` for `@encord.ai`, `…7da9…` for `@encord.com`), each with its own single mailbox. Phase 2 must handle both and, critically, **union ALL of a person's `user_id`s before aggregating their calls/tasks** — querying calls/tasks per-account would split one rep across two "people." (Also: different domains across systems, e.g. Nico `@encord.ai` in AmpleMarket vs `@encord.com` in HubSpot.) Without this, a rep's own emails get misclassified as inbound and their activity is split.
- **Identity (contacts/companies) — as built in Phase 2:** person = lowercased email (exact). Company merge requires **domain match AND name-token agreement — never domain alone** (live junk domains proved it: unrelated companies carrying `google.com`); free-email domains guarded. **No fuzzy matching implemented** — the rapidfuzz (≥90 accept, 80–90 review) plan was superseded 2026-07-15; non-matches and no-domain TARGET companies go to `identity_unresolved` for human review. Tables are full-rebuilt each run (§2) — no cached crosswalk.
- **Titles:** rules-first + LLM fallback + cache (accuracy high, cost near-zero after warm-up).
- **Automated flag:** AmpleMarket `automatic` authoritative; HubSpot proxy only (source-based).
