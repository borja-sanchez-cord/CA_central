"""Team overview — the landing page."""
import altair as alt
import streamlit as st

import db
import queries
import ui

first, last = ui.setup(
    "Team overview",
    "How much outreach the whole team did, channel by channel, every CA side by side.")
start, end, label = ui.window_pills(first, last)

sc = db.q(queries.SCORECARD, (start, end))
n = len(sc)

ui.kpi_row([
    {"label": "Activities", "value": int(sc.total_counted.sum()),
     "help": ui.DEFS["total_counted"]},
    {"label": "Emails", "value": int(sc.emails.sum()),
     "sub": "%d auto · %d manual" % (sc.auto_email.sum(), sc.manual_email.sum()),
     "help": ui.DEFS["emails"]},
    {"label": "Dials", "value": int(sc.dials.sum()),
     "sub": "%d conversations" % sc.conversations.sum(),
     "help": ui.DEFS["conversations"]},
    {"label": "LinkedIn", "value": int(sc.linkedin.sum()),
     "help": ui.DEFS["linkedin"]},
    {"label": "Inbound replies", "value": int(sc.inbound_replies.sum()),
     "help": ui.DEFS["inbound_replies"]},
    {"label": "Meetings booked", "value": int(sc.meetings_booked.sum()),
     "sub": "%d held · %d canceled · %d unknown" % (
         sc.meetings_held.sum(), sc.meetings_canceled.sum(), sc.meetings_unknown.sum()),
     "help": ui.DEFS["meetings_booked"]},
])

st.write("")

# --- per-rep scorecard table (columns tinted by channel family) --------------
st.subheader("Every CA, side by side")
show = sc[["ca_name", "total_counted", "auto_email", "manual_email", "emails",
           "dials", "pursuits", "conversations", "linkedin", "inbound_replies",
           "meetings_booked", "meetings_held", "meetings_canceled",
           "meetings_scheduled", "meetings_unknown", "accounts_touched",
           "contacts_touched", "accounts_owned", "owned_touched", "coverage_pct"]]
FAMS = {"auto_email": "#B3E249", "manual_email": "#B3E249", "emails": "#B3E249",
        "dials": "#E4574F", "pursuits": "#E4574F", "conversations": "#E4574F",
        "linkedin": "#5B8DEF", "inbound_replies": "#E8A33D",
        "meetings_booked": "#8A55F7", "meetings_held": "#8A55F7",
        "meetings_canceled": "#8A55F7", "meetings_scheduled": "#8A55F7",
        "meetings_unknown": "#8A55F7"}
styled = show.style.apply(ui.family_tints(show.columns, FAMS), axis=None) \
                   .format(precision=0, na_rep="")
st.dataframe(
    styled, hide_index=True, use_container_width=True,
    column_config={
        "ca_name": st.column_config.TextColumn("CA", pinned=True),
        "total_counted": st.column_config.NumberColumn("Activities", help=ui.DEFS["total_counted"]),
        "auto_email": st.column_config.NumberColumn("Auto", help=ui.DEFS["auto_email"]),
        "manual_email": st.column_config.NumberColumn("Manual", help=ui.DEFS["manual_email"]),
        "emails": st.column_config.NumberColumn("Emails", help=ui.DEFS["emails"]),
        "dials": st.column_config.NumberColumn("Dials", help=ui.DEFS["dials"]),
        "pursuits": st.column_config.NumberColumn("Pursuits", help=ui.DEFS["pursuits"]),
        "conversations": st.column_config.NumberColumn("Convos", help=ui.DEFS["conversations"]),
        "linkedin": st.column_config.NumberColumn("LinkedIn", help=ui.DEFS["linkedin"]),
        "inbound_replies": st.column_config.NumberColumn("Inbound", help=ui.DEFS["inbound_replies"]),
        "meetings_booked": st.column_config.NumberColumn("Booked", help=ui.DEFS["meetings_booked"]),
        "meetings_held": st.column_config.NumberColumn("Held"),
        "meetings_canceled": st.column_config.NumberColumn("Canceled"),
        "meetings_scheduled": st.column_config.NumberColumn("Scheduled"),
        "meetings_unknown": st.column_config.NumberColumn("Unknown", help=ui.DEFS["meetings_unknown"]),
        "accounts_touched": st.column_config.NumberColumn("Accts touched", help=ui.DEFS["accounts_touched"]),
        "contacts_touched": st.column_config.NumberColumn("People touched", help=ui.DEFS["contacts_touched"]),
        "accounts_owned": st.column_config.NumberColumn("Owned", help=ui.DEFS["accounts_owned"]),
        "owned_touched": st.column_config.NumberColumn("Owned touched", help=ui.DEFS["owned_touched"]),
        "coverage_pct": st.column_config.ProgressColumn(
            "Coverage %", format="%.0f%%", min_value=0, max_value=100,
            help=ui.DEFS["coverage_pct"]),
    })
st.caption("Column colours: 🟢 email · 🔴 phone · 🔵 LinkedIn · 🟠 inbound · 🟣 meetings. "
           "Hover any header for its exact definition.")

# --- channel mix ------------------------------------------------------------
st.subheader("Channel mix per CA")
mix_cols = ["auto_email", "manual_email", "dials", "linkedin", "other_outreach"]
mix = sc[["ca_name"] + mix_cols].melt("ca_name", var_name="m", value_name="count")
mix["channel"] = mix.m.map(ui.MEASURE_LABELS)
st.altair_chart(ui.themed(
    alt.Chart(mix).mark_bar().encode(
        x=alt.X("count:Q", title="Counted outbound activities"),
        y=alt.Y("ca_name:N", sort="-x", title=None),
        color=alt.Color("channel:N", title=None,
                        scale=alt.Scale(domain=[ui.MEASURE_LABELS[c] for c in mix_cols],
                                        range=[ui.MEASURE_COLORS[c] for c in mix_cols])),
        tooltip=["ca_name", "channel", "count"],
    ).properties(height=26 * n)),
    use_container_width=True)

# --- meetings split ---------------------------------------------------------
st.subheader("Meetings booked — what happened to them")
m_cols = ["meetings_held", "meetings_scheduled", "meetings_canceled", "meetings_unknown"]
mm = sc[["ca_name"] + m_cols].melt("ca_name", var_name="status", value_name="count")
mm["status"] = mm["status"].str.replace("meetings_", "")
st.altair_chart(ui.themed(
    alt.Chart(mm).mark_bar().encode(
        x=alt.X("count:Q", title="Meetings"),
        y=alt.Y("ca_name:N", sort="-x", title=None),
        color=alt.Color("status:N", title=None,
                        scale=alt.Scale(domain=["held", "scheduled", "canceled", "unknown"],
                                        range=[ui.LIME, "#5B8DEF", "#E4574F", "#565B66"])),
        tooltip=["ca_name", "status", "count"],
    ).properties(height=26 * n)),
    use_container_width=True)
st.caption("“Unknown” = no outcome logged (that's most of them — reps log outcomes on ~20%). "
           "Never assume unknown means held.")
