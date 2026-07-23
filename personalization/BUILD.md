# Build spec: Personalized-vs-Templated classifier (post-sign-off)

Everything a build agent needs to implement the production classifier is in
this folder. Do NOT re-derive the rules from prose — port the frozen code.

**Status:** analysis complete (2026-07-23), awaiting Dillon/Ray sign-off on the
open questions in the rulebook. Do not build into the pipeline before the PM
confirms sign-off.

## The frozen rule (parameters locked by the analysis)

1. Normalize each counted outbound email (`analysis/normalize.py` — the exact
   marker lists and order live there): strip quoted chain, signature, legal
   disclaimer; blank merge values (contact first/last name, company, job
   title) + generic greeting-name; blank URLs/emails/digits; lowercase;
   collapse whitespace → "core text".
2. Group emails into template families (`analysis/cluster.py` + `rule.py`):
   exact core-text match ∪ near-dup at **Jaccard ≥ 0.45 on 4-word shingles**
   (MinHash/LSH + union-find; compare to family representatives, not O(n²)).
3. Label: family reached **≥ 3 distinct recipients** (team-wide, full
   history) → every member is **Templated**; else **Personalized**.
4. Reply dimension: `is_reply` (quoted chain from an external sender) and
   `is_selfbump` (quoted chain from our own address) computed per email —
   which populations make the dashboard headline is Dillon's call (see
   rulebook §7 open questions; PM amendment: headline likely
   fresh + self-bumps, genuine prospect replies separate).
5. Nightly full-corpus recompute (labels can legitimately flip
   Personalized→Templated as families grow; ~5% ever flip, 86% same-day).
   Display treatment: mark the most recent ~2 days provisional.
6. **Corpus scope — cluster on everyone, display active-only.** Form template
   families over the FULL corpus incl. departed CAs (9,140 emails: a template
   a departed rep also sent must still count toward family size). But per-CA
   and headline DISPLAY uses active CAs only (`ca_name in dim_ca where
   is_active`), matching the rest of the dashboard — that's why the dashboard
   Emails card shows 8,339 (= 9,140 − Will Sawyer's 801), not 9,140. Same
   retain-in-model / hide-in-view split the scorecard already uses.

## Artifact map

| Path | What | Committed? |
|---|---|---|
| `brief.md` | The analysis-phase briefing (context, definition, method constraints) | yes |
| `analysis/*.py` | The frozen pipeline code — normalize/cluster/rule + all ablation & validation stages | yes |
| `analysis/variation_taxonomy.md` | How template copies vary in our corpus (prevalences) | yes |
| `acceptance/ground_truth_labels.csv` | 215 adjudicated labels (reason column stripped — quotes) | yes |
| `acceptance/holdout_labels.csv` | 74 locked held-out labels + rule predictions | yes |
| `reports/personalization_rulebook_proposal.md` | The leader-facing rulebook + evidence (contains email excerpts) | NO — gitignored, local only |
| `reports/personalization/*.parquet, *.jsonl` | Corpus extracts, normalized texts, per-email labels (email content) | NO — gitignored, local only |

Rule of the split: code and labels are committed; anything containing email
bodies/excerpts stays in gitignored `reports/`. Keep it that way.

## Acceptance tests for the build (non-negotiable)

1. **Exact-reproduction test:** run the ported classifier on the frozen July
   corpus (2026-07-06..07-22, 9,140 counted outbound emails) — its labels
   must match the analysis run **100%** (same deterministic rule, same data ⇒
   identical output; any mismatch = the port is wrong). Reference labels:
   locally in `reports/personalization/labeled_final.parquet`; the committed
   `acceptance/*.csv` cover the 215+74 adjudicated subset if the parquet is
   ever lost.
2. **Ground-truth floor:** ≥96% agreement on `acceptance/ground_truth_labels.csv`,
   ≥97% on `acceptance/holdout_labels.csv`, and ZERO hard fails (no
   ≥3-recipient family member labeled Personalized; no adjudicated-bespoke
   email labeled Templated).
3. **Additivity proof (project ritual):** `activity_flat` byte-identical
   before/after; the new label lives in its own table/view; counted totals
   unchanged everywhere; reconciliation tests extended and green.

## Headline numbers from the analysis (for orientation, not targets)

79% Templated / 21% Personalized (fresh 81/19, replies 75/25); per-CA spread
George Lim 9.5% → Nicolas Fernandez 51.4% personalized; temporal: 5.2% of
labels ever flip, median 0 days to stability, converges to ~20.6%.
