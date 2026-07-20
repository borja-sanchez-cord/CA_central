"""Rep detail — one CA: scorecard, weekly trend, account breakdown, drill to people."""
import altair as alt
import pandas as pd
import streamlit as st

import db
import queries
import ui

first, last = ui.page_setup("Rep detail", "🧑‍💼")
start, end, label = ui.window_picker(first, last)

reps = db.q(queries.REPS)["name"].tolist()
rep = st.selectbox("Customer Associate", reps)

sc = db.q(queries.SCORECARD, (start, end))
row = sc[sc.ca_name == rep]
if row.empty:
    st.info("No data for %s in this window." % rep)
    st.stop()
r = row.iloc[0]

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Total counted", int(r.total_counted), help=ui.DEFS["total_counted"])
c2.metric("Emails (auto+manual)", "%d (%d+%d)" % (r.emails, r.auto_email, r.manual_email),
          help=ui.DEFS["emails"])
c3.metric("Dials / conversations", "%d / %d" % (r.dials, r.conversations),
          help=ui.DEFS["conversations"])
c4.metric("LinkedIn", int(r.linkedin), help=ui.DEFS["linkedin"])
c5.metric("Meetings booked", int(r.meetings_booked), help=ui.DEFS["meetings_booked"])
cov_pct = 0 if pd.isna(r.coverage_pct) else r.coverage_pct
c6.metric("Coverage", "%.0f%% (%d/%d)" % (cov_pct, r.owned_touched, r.accounts_owned),
          help=ui.DEFS["coverage_pct"])
st.caption("Meetings split: %d held / %d canceled / %d scheduled / %d unknown. %s"
           % (r.meetings_held, r.meetings_canceled, r.meetings_scheduled,
              r.meetings_unknown, ui.MEETINGS_NOTE))

# --- weekly trend for this rep ----------------------------------------------
st.subheader("Week by week")
wk = db.q(queries.WEEKLY_TREND)
wk = wk[wk.ca_name == rep]
trend_cols = ["total_counted", "emails", "dials", "linkedin", "inbound_replies", "meetings_booked"]
tl = wk[["week_start"] + trend_cols].melt("week_start", var_name="measure", value_name="count")
st.altair_chart(
    alt.Chart(tl).mark_line(point=True).encode(
        x=alt.X("week_start:T", title="Week (Mon start)"),
        y=alt.Y("count:Q", title=None),
        color=alt.Color("measure:N", title="Measure"),
        tooltip=["week_start:T", "measure", "count"],
    ).properties(height=260),
    use_container_width=True)
if len(wk) and wk.week_start.max() >= last:
    st.caption("The most recent week is still in progress.")

# --- account breakdown (the heat-map ask, per rep) ---------------------------
st.subheader("Where the touchpoints went — accounts (%s)" % label)
st.caption(ui.NO_ACCOUNT_NOTE + " Meetings are not included here (can't be tied to accounts yet).")
acc = db.q(queries.REP_ACCOUNTS, (start, end, rep))
if acc.empty:
    st.info("No account-level activity in this window.")
    st.stop()

total_tp = int(acc.touchpoints.sum())
acc["pct_of_touchpoints"] = (acc.touchpoints / total_tp * 100).round(1)

top = acc[acc.account_name != "(no account matched)"].head(20)
st.altair_chart(
    alt.Chart(top).mark_bar().encode(
        x=alt.X("touchpoints:Q", title="Touchpoints"),
        y=alt.Y("account_name:N", sort="-x", title=None),
        color=alt.Color("icp_tier:N", title="ICP tier"),
        tooltip=["account_name", "icp_tier", "touchpoints", "people_touched"],
    ).properties(height=max(120, 22 * len(top)), title="Top accounts"),
    use_container_width=True)

st.dataframe(
    acc, hide_index=True, use_container_width=True,
    column_config={
        "account_name": st.column_config.TextColumn("Account", pinned=True),
        "icp_tier": "ICP tier",
        "owned_by_this_rep": st.column_config.CheckboxColumn("Owned by this rep"),
        "touchpoints": st.column_config.NumberColumn("Touchpoints", help=ui.DEFS["touchpoints"]),
        "people_touched": "People touched",
        "pct_of_touchpoints": st.column_config.NumberColumn("% of touchpoints", format="%.1f%%"),
    })

# --- drill to people at one account ------------------------------------------
st.subheader("People at one account")
target = st.selectbox("Account", acc.account_name.tolist())
ppl = db.q(queries.ACCOUNT_CONTACTS, (start, end, rep, target))
if ppl.empty:
    st.info("No person-level rows here (activity logged without a contact).")
else:
    st.dataframe(ppl, hide_index=True, use_container_width=True)
