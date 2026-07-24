"""Account coverage — which accounts get the effort, and which owned ones don't."""
import altair as alt
import pandas as pd
import streamlit as st

import db
import queries
import ui

first, last = ui.setup(
    "Account coverage & neglects",
    "Which accounts each CA is working — and which of the accounts they own are being left untouched.")
start, end, label = ui.window_pills(first, last)

# --- accounts by touchpoint volume (distribution per CA) ------------------------
st.subheader("Touchpoint concentration")
BUCKETS = [("1-9", 1, 9), ("10-24", 10, 24), ("25-49", 25, 49),
           ("50-99", 50, 99), ("100+", 100, 10**9)]
dv = ui.active_only(db.q(queries.REP_ACCOUNTS_ALL, (start, end)))
dv = dv[dv.account_name != "(no account matched)"].copy()
dv["bucket"] = pd.cut(dv.touchpoints,
                      bins=[0, 9, 24, 49, 99, 10**9],
                      labels=[b[0] for b in BUCKETS])
dist = dv.groupby(["ca_name", "bucket"], observed=True).size().reset_index(name="accounts")
order_ca = dv.groupby("ca_name").size().sort_values(ascending=False).index.tolist()
st.altair_chart(ui.themed(
    alt.Chart(dist).mark_bar().encode(
        x=alt.X("accounts:Q", title="Accounts"),
        y=alt.Y("ca_name:N", sort=order_ca, title=None),
        color=alt.Color("bucket:N", title="Touchpoints",
                        scale=alt.Scale(domain=[b[0] for b in BUCKETS],
                                        range=["#4A415F", "#5C4A8F", "#6E52C4",
                                               "#8A55F7", "#B3E249"])),
        tooltip=["ca_name", "bucket", "accounts"],
    ).properties(height=26 * max(1, len(order_ca)))),
    use_container_width=True)
st.caption("Depth vs spread: many accounts at 1-9 touchpoints = wide and shallow; "
           "100+ on one account = concentrated bets.")

# --- owned-account coverage per CA ---------------------------------------------
st.subheader("Owned accounts")
st.caption("How much of what each CA owns are they actually working?")
cov = ui.active_only(db.q(queries.OWNED_COVERAGE, (start, end)), col="owner_name")
# deal-derived labels (migration 008, Dillon #24+#25): joined for DISPLAY only —
# none of the coverage numbers change, an account only ever gains a label.
ds = db.q(queries.ACCOUNT_DEAL_STATUS)
cov = cov.merge(ds, on="account_id", how="left")
t01 = cov[(cov.icp_tier.isin(["Tier 0", "Tier 1"])) & (cov.team_touches == 0)]

# Coverage is deal-aware (Dillon, Jul 2026): only WORKABLE accounts count —
# a shield (customer / open deal / resting after a recent loss or churn)
# excludes the account from BOTH sides of the ratio. Same rule as the
# neglected flag below and the same recompute Team overview uses.
workable = cov[cov.shield.isna()]
per_rep = workable.groupby("owner_name").agg(
    owned=("account_name", "count"),
    owner_touched=("owner_touches", lambda s: int((s > 0).sum())),
).reset_index()
per_rep["coverage_pct"] = (per_rep.owner_touched / per_rep.owned * 100).round(0)
per_rep = per_rep.sort_values("coverage_pct")

st.dataframe(
    per_rep, hide_index=True, use_container_width=True,
    column_config={
        "owner_name": st.column_config.TextColumn("Owner (CA)", pinned=True),
        "owned": st.column_config.NumberColumn(
            "Workable", help="Owned accounts this CA could work today — customers, "
                             "open deals and recently lost/churned excluded."),
        "owner_touched": st.column_config.NumberColumn(
            "They touched", help="Of those, how many they personally worked in this window."),
        "coverage_pct": st.column_config.NumberColumn(
            "Coverage", format="%d%%", help=ui.DEFS["coverage_pct"]),
    })
st.caption(":grey[Coverage is deal-aware — same rule as the neglected flag below: customers, "
           "open deals and recently lost/churned accounts count on neither side of the ratio.]")

# --- neglected top-tier accounts -------------------------------------------------
# Zero-touch accounts that are customers, mid-deal, or recently lost/churned
# are NOT neglect (Dillon rules, Jul 2026) — they get a label and stay in the
# list; only the unshielded remainder is flagged red and charted.
st.subheader("Neglected top-tier accounts", help=(
    "**What counts as neglected?**\n\n"
    "An owned **top-tier** account (Tier 0/1) with **zero touches by anyone** — "
    "unless one of these says leave it alone:\n"
    "- **Customer** — a won deal, not churned since. Never flagged.\n"
    "- **Open deal** — a deal in progress. Not flagged while open "
    "(age shown, so a stale deal nobody closed stays visible).\n"
    "- **Lost** — closed-lost. Rests 60 days, then flags again.\n"
    "- **Churned** — churn or lost renewal. Rests 9 months, then flags again.\n\n"
    "Labels come from HubSpot deals, synced nightly."))


def _status(r):
    if r.shield == "customer":
        return "Customer"
    if r.shield == "open_deal":
        # deal AGE surfaced on purpose: a zombie deal nobody closed would
        # otherwise hide real neglect forever (Dillon's caveat, Ray owns
        # the process fix — decisions.md 2026-07-22)
        return ("Open deal (%dd old)" % r.oldest_open_deal_days
                if pd.notna(r.oldest_open_deal_days) else "Open deal")
    if r.shield == "churned_recently":
        return "Churned %s" % pd.Timestamp(r.last_churned_date).strftime("%b %Y")
    if r.shield == "lost_recently":
        return "Lost %s" % pd.Timestamp(r.last_lost_date).strftime("%d %b")
    return "Neglected"


t01 = t01.copy()
# guard: .apply(axis=1) on an EMPTY frame returns a DataFrame, not a Series
t01["status"] = t01.apply(_status, axis=1) if len(t01) else ""
negl = t01[t01.shield.isna()]
ui.pill("<b>%d</b> owned top-tier accounts, zero recorded touches by anyone" % len(negl), "red")
st.caption("Top tier = HubSpot Tier 0/1 — the validated tier field, never the automated one. "
           "%d more untouched top-tier accounts are labelled, not flagged (see the "
           "**?** by the heading)." % (len(t01) - len(negl)))
if len(t01):
    counts = (negl.groupby(["owner_name", "icp_tier"]).size()
                  .reset_index(name="n"))
    owners = (t01.groupby("owner_name").size()
                 .sort_values(ascending=False).index.tolist())
    bar_col, list_col = st.columns([1.3, 1.7])
    with bar_col:
        if len(negl):
            n_owners = (negl.groupby("owner_name").size()
                            .sort_values(ascending=False).index.tolist())
            st.altair_chart(ui.themed(
                alt.Chart(counts).mark_bar().encode(
                    x=alt.X("n:Q", title="Accounts"),
                    y=alt.Y("owner_name:N", sort=n_owners, title=None),
                    color=alt.Color("icp_tier:N", title=None,
                                    scale=alt.Scale(domain=["Tier 0", "Tier 1"],
                                                    range=["#BF616A", "#D8A0A6"])),
                    tooltip=["owner_name", "icp_tier", "n"],
                ).properties(height=26 * len(n_owners))),
                use_container_width=True)
        else:
            st.success("Every untouched top-tier account has a deal-status label.")
    with list_col:
        who = st.selectbox("List one CA's accounts", owners)
        st.dataframe(
            t01[t01.owner_name == who][["account_name", "icp_tier", "status"]]
                .sort_values(["status", "icp_tier", "account_name"],
                             key=lambda s: s.map(lambda v: " " + v
                                                 if v == "Neglected" else str(v))
                             if s.name == "status" else s),
            hide_index=True, use_container_width=True,
            column_config={"account_name": "Account",
                           "icp_tier": st.column_config.TextColumn("Tier (validated)"),
                           "status": st.column_config.TextColumn(
                               "Status", help=ui.DEFS["neglect_status"])})
else:
    st.success("None in this window.")

with st.expander("Every owned account (incl. zeros) — owner vs team touches"):
    st.caption("“Owner 0, team 12” is colleagues working your account — not neglect.")
    full = cov.copy()
    full["deal_status"] = full.shield.map({
        "customer": "Customer", "open_deal": "Open deal",
        "churned_recently": "Churned recently", "lost_recently": "Lost recently"})
    st.dataframe(
        full[["owner_name", "account_name", "icp_tier", "vertical", "deal_status",
              "owner_touches", "owner_last_touch", "team_touches",
              "team_last_touch", "team_reps"]],
        hide_index=True, use_container_width=True,
        column_config={"deal_status": st.column_config.TextColumn(
            "Deal status", help=ui.DEFS["neglect_status"])})
