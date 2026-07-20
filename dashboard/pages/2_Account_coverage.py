"""Account coverage — which accounts get the effort, and which owned ones don't."""
import altair as alt
import pandas as pd
import streamlit as st

import db
import queries
import ui

first, last = ui.setup(
    "Account coverage",
    "Which accounts each CA is working — and which of the accounts they own are being left untouched.",
    "🗺️")
start, end, label = ui.window_pills(first, last)

# --- CA × account heat map ------------------------------------------------------
st.subheader("Where the touchpoints are landing")
d = db.q(queries.REP_ACCOUNTS_ALL, (start, end))
d = d[d.account_name != "(no account matched)"]
top_accounts = (d.groupby("account_name").touchpoints.sum()
                 .sort_values(ascending=False).head(25).index.tolist())
hm = d[d.account_name.isin(top_accounts)]
st.altair_chart(ui.themed(
    alt.Chart(hm).mark_rect(cornerRadius=2).encode(
        x=alt.X("ca_name:N", title=None, axis=alt.Axis(labelAngle=-40)),
        y=alt.Y("account_name:N", sort=top_accounts, title=None,
                axis=alt.Axis(labelLimit=280)),
        color=alt.Color("touchpoints:Q", title="Touchpoints",
                        scale=alt.Scale(range=["#232A12", ui.LIME], interpolate="rgb")),
        tooltip=["ca_name", "account_name", "touchpoints"],
    ).properties(height=560)),
    use_container_width=True)
st.caption("The team's 25 most-touched accounts — brighter = more touchpoints. "
           "A bright row worked by several CAs may deserve coordination; "
           "a dark row may deserve attention.")

# --- owned-account coverage per CA ---------------------------------------------
st.subheader("Owned accounts — is anyone home?")
cov = db.q(queries.OWNED_COVERAGE, (start, end))
t01 = cov[(cov.icp_tier.isin(["Tier 0", "Tier 1"])) & (cov.team_touches == 0)]

per_rep = cov.groupby("owner_name").agg(
    owned=("account_name", "count"),
    owner_touched=("owner_touches", lambda s: int((s > 0).sum())),
    team_touched=("team_touches", lambda s: int((s > 0).sum())),
).reset_index()
per_rep["neglected_t01"] = per_rep.owner_name.map(
    t01.groupby("owner_name").size()).fillna(0).astype(int)
per_rep["coverage_pct"] = (per_rep.owner_touched / per_rep.owned * 100).round(0)
per_rep = per_rep.sort_values("coverage_pct")

st.dataframe(
    per_rep, hide_index=True, use_container_width=True,
    column_config={
        "owner_name": st.column_config.TextColumn("Owner (CA)", pinned=True),
        "owned": st.column_config.NumberColumn("Owns", help=ui.DEFS["accounts_owned"]),
        "owner_touched": st.column_config.NumberColumn(
            "They touched", help=ui.DEFS["owned_touched"]),
        "team_touched": st.column_config.NumberColumn(
            "Anyone touched", help="Owned accounts touched by ANY CA in the window."),
        "neglected_t01": st.column_config.NumberColumn(
            "🚩 Top-tier untouched",
            help="Owned Tier 0/1 accounts nobody on the team touched in this window."),
        "coverage_pct": st.column_config.ProgressColumn(
            "Coverage", format="%.0f%%", min_value=0, max_value=100,
            help=ui.DEFS["coverage_pct"]),
    })

# --- neglected top-tier accounts, grouped by CA ----------------------------------
st.subheader("Neglected top-tier accounts")
ui.pill("<b>%d</b> owned Tier 0/1 accounts with zero touches from anyone in this window"
        % len(t01), "red")
st.caption("Touch counts are a floor (the missing-company gap can hide touches, "
           "never invent them) — zero means no *recorded* touch.")
if len(t01):
    counts = t01.groupby("owner_name").size().sort_values(ascending=False)
    pick_col, list_col = st.columns([1.1, 2])
    with pick_col:
        st.altair_chart(ui.themed(
            alt.Chart(counts.reset_index(name="n")).mark_bar(color=ui.PURPLE).encode(
                x=alt.X("n:Q", title="Neglected top-tier accounts"),
                y=alt.Y("owner_name:N", sort="-x", title=None),
                tooltip=["owner_name", "n"],
            ).properties(height=24 * len(counts))),
            use_container_width=True)
    with list_col:
        who = st.selectbox("Whose accounts to list", counts.index.tolist())
        st.dataframe(
            t01[t01.owner_name == who][["account_name", "icp_tier", "vertical"]],
            hide_index=True, use_container_width=True,
            column_config={"account_name": "Account", "icp_tier": "Tier",
                           "vertical": "Vertical"})
else:
    st.success("None in this window.")

with st.expander("Every owned account (incl. zeros) — owner vs team touches"):
    st.caption("“Owner 0, team 12” is colleagues working your account — not neglect.")
    st.dataframe(cov, hide_index=True, use_container_width=True)
