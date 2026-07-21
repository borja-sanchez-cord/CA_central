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
st.caption("Top 25 most-touched accounts. Brighter = more touchpoints.")

# --- accounts by touchpoint volume (distribution per CA) ------------------------
st.subheader("Accounts by touchpoint volume")
dv = db.q(queries.REP_ACCOUNTS_ALL, (start, end))
dv = dv[dv.account_name != "(no account matched)"].copy()
order_ca = dv.groupby("ca_name").size().sort_values(ascending=False).index.tolist()

colour_by = st.radio(
    "Colour by", ["Ownership", "Touchpoint depth"], horizontal=True,
    label_visibility="collapsed", key="volume_colour")

if colour_by == "Ownership":
    # Each bar = every account the CA touched (owned or not). Split so the
    # "Owned by this CA" segment lines up with the Owned-accounts table below
    # — the two visuals use different denominators, so make the gap visible.
    def own_class(row):
        if row.owned_by_this_rep:
            return "Owned by this CA"
        return "Owned by someone else" if row.owned_by_rep_id else "No owner in HubSpot"
    OWN_ORDER = ["Owned by this CA", "Owned by someone else", "No owner in HubSpot"]
    OWN_COLOR = ["#6223E9", "#8A7CB8", "#5A616E"]   # yours = bright, else muted, none = grey
    dv["seg"] = dv.apply(own_class, axis=1)
    dist = (dv.groupby(["ca_name", "seg"], observed=True).size()
              .reset_index(name="accounts"))
    dist["seg_order"] = dist.seg.map({s: i for i, s in enumerate(OWN_ORDER)})
    st.altair_chart(ui.themed(
        alt.Chart(dist).mark_bar().encode(
            x=alt.X("accounts:Q", title="Accounts touched"),
            y=alt.Y("ca_name:N", sort=order_ca, title=None),
            color=alt.Color("seg:N", title=None,
                            scale=alt.Scale(domain=OWN_ORDER, range=OWN_COLOR),
                            sort=OWN_ORDER),
            order=alt.Order("seg_order:Q"),
            tooltip=["ca_name", "seg", "accounts"],
        ).properties(height=26 * max(1, len(order_ca)))),
        use_container_width=True)
    st.caption("Every account each CA touched. The purple 'Owned by this CA' segment "
               "equals their number in the Owned-accounts table below — the rest are "
               "accounts they worked but don't own.")
else:
    BUCKETS = [("1-9", 1, 9), ("10-24", 10, 24), ("25-49", 25, 49),
               ("50-99", 50, 99), ("100+", 100, 10**9)]
    dv["bucket"] = pd.cut(dv.touchpoints,
                          bins=[0, 9, 24, 49, 99, 10**9],
                          labels=[b[0] for b in BUCKETS])
    dist = dv.groupby(["ca_name", "bucket"], observed=True).size().reset_index(name="accounts")
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
        "owned": st.column_config.NumberColumn(
            "Owns", help="Accounts where this CA is the HubSpot target-account owner."),
        "owner_touched": st.column_config.NumberColumn(
            "They touched", help="Of those, how many they personally worked in this window."),
        "coverage_pct": st.column_config.NumberColumn(
            "Coverage", format="%d%%", help="They touched / Owns."),
    })

# --- neglected top-tier accounts -------------------------------------------------
st.subheader("Neglected top-tier accounts")
ui.pill("<b>%d</b> owned top-tier accounts, zero recorded touches by anyone" % len(t01), "red")
st.caption("Top tier = HubSpot Tier 0/1 — the validated tier field, never the automated one.")
if len(t01):
    counts = (t01.groupby(["owner_name", "icp_tier"]).size()
                 .reset_index(name="n"))
    owners = (t01.groupby("owner_name").size()
                 .sort_values(ascending=False).index.tolist())
    bar_col, list_col = st.columns([1.3, 1.7])
    with bar_col:
        st.altair_chart(ui.themed(
            alt.Chart(counts).mark_bar().encode(
                x=alt.X("n:Q", title="Accounts"),
                y=alt.Y("owner_name:N", sort=owners, title=None),
                color=alt.Color("icp_tier:N", title=None,
                                scale=alt.Scale(domain=["Tier 0", "Tier 1"],
                                                range=["#BF616A", "#D8A0A6"])),
                tooltip=["owner_name", "icp_tier", "n"],
            ).properties(height=26 * len(owners))),
            use_container_width=True)
    with list_col:
        who = st.selectbox("List one CA's accounts", owners)
        st.dataframe(
            t01[t01.owner_name == who][["account_name", "icp_tier"]]
                .sort_values(["icp_tier", "account_name"]),
            hide_index=True, use_container_width=True,
            column_config={"account_name": "Account",
                           "icp_tier": st.column_config.TextColumn("Tier (validated)")})
else:
    st.success("None in this window.")

with st.expander("Every owned account (incl. zeros) — owner vs team touches"):
    st.caption("“Owner 0, team 12” is colleagues working your account — not neglect.")
    st.dataframe(cov, hide_index=True, use_container_width=True)
