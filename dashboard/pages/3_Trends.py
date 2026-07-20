"""Weekly trends — the “is coaching working?” screen. Gets better every week."""
import altair as alt
import streamlit as st

import db
import queries
import ui

first, last = ui.page_setup("Trends", "📈")

MEASURES = ["total_counted", "emails", "auto_email", "manual_email", "dials",
            "pursuits", "conversations", "linkedin", "inbound_replies",
            "meetings_booked", "accounts_touched", "contacts_touched", "coverage_pct"]

measure = st.selectbox("Measure", MEASURES,
                       format_func=lambda m: m.replace("_", " ").title())
if ui.DEFS.get(measure):
    st.caption(ui.DEFS[measure])

wk = db.q(queries.WEEKLY_TREND)
reps = sorted(wk.ca_name.unique())
sel = st.multiselect("CAs (empty = whole team)", reps)

if sel:
    d = wk[wk.ca_name.isin(sel)]
    chart = alt.Chart(d).mark_line(point=True).encode(
        x=alt.X("week_start:T", title="Week (Mon start)"),
        y=alt.Y("%s:Q" % measure, title=None),
        color=alt.Color("ca_name:N", title=None),
        tooltip=["week_start:T", "ca_name", measure])
else:
    if measure == "coverage_pct":
        st.info("Coverage % is per-rep — pick CAs above (a team average would mislead).")
        st.stop()
    d = wk.groupby("week_start", as_index=False)[measure].sum()
    chart = alt.Chart(d).mark_line(point=True).encode(
        x=alt.X("week_start:T", title="Week (Mon start)"),
        y=alt.Y("%s:Q" % measure, title=None),
        tooltip=["week_start:T", measure])

st.altair_chart(chart.properties(height=340), use_container_width=True)
st.caption("History starts %s; the most recent week is partial until Sunday. "
           "Weekly cells are the verified scorecard run for that week — nothing recomputed." % first)

with st.expander("Table — weeks × CAs"):
    pv = wk.pivot_table(index="ca_name", columns="week_start", values=measure)
    st.dataframe(pv, use_container_width=True)
