"""Heat map — where each rep's effort lands by ICP tier — and owned-account neglect."""
import altair as alt
import pandas as pd
import streamlit as st

import db
import queries
import ui

first, last = ui.page_setup("Heat map & neglect", "🗺️")
start, end, label = ui.window_picker(first, last)

# --- rep × tier heat map ------------------------------------------------------
st.subheader("Touchpoints by CA × ICP tier (%s)" % label)
st.caption("Answers “are touchpoints going into good accounts?” " + ui.NO_ACCOUNT_NOTE)

d = db.q(queries.REP_ACCOUNTS_ALL, (start, end))
d["bucket"] = d.icp_tier.fillna("No tier")
d.loc[d.account_name == "(no account matched)", "bucket"] = "(no account matched)"
order = ["Tier 0", "Tier 1", "Tier 2", "Tier 3", "Tier 4", "DQ",
         "No tier", "(no account matched)"]
hm = d.groupby(["ca_name", "bucket"], as_index=False).touchpoints.sum()

st.altair_chart(
    alt.Chart(hm).mark_rect().encode(
        x=alt.X("bucket:N", sort=order, title="ICP tier"),
        y=alt.Y("ca_name:N", title=None),
        color=alt.Color("touchpoints:Q", scale=alt.Scale(scheme="blues"), title="Touchpoints"),
        tooltip=["ca_name", "bucket", "touchpoints"],
    ).properties(height=26 * hm.ca_name.nunique()),
    use_container_width=True)

# --- owned-account coverage / neglect ------------------------------------------
st.subheader("Owned accounts — coverage & neglect (%s)" % label)
cov = db.q(queries.OWNED_COVERAGE, (start, end))

per_rep = cov.groupby("owner_name").agg(
    owned=("account_name", "count"),
    owner_touched=("owner_touches", lambda s: int((s > 0).sum())),
    team_touched=("team_touches", lambda s: int((s > 0).sum())),
    t01_untouched=("account_name", "count"),  # replaced below
).reset_index()
t01 = cov[(cov.icp_tier.isin(["Tier 0", "Tier 1"])) & (cov.team_touches == 0)]
per_rep["t01_untouched"] = per_rep.owner_name.map(
    t01.groupby("owner_name").size()).fillna(0).astype(int)
per_rep["coverage_pct"] = (per_rep.owner_touched / per_rep.owned * 100).round(0)
per_rep = per_rep.sort_values("coverage_pct")

st.dataframe(
    per_rep, hide_index=True, use_container_width=True,
    column_config={
        "owner_name": st.column_config.TextColumn("Owner (CA)", pinned=True),
        "owned": st.column_config.NumberColumn("Accounts owned", help=ui.DEFS["accounts_owned"]),
        "owner_touched": st.column_config.NumberColumn(
            "Touched by owner", help=ui.DEFS["owned_touched"]),
        "team_touched": "Touched by anyone",
        "t01_untouched": st.column_config.NumberColumn(
            "🚩 Tier 0/1 nobody touched", help="Owned Tier 0/1 accounts with zero team touches."),
        "coverage_pct": st.column_config.NumberColumn("Coverage %", format="%.0f%%",
                                                      help=ui.DEFS["coverage_pct"]),
    })

flagged = len(t01)
st.subheader("🚩 Neglected Tier 0/1 owned accounts — %d" % flagged)
st.caption("Owned, top-tier, and NOBODY on the team touched them in this window. "
           "Touch counts are a floor (missing-company gap): zero = no *recorded* touch.")
if flagged:
    st.dataframe(
        t01[["owner_name", "account_name", "icp_tier", "vertical",
             "owner_last_touch", "team_last_touch"]],
        hide_index=True, use_container_width=True)
else:
    st.success("None in this window.")

with st.expander("Every owned account (incl. zeros) — owner vs team touches"):
    st.caption("“Owner 0, team 12” is colleagues working your account — not neglect.")
    st.dataframe(cov, hide_index=True, use_container_width=True)
