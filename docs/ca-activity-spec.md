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
| channel | enum | `call` \| `meeting` \| `manual_email` \| `auto_email` \| `li_message` \| `li_connect` |
| is_automated | bool | AmpleMarket `automatic` flag (authoritative); HubSpot = proxy, lower confidence |
| is_automated_confidence | enum | `high` (Ample) \| `low` (HubSpot proxy) |
| occurred_at | timestamp | activity date/time |
| email_subject | string (nullable) | for lemwarmup / source filtering |
| **body** | text (nullable) | **v2 field — always null in v1, populated later via webhook. Present in schema now.** |
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

AmpleMarket activity **syncs into HubSpot**, so naive merging double-counts.

- **AmpleMarket = source of truth** for its channels (LI messages, LI connects, sequenced/auto emails, ample-logged calls) — pulled from REST `/calls` and `/tasks` (per `user_id`).
- **HubSpot = source of truth** only for **non-Ample activity**: manual Gmail emails + meetings — pulled from separate `/calls`, `/meetings`, `/emails` objects.
- HubSpot ingestion **excludes** `amplemarket`-sourced records and `lemwarmup`, mirroring the existing HubSpot report.

---

## 4. Derived Fields

- **is_automated** — AmpleMarket `automatic` boolean (high confidence). HubSpot has no clean sequence flag → infer from `hs_object_source`, tag `low` confidence.
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

---

## 7. v2 Path — Message Body (designed-in, not built)

The `body` field exists in the schema from day one but stays null in v1. v2 = **switch on the AmpleMarket Sequence Stage webhook** to capture email/LinkedIn message text for the "is outreach tailored vs. generic" analysis.

- **Easy to add later:** the schema field, REST pull, identity matching, and dashboard all stay unchanged.
- **The only new v2 piece** is an always-on webhook listener — kept out of v1 deliberately (no 24/7 infra before analysis needs it).
- **Caveat:** the webhook only captures messages **from when it's switched on** — no backfill of past bodies. Turn it on the day v2 is committed.

---

## 8. Non-Functional

- **Freshness:** daily refresh (satisfied by batch REST).
- **History:** retain raw activity indefinitely for trend (never overwrite).
- **Single source:** all reporting reads the store, never live APIs.
- **Rate limits to respect:** HubSpot Search API ~4 req/sec (real bottleneck; shared across objects); AmpleMarket 500 req/min default, tighter per-endpoint caps. Schedule pulls accordingly.

---

## 9. Resolved Technical Facts (from research)

- **AmpleMarket:** no single events endpoint. REST `/tasks` (needs `user_id` per call) + `/calls` give channel + `automatic` flag; **no company field** → join via contact email domain. MCP just wraps REST — not used. Bodies only via webhook (v2).
- **HubSpot:** calls/meetings/emails are separate objects; associate to contact + company via associations; filter by `hs_timestamp`, exclude by `hs_email_subject` / `hs_object_source`. Private-app token auth. Search API ~4 req/sec is the constraint.
- **Identity:** person = lowercased email (exact); company = normalized domain; fuzzy name match only as fallback (rapidfuzz ≥90 accept, 80–90 review); guard free-email domains. Cached in `identity_crosswalk`.
- **Titles:** rules-first + LLM fallback + cache (accuracy high, cost near-zero after warm-up).
- **Automated flag:** AmpleMarket authoritative; HubSpot proxy only.
