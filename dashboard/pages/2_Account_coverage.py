"""Account coverage — which accounts get the effort, and which owned ones don't."""
import altair as alt
import pandas as pd
import streamlit as st

import db
import queries
import ui

first, last = ui.setup(
    "Account coverage",
    "Which accounts each CA is working — and which of the accounts they own are being left untouched.")
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
st.subheader("Owned accounts")
st.caption("How much of what each CA owns are they actually working? "
           "“Owns” = accounts where they are the HubSpot target-account owner; "
           "“They touched” = how many of those they personally worked in this window; "
           "coverage is the ratio.")
cov = db.q(queries.OWNED_COVERAGE, (start, end))
t01 = cov[(cov.icp_tier.isin(["Tier 0", "Tier 1"])) & (cov.team_touches == 0)]

per_rep = cov.groupby("owner_name").agg(
    owned=("account_name", "count"),
    owner_touched=("owner_touches", lambda s: int((s > 0).sum())),
).reset_index()
per_rep["coverage_pct"] = (per_rep.owner_touched / per_rep.owned * 100).round(0)
per_rep = per_rep.sort_values("coverage_pct")

st.dataframe(
    per_rep, hide_index=True, use_container_width=True,
    column_config={
        "owner_name": st.column_config.TextColumn("Owner (CA)", pinned=True),
        "owned": st.column_config.NumberColumn("Owns", help=ui.DEFS["accounts_owned"]),
        "owner_touched": st.column_config.NumberColumn(
            "They touched", help=ui.DEFS["owned_touched"]),
        "coverage_pct": st.column_config.NumberColumn(
            "Coverage", format="%d%%", help=ui.DEFS["coverage_pct"]),
    })

# --- neglected top-tier accounts as floating bubbles ----------------------------
st.subheader("Neglected top-tier accounts")
ui.pill("<b>%d</b> owned top-tier accounts with zero touches from anyone in this window"
        % len(t01), "red")
st.caption("Top tier = HubSpot Tier 0 and Tier 1 — leadership's own best-fit designation, "
           "the one place we deliberately lean on tier because it marks the accounts that "
           "matter most. Each bubble below is one such account nobody on the team touched, "
           "clustered under its owning CA (bigger bubble = Tier 0). Touch counts are a floor: "
           "zero means no recorded touch.")
if len(t01):
    neg = t01.copy()
    neg["bubble"] = neg.icp_tier.map({"Tier 0": 3, "Tier 1": 1})
    neg["rank"] = neg.groupby("owner_name").cumcount()
    owners = neg.groupby("owner_name").size().sort_values(ascending=False).index.tolist()
    st.altair_chart(ui.themed(
        alt.Chart(neg).mark_circle(opacity=0.75, stroke="#161A21", strokeWidth=1).encode(
            x=alt.X("owner_name:N", sort=owners, title=None, axis=alt.Axis(labelAngle=-40)),
            y=alt.Y("rank:Q", axis=None, title=None),
            size=alt.Size("bubble:Q", legend=None, scale=alt.Scale(range=[90, 430])),
            color=alt.Color("owner_name:N", legend=None,
                            scale=alt.Scale(scheme="set2")),
            tooltip=[alt.Tooltip("account_name:N", title="Account"),
                     alt.Tooltip("owner_name:N", title="Owner"),
                     alt.Tooltip("icp_tier:N", title="Tier")],
        ).properties(height=max(240, 30 * neg.groupby("owner_name").size().max()))),
        use_container_width=True)
    st.caption("Hover a bubble for the account name and tier.")
else:
    st.success("None in this window.")

with st.expander("Every owned account (incl. zeros) — owner vs team touches"):
    st.caption("“Owner 0, team 12” is colleagues working your account — not neglect.")
    st.dataframe(cov, hide_index=True, use_container_width=True)
