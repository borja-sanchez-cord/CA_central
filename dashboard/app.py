"""CA Activity Dashboard — Team overview (landing page)."""
import altair as alt
import streamlit as st

import db
import queries
import ui

first, last = ui.page_setup("Team overview")
start, end, label = ui.window_picker(first, last)

sc = db.q(queries.SCORECARD, (start, end, ))
n = len(sc)

st.subheader("%s (%s → %s) — %d CAs" % (label, start, end, n))

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Total counted", int(sc.total_counted.sum()), help=ui.DEFS["total_counted"])
c2.metric("Emails", int(sc.emails.sum()), help=ui.DEFS["emails"])
c3.metric("Dials", int(sc.dials.sum()), help=ui.DEFS["dials"])
c4.metric("Conversations", int(sc.conversations.sum()), help=ui.DEFS["conversations"])
c5.metric("LinkedIn", int(sc.linkedin.sum()), help=ui.DEFS["linkedin"])
c6.metric("Meetings booked", int(sc.meetings_booked.sum()), help=ui.DEFS["meetings_booked"])
st.caption(ui.MEETINGS_NOTE)

# --- per-rep scorecard table ------------------------------------------------
show = sc[["ca_name", "total_counted", "auto_email", "manual_email", "emails",
           "dials", "pursuits", "conversations", "linkedin", "inbound_replies",
           "meetings_booked", "meetings_held", "meetings_canceled",
           "meetings_scheduled", "meetings_unknown", "accounts_touched",
           "contacts_touched", "accounts_owned", "owned_touched", "coverage_pct"]]
st.dataframe(
    show, hide_index=True, use_container_width=True,
    column_config={
        "ca_name": st.column_config.TextColumn("CA", pinned=True),
        "coverage_pct": st.column_config.NumberColumn(
            "Coverage %", format="%.0f%%", help=ui.DEFS["coverage_pct"]),
        **{k: st.column_config.NumberColumn(
               k.replace("_", " ").title(), help=ui.DEFS.get(k))
           for k in show.columns if k not in ("ca_name", "coverage_pct")},
    })

# --- channel mix ------------------------------------------------------------
st.subheader("Channel mix per CA")
mix_cols = ["auto_email", "manual_email", "dials", "linkedin", "other_outreach"]
mix = sc[["ca_name"] + mix_cols].melt("ca_name", var_name="channel", value_name="count")
st.altair_chart(
    alt.Chart(mix).mark_bar().encode(
        x=alt.X("count:Q", title="Counted outbound activities"),
        y=alt.Y("ca_name:N", sort="-x", title=None),
        color=alt.Color("channel:N", title="Channel"),
        tooltip=["ca_name", "channel", "count"],
    ).properties(height=26 * n),
    use_container_width=True)

# --- meetings split ---------------------------------------------------------
st.subheader("Meetings booked — outcome split")
m_cols = ["meetings_held", "meetings_canceled", "meetings_scheduled", "meetings_unknown"]
mm = sc[["ca_name"] + m_cols].melt("ca_name", var_name="status", value_name="count")
mm["status"] = mm["status"].str.replace("meetings_", "")
st.altair_chart(
    alt.Chart(mm).mark_bar().encode(
        x=alt.X("count:Q", title="Meetings"),
        y=alt.Y("ca_name:N", sort="-x", title=None),
        color=alt.Color("status:N", title="Outcome",
                        scale=alt.Scale(domain=["held", "scheduled", "canceled", "unknown"],
                                        range=["#2ca02c", "#1f77b4", "#d62728", "#c7c7c7"])),
        tooltip=["ca_name", "status", "count"],
    ).properties(height=26 * n),
    use_container_width=True)
st.caption("“Unknown” means no outcome was logged — never assume held. "
           "Pick a CA in **Rep detail** (left sidebar) to drill in.")
