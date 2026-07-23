# Brief: Personalized-vs-Templated email classification (analysis phase)

You are an analysis agent with a fresh context. This document is your full
briefing. Read it completely before touching anything. Your job is ANALYSIS
ONLY — you will produce a proposed deterministic rulebook and evidence, not
build anything into the pipeline or dashboard.

---

## How to read this brief

**Fixed** (do not deviate): the GOAL (§2), the DEFINITION of templated vs
personalized (§4.2), the hard constraints (§7), and the deliverable (§6.8).

**Just leads** (verify, improve, or discard): every normalization step,
similarity measure, threshold, technique, and number named below comes from a
~20-minute mini-analysis. It is NOT a specification. **You own the method.**
If your own analysis finds a cleaner representation or a better rule, use it
and explain why. The specifics are here to save you time and flag known traps —
not to box you in. When two paths conflict, optimize for reproducing the
template-family ground truth (§6.4), not for matching what this doc suggested.

---

## 1. Project context (30 seconds)

Repo: `/Users/borja/builds/CA_central` — a data pipeline + Streamlit dashboard
giving sales leadership visibility into Customer Associate (CA) outbound
activity. Sources (HubSpot, Amplemarket) → raw tables → identity resolution →
`activity` fact table → **`activity_flat`** (THE view everything reads) →
dashboard. Counting rules are leadership-verified; `activity_flat` is never to
be modified by you.

The PM (Borja, non-technical) works with sales leaders Dillon and Ray. Every
metric definition shown to them must be simple, deterministic, and honest.

Read `docs/ontology.md` and `docs/decisions.md` (skim) for the metric culture:
probe first, numbers before promises, label-never-blend, caveats said out loud.

## 2. The problem

The dashboard splits emails into "auto" vs "manual" based on HubSpot sync
metadata. A pipeline audit (2026-07-23) proved this label is wrong in BOTH
directions:

- Sequence tools send through reps' connected mailboxes → HubSpot logs a
  normal mailbox send → labeled "manual". Measured: **15% of "manual" emails
  are byte-identical text sent to 5+ people** (automation wearing a manual label).
- Reps manually execute sequence steps through Amplemarket → the send syncs
  with tool metadata → labeled "auto". Measured: **~26% of "auto" emails have
  genuinely unique, hand-written text.**

Leadership doesn't actually care who pressed send. They care: **was this email
written for that one prospect (personalized) or was it a blast (templated)?**
That is answerable from the email BODY TEXT, which we store in full
(`activity_flat.body_html`, 100% coverage on all counted outbound emails).

The old auto/manual split will be KEPT (renamed "send method"); the new
Personalized/Templated split will sit BESIDE it. You are designing the new one.

## 3. What a 20-minute mini-analysis suggested (LEADS — verify, improve, or discard)

Hints from a quick look at the ~9,140 counted outbound emails
(window 2026-07-06..07-22). NOT a method to follow — take what's useful,
prove or drop the rest, design against what YOUR analysis finds.

- **The hard part is the variation, not the matching.** Templates repeat, but
  names/company/placeholders — sometimes a whole dynamic first line — change
  per send. "The text differs → it's unique" is the trap to beat.
- **Normalizing before comparing looked like the biggest lever** — cleaning
  the text first (stripping quoted reply chains — ~44% of emails carry one and
  it wrecks naive matching — signatures, the legal disclaimer, and neutralizing
  merge fields) mattered more than any threshold. A lead, not a recipe.
- **A crude rule got surprisingly far:** group by near-identical normalized
  text, call it "templated" once a text hits ~3+ recipients → ~75% templated /
  25% personalized, matching a hand-read of 24 emails. Treat 75/25 as a rough
  prior, not a target.
- **Species you'll likely meet** (so your taxonomy isn't caught off guard):
  pure blasts; merge-field templates; templates with a dynamic/AI first line;
  generic bumps ("just bumping this"); logistics/scheduling replies (unique
  text, not personalized outreach); genuinely bespoke outreach; event invites.

## 4. Decisions the PM has ALREADY made (do not relitigate)

1. **Replies vs fresh outreach is a first-class dimension.** Detect it
   deterministically (quoted chain present / subject starts "Re:" /
   thread position). Classify and report the two populations separately;
   whether the headline % includes replies is Dillon's call — present both.
2. **The axis is REUSE, not quality and not authorship.** THE definition:
   - **Templated** = the underlying message was **reused across multiple
     recipients**, even though names, company, job title, and tool-inserted
     placeholder/variable bits change per send. A polished, bespoke-*sounding*
     email blasted to 200 people is **Templated** — full stop. Reads-well is
     irrelevant.
   - **Personalized** = the substance was written for that one prospect and
     not reused.
   - Do NOT judge intrinsic quality, "does it read hand-written," or human-
     vs-AI authorship. None of that. The only question is: was this message
     reused, once you see through the per-recipient variation?
   - The hard part (the whole reason this needs care): detecting "same
     template" THROUGH legitimate variation. A naive "the text differs, so
     it's unique" is the exact mistake to beat — a template whose only change
     is the name/company/a placeholder line is still one template.
3. **More than 2 buckets is allowed** if the data insists (e.g. Personalized /
   Templated / Generic-follow-up), BUT every bucket must be deterministically
   computable. The dashboard may roll buckets up to 2 for display.
3b. **The classifier need NOT be text-only — and probably can't be.** Because
   text alone can't always separate a template's dynamic line from real
   personalization, the deterministic rule may (and likely should) COMBINE
   the text-reuse signal with **Amplemarket sequence membership** — a
   text-independent fact of reuse (was this send an automated sequence step?).
   Sequence membership is thus a candidate classifier INPUT, not merely a
   validator. Sketch to test, not assume: sequence-step → Templated;
   non-sequence + text-reuse detected → Templated; non-sequence + genuinely
   unique → Personalized; "rep heavily rewrote a sequence email" is an edge to
   study, not decide blind. Report where each signal is available (not every
   send may be joinable to a sequence) and the coverage.
4. **The production classifier must be 100% deterministic code** — no LLM
   calls at runtime. You (an LLM) are only used offline to discover rules and
   produce ground-truth labels.
5. **Scale strategy:** cluster programmatically; deep-read only a stratified
   sample. Do NOT attempt to read all 9k+ bodies.
6. **Success bar:** on the small held-out sample (§6.5c) the rule agrees with
   your ground-truth labels **≥95%**, AND all misses are borderline — a pure
   blast labeled "Personalized" (or a bespoke email labeled "Templated") is a
   HARD FAIL regardless of the headline number. Plus the independent
   sequence-membership cross-check (§6.5b) must broadly agree. No formal
   train/validation split is required (too few knobs to overfit); sensitivity
   + the independent cross-check are the real guards.
7. **The old split stays** (renamed). You are not designing its replacement,
   only the new companion metric.

## 5. The temporal requirement (PM's own insight — treat as a core constraint)

The corpus GROWS DAILY. "Sent to 3+ people" is a property of the corpus at
evaluation time, not of the email alone. Your rulebook must specify:

- **Template memory:** each email is compared against ALL core texts / openers
  seen to date (full history), not a rolling window. First occurrence of a
  future template legitimately looks unique.
- **Label mobility:** when copies 2 and 3 of a text arrive, copy 1's label
  must flip to Templated retroactively (nightly recompute). This is honest
  behavior, not a bug — but it must be MEASURED:
  - Replay the corpus chronologically (day by day). Report: what % of
    emails' final labels differ from their day-1 label? What's the median
    days-to-stability? What does the daily "personalized %" look like during
    the replay (does it converge)?
  - If instability is material, propose a display mitigation (e.g. the metric
    shown for a trailing period that has settled), but do NOT weaken the rule
    to avoid flips.
- **Cross-CA memory:** templates are shared across the team; the memory is
  team-wide, not per-CA.

## 6. Your tasks, in order

1. **Setup.** Credentials in `/Users/borja/builds/CA_central/.env` (read it
   in-process; reading it and reaching the DB need Bash with
   `dangerouslyDisableSandbox: true`). Connect with `SUPABASE_DB_URL` via
   psycopg2. **SELECT-only** (see hard rules). Corpus:
   `select activity_id, ca_name, channel, subject_norm, contact_firstname,
   contact_lastname, account_name, contact_jobtitle, contact_email,
   occurred_at, body_html from activity_flat where counts and channel in
   ('auto_email','manual_email')`.
2. **Rebuild + refine the normalization pipeline** (§3). Hunt for NEW leak
   sources beyond the four known ones (quoted chains, greeting names,
   disclaimer, merge values). Candidates to investigate: forwarded chains,
   calendar-invite boilerplate, image-only bodies, non-English emails,
   tracking-pixel artifacts, per-CA signature variants that survive the
   sign-off cut.
3. **Cluster + CHARACTERIZE THE VARIATION (do this before designing any
   rule).** Cluster the full corpus (exact groups + opener groups + a
   similarity pass like shingle/minhash). Then — the important part — read
   across clusters and **enumerate and quantify HOW copies of a template
   actually differ in our data.** The variation is open-ended, NOT just name
   swaps. Expect and measure the prevalence of at least:
   - merge fields (name, company, job title)
   - dynamic / AI-generated first lines (a whole unique sentence on a shared body)
   - spintax / rotated phrasings (synonym swaps to dodge spam filters)
   - conditional blocks (vertical- or segment-specific paragraph swapped in)
   - reordered sentences, different CTA / meeting-link lines, formatting diffs
   Produce a **variation taxonomy with % prevalence**. The deterministic rules
   in step 5 must be designed against THIS, not against an assumption that
   variation = a changed name. Explicitly identify the CEILING: cases where
   text alone cannot separate "template + dynamic line" from "personalized"
   (a tool-generated unique sentence and a human-written one are textually
   identical) — quantify how much of the corpus sits in that ambiguous zone.
4. **Build the ground truth = TEMPLATE FAMILIES (this is the heart of it).**
   The unit of truth is the "template family": a set of emails that are the
   SAME underlying template despite per-recipient variation (name, company,
   placeholder line, etc.). This is corpus-level work, NOT per-email quality
   judgment:
   - Cluster loosely to surface candidate families (similar emails grouped).
   - For a stratified sample of families AND of apparent singletons, ADJUDICATE
     by reading: "are these truly the same reused template (just with the
     variable bits swapped), or genuinely distinct emails that happen to look
     similar?" And for an apparent singleton: "is this really one-of-a-kind, or
     is it a variant of a family the loose clustering missed?"
   - Label each sampled email: **Templated** (belongs to a reuse family) or
     **Personalized** (genuinely not reused). Record the family id + a
     one-line reason. This is the GROUND TRUTH. Save to
     `reports/personalization_ground_truth.csv`.
   - You are the arbiter of "same template vs different email" ONLY. You are
     NOT judging whether an email reads bespoke, is high-effort, or is human-
     vs-AI-written. A gorgeous template sent to 200 people = Templated.
   The deterministic rules (next) are reverse-engineered to reproduce these
   same-family judgments cheaply — do the labeling BEFORE finalizing rules.

5. **Derive the deterministic rules from the ground truth, and DISCOVER which
   parameters matter — don't assume them.**
   - **Ablation:** treat EVERY decision as a knob, not just the numeric ones —
     each normalization step (quoted-chain strip, signature strip, disclaimer,
     each merge-field blank, greeting strip) toggled on/off; AND the
     **similarity measure used to group emails into template families** —
     likely the central knob — choose or invent whatever the variation you
     actually find demands. Known trap to account for (your ablation will
     confirm it): similarity on RAW bodies tends to break — shared
     signature/disclaimer inflate it and quoted reply chains corrupt it both
     ways — so normalization probably needs to precede the similarity step.
     Obvious candidate measures, not a checklist:
       * character-level % identical (difflib ratio / Levenshtein) — catches
         merge-field templates cheaply, but fragile to sentence reordering and
         to a long dynamic/AI first line tanking the score;
       * word/shingle set-overlap (Jaccard) — survives reordering and a
         bolted-on unique line better;
       * longest-shared-block — good when a shared skeleton has unique bits
         inserted.
     Sweep the similarity THRESHOLD, and test whether it must be LENGTH-AWARE
     (a fixed % behaves differently on a 3- vs 10-sentence email). The
     similarity measure only GROUPS emails; "family size ≥ N recipients" is a
     SEPARATE knob on top. Also sweep opener length. Measure each decision's
     effect on (a) agreement with ground truth and (b) the headline %. The
     load-bearing parameters are whatever actually moves the needle; report the
     rest as "tested, doesn't matter" (like the disclaimer). The rulebook must
     present the 3–5 decisions that actually drive the result, each with its
     measured impact — a DISCOVERED list — and every one must stay
     implementable in efficient deterministic production code (compare each
     email to family representatives, not all O(n²) pairs).
   - **Error analysis drives it:** read every case where the rule disagrees
     with your ground-truth labels; the recurring CAUSE of the misses names
     the parameter that matters (this is exactly how the quoted-chain and
     missing-name leaks were found — by reading misses, not guessing).
   - **Validation (not a formal train/test split — overkill for a ~handful-of-
     knobs deterministic rule):**
     (a) **Sensitivity** as above — if the % is stable across a range of each
         threshold, the rule isn't balanced on a knife-edge fit.
     (b) **Independent cross-check:** compare the winning rule's Templated set
         against Amplemarket **sequence membership** (a signal that never
         touches the email text — join tasks/sequence data). High agreement =
         the rule is right for reasons independent of your text labels. This
         is worth more than holding out slices of the same-source labels.
     (c) **Small held-out gut-check (~50–100 emails):** label them, lock them
         away, don't look until the rule is frozen, then report accuracy on
         them as the clean number to quote Dillon.
6. **Temporal replay** (§5). Measure label stability under the winning rule.
7. **Per-CA results** under the winning rule (personalized % per CA, fresh
   outreach vs replies separated).
8. **Deliverable.** Write `reports/personalization_rulebook_proposal.md`:
   - The rulebook in plain English, numbered, deterministic — readable by a
     non-technical sales leader in 2 minutes.
   - The evidence: accuracy vs ground truth, confusion table, 3 REAL example
     emails per bucket (subject + first ~200 chars, no full bodies).
   - Sensitivity table (each threshold: value chosen, alternatives, % impact).
   - Temporal stability findings + recommended display treatment.
   - Per-CA table.
   - Explicit OPEN QUESTIONS for Dillon (e.g. do replies count in the
     headline %? where do generic bumps land?). Give a recommendation each.
   Everything in `reports/` is gitignored (individual performance data) — keep
   it that way.

## 7. Hard rules

- **Database: SELECT only.** Never INSERT/UPDATE/DELETE/ALTER/CREATE/DROP.
  You are not building the production classifier into the DB or pipeline —
  that is a later, separate step after leadership sign-off.
- Do not modify anything under `ingestion/`, `identity/`, `model/`,
  `migrations/`, `dashboard/`, `docs/`. You write only under `reports/` and
  scratch space. Do not git commit or push. Do not touch `.github/`.
- Never print the connection string or any token. Never put full email bodies
  in committed files; short excerpts (≤200 chars) in `reports/` (gitignored)
  are fine.
- Email bodies are confidential business data — they stay on this machine and
  in your analysis outputs under `reports/`.
- Python stdlib + psycopg2 + pandas are available. If you want extra libs
  (e.g. datasketch), prefer stdlib implementations instead.
- Take your time. Thoroughness beats speed. When a judgment call is genuinely
  ambiguous, record it as an open question rather than deciding silently.

## 8. What happens after you finish (so you scope correctly)

Your rulebook proposal → PM reviews → PM takes it to Dillon/Ray → agreed rules
come back → a build phase (separate agent/session) implements them as a
deterministic nightly job + dashboard display, with the usual proof rituals
(activity_flat untouched, before/after reconciliation, tests). You do NOT do
the build phase.
