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

### Table: `identity_crosswalk` (persisted match cache)

| Field | Notes |
|---|---|
| amplemarket_contact_id ↔ hubspot_contact_id ↔ resolved_contact_id | person crosswalk |
| resolved_account_id | company crosswalk |
| original_email / original_domain | stored alongside for audit |

Daily runs are incremental joins against this, not re-resolution.

---

## 3. Source Rules (dedup — load-bearing)

AmpleMarket activity **syncs into HubSpot**, so naive merging double-counts. **Key correction (validated against live data):** AmpleMarket's REST API exposes email **tasks** (to-dos), *not* the emails actually **sent**. The only record of a sent AmpleMarket email is the copy that syncs into HubSpot. So HubSpot is *not* safely discardable for AmpleMarket emails.

**Raw layer (Phase 1) — keep everything, faithfully:**
- Pull AmpleMarket `/tasks` (per `user_id`, `status=completed`) + `/calls`; capture channel, `automatic` flag, timestamp, contact, sequence name.
- Pull HubSpot `emails` + `meetings`; keep **all** origins. An email's origin is tagged by `hs_object_source` / `hs_object_source_detail_1`, and there are **four** values seen live, not three: `INTEGRATION`+`Amplemarket`, `INTEGRATION`+`Apollo Integration`, `EMAIL` (manual Gmail via the HubSpot Sales extension), and **`CRM_UI`** (sent from inside the HubSpot web UI). Any new `object_source` value must be treated as manual until proven otherwise, never dropped.
- Only genuine **warmup** noise is dropped at ingestion (subject markers). *Nothing else is discarded — a raw layer must not throw away data it cannot recreate.*

**Unified model (Phase 3) — de-duplicate on read, once the full picture exists:**
- **Attribute emails by sender, not `owner_id`:** who sent it = `hs_email_from_email` matched to the rep's linked addresses (`/users` map). `hubspot_owner_id` is **unreliable** rep identity — shared across reps and split across many per rep (see §9). Direction follows: sender ∈ rep's addresses ⇒ outbound; else inbound.
- **Same-email cross-tool duplicate — the exact "subject + date + time" match will NOT work; here is why and what does.** One outbound email is logged into HubSpot by *several* tools at once (seen: Apollo + Gmail; Apollo + `CRM_UI`; up to 3 copies — it is **N-way across a varying set of sources**, not a fixed Apollo↔AmpleMarket pair). Two structural traps, both observed live (Andrew Bell 2026-07-14):
  1. **Subjects don't match exactly** — Apollo rewrites the subject with a `[Apollo] [Email] [<<] ` prefix, so its copy reads `[Apollo] [Email] [<<] Re: Encord x TUM` vs the clean `Re: Encord x TUM`. **Strip known tool prefixes before comparing.**
  2. **Timestamps don't match exactly** — the copies land **seconds apart** (10:53:28 vs 10:53:38; 13:10:00 vs 13:10:04), never on the same instant. **Match within a time window (≈±60s), not on equality.**
  Correct dedup key: **normalized-subject + sender + time-window**, and — now that we capture it (§7) — **the message `body` is the strongest join signal** (identical across copies even when subject/time differ). Group all copies of one send into a single activity; keep a note of which tools logged it.
- **AmpleMarket task ↔ send:** reconcile the email *task* with its HubSpot-synced *send* so they aren't counted twice, without dropping the send.
- **Calls — attempts vs conversations; do NOT collapse on `task_id`:** `task_id` is usually null on `/calls`. A rep pursuing one contact logs several records seconds apart, often to different numbers (validated: 4 dials → 1 conversation). Group a conversation by **same contact + tight time window**; a real conversation requires `human = true` (`answered` alone may be voicemail/IVR). Report both dial *attempts* and *connects/conversations*. (A `phone_call` task and its `/calls` record are still one call *when* a `task_id` is present — it just can't be the de-dup key.)
- **Calendar-invite fan-out:** one calendar action is logged once per attendee → collapse to a single activity (subject + time, differing only by recipient).
- **Calendar invite (email object) ↔ meeting (meeting object) are the same real event in two tables — do not count both.** A booked meeting sent via a tool generates **both** an "Invitation:"/"Updated invitation:" row in the `emails` table **and** a row in the `meetings` table. Validated (Andrew Bell 2026-07-14): the "Encord x Bosch | Sync" invite email (12:00 BST) and the Bosch meeting object (11:00 UTC = 12:00 BST) are one event. When tallying "emails + meetings," invite/notification emails must be recognized as belonging to the meeting channel, not counted as separate outbound emails, or every tool-booked meeting inflates both buckets.
- **Sent vs received:** only outbound emails (sender = rep) count as rep *effort*; inbound prospect replies are tracked separately as an engagement/outcome signal.
- **Exclude internal-only recipients:** activity addressed solely to internal `@encord.com` people is not outbound CA activity.
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
- **AmpleMarket `/users`:** **paginated (20/page) — must follow the cursor** (there are ~54 users total, not 20). Do not assume one page. `role` field is **unreliable for identifying CAs** (active reps appear as `admin`; some have duplicate accounts) — see rep-identity note below. Returns each user's `id`, `email`, and a **`mailboxes` array that links that rep's multiple addresses** (e.g. Yuvi `@encord.com` + `@encord.ai`), and lists reps **regardless of task activity** — this is the authoritative source for the internal-ID → person → addresses map (load-bearing for call attribution, below).
- **AmpleMarket `/tasks`:** requires `user_id`; pass `status=completed` to get *done* activity. **No server-side date filter** (date params are ignored) → page newest-first and stop once past the target day. Each task carries `type` (channel), `automatic`, `finished_on`, `sequence_name`, and `contact` (id/name/email).
- **AmpleMarket `/calls`:** global list, also accepts `user_id` filter; no date filter → page newest-first. Carries `duration`, `answered`, `human` (human vs machine/voicemail), `task_id`, `contact`. **A `phone_call` task and its `/calls` record are the same call** (link via `task_id`) — **but `task_id` is frequently null** (it was on all 4 of Joe Turner's 13 Jul calls), so it cannot be the primary de-dup key; group repeated dials by **same contact + tight time window** instead. A call also carries `to`/`from` numbers: a rep may dial one contact across **several different numbers** in seconds (4 dials → 1 conversation, validated). Treat `human = true` as a real conversation (`answered` alone can be voicemail/IVR). **A call record identifies its rep only by internal `user_id` — no name or email** — so attributing a call to a person requires the `/users` map (above), *never* the rep's task history. (A rep with no tasks that day would otherwise have all their calls silently dropped — validated on Yuvi, 13 Jul.) **A call's `contact` can also be null** even for a real conversation (Andrew Bell 2026-07-14: his one `human=true` 104s connect had no contact attached) → some genuine conversations cannot be tied to an account/contact, so Phase 4 coverage must tolerate unattributable-but-real calls rather than dropping them.
- **AmpleMarket exposes email *tasks*, not *sends*.** Automated/sequence email sends never appear as tasks — their only REST-accessible record is the HubSpot-synced copy (§3). No company field → join via contact email domain. Bodies only via webhook (v2). MCP just wraps REST — not used.
- **HubSpot:** calls/meetings/emails are separate objects; Search API filters by `hs_timestamp` (GTE/LT), sort + paginate via `paging.next.after`. **`hs_object_source` / `hs_object_source_detail_1` distinguish the tool — FOUR values seen live:** `INTEGRATION`+`Amplemarket`, `INTEGRATION`+`Apollo Integration`, `EMAIL` (blank detail = manual Gmail via the Sales extension), and **`CRM_UI`** (blank detail = sent by hand from the HubSpot web UI). Treat any unlisted value as manual, never drop it. Emails can be **inbound** — check sender (`hs_email_from_email`) vs the rep's addresses. **Recipients captured (2026-07-15):** `hs_email_to_email`/`hs_email_cc_email` → `to_email`/`cc_email` columns; multiple recipients come **semicolon-separated in one string**, entries vary between `a@b.com` / `Name <a@b.com>` / `<a@b.com>` → parse + normalize before matching, and expect the rep's own address among recipients (self-cc). **Meetings capture `outcome` + `attendee_owner_ids` (2026-07-15):** `attendee_owner_ids` (semicolon-separated internal owner ids) is the usable meeting→rep attribution path — not the single unreliable `owner_id`. `outcome` (COMPLETED/CANCELED/SCHEDULED/no-show) is set only when logged — **null = unknown, not "didn't happen."** External (prospect) attendees are associations, not properties — unavailable via the Search pull. Meetings rows update on re-run (outcome mutates after first capture); the other raw tables are append-only. **`hubspot_owner_id` is NOT reliable rep identity** — it is shared across several reps (owner_id `538916758` carried Kamil, Yianni, George Lim, Nico, Ilaria) and one rep spans many owner_ids (Dillon across 6); **attribute a sent email by its sender address, never by owner_id.** Private-app token auth; Search ~4 req/sec; **10,000-record cap per Search query** (fine per-day; chunk large backfills).
- **Rep identity spans multiple accounts/addresses — in TWO distinct shapes, both must collapse to one person.** (1) *One account, multiple mailboxes* — Katie Mannion is a single `/users` id with a `mailboxes` array of `katie@encord.com` + `katie@encord.ai`. (2) *Multiple separate accounts, one mailbox each* — Yuvi Ajoomal is **two different `/users` ids** (`…7508…` for `@encord.ai`, `…7da9…` for `@encord.com`), each with its own single mailbox. Phase 2 must handle both and, critically, **union ALL of a person's `user_id`s before aggregating their calls/tasks** — querying calls/tasks per-account would split one rep across two "people." (Also: different domains across systems, e.g. Nico `@encord.ai` in AmpleMarket vs `@encord.com` in HubSpot.) Without this, a rep's own emails get misclassified as inbound and their activity is split.
- **Identity (contacts/companies):** person = lowercased email (exact); company = normalized domain; fuzzy name match only as fallback (rapidfuzz ≥90 accept, 80–90 review); guard free-email domains. Cached in `identity_crosswalk`.
- **Titles:** rules-first + LLM fallback + cache (accuracy high, cost near-zero after warm-up).
- **Automated flag:** AmpleMarket `automatic` authoritative; HubSpot proxy only (source-based).
