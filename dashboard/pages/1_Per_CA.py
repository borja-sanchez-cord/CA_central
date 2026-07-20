"""Per CA — one rep: what they did, how it's trending, where it went."""
import altair as alt
import pandas as pd
import streamlit as st

import db
import queries
import ui

first, last = ui.setup(
    "Per CA",
    "One CA's activity: what they did, how it's trending, and which accounts it went into.")

reps = db.q(queries.REPS)["name"].tolist()
c1, _ = st.columns([1.4, 2])
rep = c1.selectbox("Customer Associate", reps, label_visibility="collapsed")
start, end, label = ui.window_pills(first, last)

sc = db.q(queries.SCORECARD, (start, end))
row = sc[sc.ca_name == rep]
if row.empty:
    st.info("No data for %s in this window." % rep)
    st.stop()
r = row.iloc[0]

cov_pct = 0 if pd.isna(r.coverage_pct) else r.coverage_pct
ui.kpi_row([
    {"label": "Activities", "value": int(r.total_counted),
     "help": ui.DEFS["total_counted"]},
    {"label": "Emails", "value": int(r.emails),
     "sub": "%d auto / %d manual" % (r.auto_email, r.manual_email),
     "help": ui.DEFS["emails"]},
    {"label": "Dials", "value": int(r.dials),
     "sub": "%d convos / %d pursuits" % (r.conversations, r.pursuits),
     "help": ui.DEFS["conversations"]},
    {"label": "LinkedIn", "value": int(r.linkedin),
     "sub": "%d con / %d msg / %d other" % (r.li_connect, r.li_message, r.li_other),
     "help": ui.DEFS["linkedin"]},
    {"label": "Meetings", "value": int(r.meetings_booked),
     "sub": "%d held / %d canc / %d sch / %d unk" % (
         r.meetings_held, r.meetings_canceled, r.meetings_scheduled, r.meetings_unknown),
     "help": ui.DEFS["meetings_booked"]},
    {"label": "Coverage", "value": "%.0f%%" % cov_pct,
     "sub": "%d of %d owned touched" % (r.owned_touched, r.accounts_owned),
     "help": ui.DEFS["coverage_pct"]},
])
st.write("")

# --- weekly trend for this rep ----------------------------------------------
st.subheader("Week by week")
wk = ui.week_label(db.q(queries.WEEKLY_TREND))
wk = wk[wk.ca_name == rep]
trend_cols = ["emails", "dials", "linkedin", "inbound_replies", "meetings_booked"]
tl = wk[["week", "week_start"] + trend_cols].melt(
    ["week", "week_start"], var_name="m", value_name="count")
tl["measure"] = tl.m.map(ui.MEASURE_LABELS)
order = [w for w in wk.sort_values("week_start").week.unique()]
st.altair_chart(
    ui.trend_chart(tl, "count", "measure", order,
                   [ui.MEASURE_LABELS[c] for c in trend_cols],
                   [ui.MEASURE_COLORS[c] for c in trend_cols], height=280),
    use_container_width=True)
st.caption("Latest week is partial until Sunday.")

# --- account breakdown --------------------------------------------------------
st.subheader("Which accounts the touchpoints went into")
acc = db.q(queries.REP_ACCOUNTS, (start, end, rep))
if acc.empty:
    st.info("No account-level activity in this window.")
    st.stop()

no_acc = acc[acc.account_name == "(no account matched)"]
acc_v = acc[acc.account_name != "(no account matched)"].copy()
total_tp = int(acc.touchpoints.sum())
if len(no_acc):
    ui.pill("<b>%d</b> of %d touchpoints have no account recorded — still counted in totals"
            % (int(no_acc.touchpoints.iloc[0]), total_tp))

ch_cols = ["auto_email", "manual_email", "calls", "linkedin", "inbound_replies"]
CH_LBL = {"auto_email": "Automated emails", "manual_email": "Manual emails",
          "calls": "Dials", "linkedin": "LinkedIn", "inbound_replies": "Inbound replies"}
CH_COL = {"auto_email": "#A7C957", "manual_email": "#6E8B3D", "calls": "#CC7A6F",
          "linkedin": "#7DA0CA", "inbound_replies": "#E4C07A"}
top = acc_v.head(15)
stk = top[["account_name"] + ch_cols].melt("account_name", var_name="m", value_name="count")
stk["channel"] = stk.m.map(CH_LBL)
st.altair_chart(ui.themed(
    alt.Chart(stk).mark_bar().encode(
        x=alt.X("count:Q", title="Touchpoints"),
        y=alt.Y("account_name:N", sort="-x", title=None,
                axis=alt.Axis(labelLimit=300)),
        color=alt.Color("channel:N", title=None,
                        scale=alt.Scale(domain=list(CH_LBL.values()),
                                        range=[CH_COL[c] for c in ch_cols])),
        tooltip=["account_name", "channel", "count"],
    ).properties(height=max(140, 24 * len(top)))),
    use_container_width=True)

if len(acc_v):
    acc_v["pct_of_touchpoints"] = (acc_v.touchpoints / total_tp * 100).round(1)
    st.dataframe(
        acc_v[["account_name", "touchpoints", "pct_of_touchpoints", "people_touched",
               "owned_by_this_rep", "icp_tier", "last_touch"]],
        hide_index=True, use_container_width=True,
        column_config={
            "account_name": st.column_config.TextColumn("Account", pinned=True),
            "touchpoints": st.column_config.ProgressColumn(
                "Touchpoints", format="%d", min_value=0,
                max_value=int(acc_v.touchpoints.max()), help=ui.DEFS["touchpoints"]),
            "pct_of_touchpoints": st.column_config.NumberColumn("% of total", format="%.1f%%"),
            "people_touched": st.column_config.NumberColumn("People"),
            "owned_by_this_rep": st.column_config.CheckboxColumn("Owned by this CA"),
            "icp_tier": st.column_config.TextColumn("Tier"),
            "last_touch": st.column_config.DateColumn("Last touch"),
        })

# --- drill to people at one account ------------------------------------------
st.subheader("Drill into one account")
target = st.selectbox("Pick an account to see exactly who %s contacted there" % rep,
                      acc_v.account_name.tolist())
st.markdown("**Who %s contacted at %s**" % (rep, target))
ppl = db.q(queries.ACCOUNT_CONTACTS, (start, end, rep, target))
if ppl.empty:
    st.info("No person-level rows here (activity logged without a contact).")
else:
    st.dataframe(
        ppl, hide_index=True, use_container_width=True,
        column_config={
            "contact_name": st.column_config.TextColumn("Person", pinned=True),
            "contact_email": "Email", "jobtitle": "Job title",
            "touchpoints": st.column_config.NumberColumn("Touchpoints"),
            "emails": "Emails", "calls": "Dials", "linkedin": "LinkedIn",
            "inbound_replies": "Inbound", "last_touch": st.column_config.DateColumn("Last touch"),
        })
