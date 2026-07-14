# CA Activity Visibility — Context & Background

**Read this first.** It explains *why* this project exists and what "good" looks like, so anyone (human or agent) can pick up the work without prior context. The technical "how" is in `spec.md`, `research.md`, and `roadmap.md`.

---

## The problem

We can't clearly see what activity a CA (the reps doing outbound) is actually doing on their accounts. The activity is split across two tools:

- **AmpleMarket** — the main outreach tool, roughly **80%** of activity (LinkedIn messages + connections, calls, and email sequences).
- **HubSpot** — holds everything else, mainly manual emails the rep writes themselves (synced in from Gmail) and meetings.

Because the data lives in two separate places, no one can get an accurate, combined picture of how hard and how well a rep is working their accounts. Each tool shows half the story; neither shows the whole.

## Why it matters

We're booking a reasonable number of meetings, but we want to improve both the **quantity** and the **quality** of meetings booked. Right now we can look at the quality of outcomes, but the **volume and depth of activity is a grey area** — we can't confidently say a rep is doing the right work.

Without visibility into the work itself, it's very hard to understand why one CA performs better than another, or to coach the ones who are underperforming. Leaders need this data to spot where a rep is falling short and help them fix it — the end goal being every CA consistently hitting their target of qualified opportunities ("SEOs") per month because they've been set up and coached well.

## What we want to see

For each rep, not just "how much activity" overall, but **depth per account**:

- How many **accounts** are they working, out of the ones they own (their target territory)?
- Within each account, how many **different people** are they reaching?
- How many **touchpoints**, and across which **channels** (calls, LinkedIn, manual vs. automated emails, meetings)?
- Are they working **all** their accounts, or hammering a few and ignoring the rest?
- Later (v2): is the outreach **tailored and personal**, or generic automated sequences?

## Concrete examples (from the stakeholder)

These illustrate the kind of insight we're after:

- *"For Frontier Health over the last 30 days — she's spoken to 7 people, had ~20 touchpoints, from 5 emails and 7 calls."* → per-account depth.
- *"George has 32 accounts. Is he doing 20 touchpoints across all 32, or hitting 20 hard and leaving 12 untouched?"* → coverage + concentration.
- *"186 contacts across 32 accounts shows good penetration (~6 people per account)."* → healthy depth.
- *"36 people across 22 accounts is too narrow (~1.5 per account)."* → too shallow.

**Note:** these numbers are the stakeholder thinking out loud — **illustrative, not confirmed targets.** Firm benchmarks for "good" are still to be set (see the threshold question in the roadmap). Until then the tool shows the numbers and leaders judge.

## Who uses it

Sales leaders — **James Falkner, Leo, DC**, and the project owner — will look at this regularly to understand rep performance and decide where to step in and coach. They are **not technical**: they need a filterable dashboard, not a database or queries.

## What success looks like for the tool

One place where a leader can pick a rep and see, for any time window (this week, last 30 days), how much and how well that rep worked their accounts — down to individual accounts and contacts — with enough context (account quality, seniority of people reached, personal vs. automated) to judge whether it's genuinely good outbound.
