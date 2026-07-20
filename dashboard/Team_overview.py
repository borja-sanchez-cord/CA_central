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
rh = db.q(queries.MEETINGS_RH, (start, end))
rh_total = int(rh.rh.sum()) if len(rh) else 0

ui.kpi_row([
    {"label": "Activities", "value": int(sc.total_counted.sum()), "help": ui.DEFS["total_counted"]},
    {"label": "Emails", "value": int(sc.emails.sum()),
     "sub": "%d auto / %d manual" % (sc.auto_email.sum(), sc.manual_email.sum()),
     "help": ui.DEFS["emails"]},
    {"label": "Dials", "value": int(sc.dials.sum()),
     "sub": "%d conversations" % sc.conversations.sum(), "help": ui.DEFS["conversations"]},
    {"label": "LinkedIn", "value": int(sc.linkedin.sum()), "help": ui.DEFS["linkedin"]},
    {"label": "Inbound", "value": int(sc.inbound_replies.sum()),
     "help": ui.DEFS["inbound_replies"]},
    {"label": "Meetings", "value": int(sc.meetings_booked.sum()),
     "sub": "%d held / %d canc / %d unk | %d via Revenue Hero" % (
         sc.meetings_held.sum(), sc.meetings_canceled.sum(),
         sc.meetings_unknown.sum(), rh_total),
     "help": ui.DEFS["meetings_booked"] + " 'Via Revenue Hero' = auto-booked by the "
             "inbound scheduler — still counted today."},
    {"label": "Other", "value": int(sc.other_outreach.sum()), "help": ui.DEFS["other"]},
])
st.write("")

# --- per-rep scorecard table: red->green heatmap per column ------------------
st.subheader("Every CA, side by side")
cols_int = ["total_counted", "auto_email", "manual_email", "emails", "dials",
            "pursuits", "conversations", "linkedin", "inbound_replies", "other_outreach",
            "meetings_booked", "meetings_held", "meetings_canceled",
            "meetings_scheduled", "meetings_unknown", "accounts_touched",
            "contacts_touched", "accounts_owned", "owned_touched"]
show = sc[["ca_name"] + cols_int + ["coverage_pct"]].copy()
for c in cols_int:
    show[c] = show[c].fillna(0).astype(int)
# direction-aware: canceled/unlogged meetings are BAD when high; owning more
# accounts is neither good nor bad, so it stays unshaded.
good = [c for c in cols_int + ["coverage_pct"]
        if c not in ("meetings_canceled", "meetings_unknown", "accounts_owned")]
bad = ["meetings_canceled", "meetings_unknown"]
st.dataframe(
    ui.heat_styler(show, good, bad), hide_index=True, use_container_width=True, height=640,
    column_config={
        "ca_name": st.column_config.TextColumn("CA", pinned=True),
        "total_counted": st.column_config.NumberColumn("Activities", help=ui.DEFS["total_counted"]),
        "auto_email": st.column_config.NumberColumn("Auto email", help=ui.DEFS["auto_email"]),
        "manual_email": st.column_config.NumberColumn("Manual email", help=ui.DEFS["manual_email"]),
        "emails": st.column_config.NumberColumn("Emails", help=ui.DEFS["emails"]),
        "dials": st.column_config.NumberColumn("Dials", help=ui.DEFS["dials"]),
        "pursuits": st.column_config.NumberColumn("Pursuits", help=ui.DEFS["pursuits"]),
        "conversations": st.column_config.NumberColumn("Convos", help=ui.DEFS["conversations"]),
        "linkedin": st.column_config.NumberColumn("LinkedIn", help=ui.DEFS["linkedin"]),
        "inbound_replies": st.column_config.NumberColumn("Inbound", help=ui.DEFS["inbound_replies"]),
        "other_outreach": st.column_config.NumberColumn("Other", help=ui.DEFS["other"]),
        "meetings_booked": st.column_config.NumberColumn("Mtg booked", help=ui.DEFS["meetings_booked"]),
        "meetings_held": st.column_config.NumberColumn("Mtg held"),
        "meetings_canceled": st.column_config.NumberColumn("Mtg canceled"),
        "meetings_scheduled": st.column_config.NumberColumn("Mtg sched"),
        "meetings_unknown": st.column_config.NumberColumn("Mtg unknown", help=ui.DEFS["meetings_unknown"]),
        "accounts_touched": st.column_config.NumberColumn("Accts touched", help=ui.DEFS["accounts_touched"]),
        "contacts_touched": st.column_config.NumberColumn("People touched", help=ui.DEFS["contacts_touched"]),
        "accounts_owned": st.column_config.NumberColumn("Owned", help=ui.DEFS["accounts_owned"]),
        "owned_touched": st.column_config.NumberColumn("Owned touched", help=ui.DEFS["owned_touched"]),
        "coverage_pct": st.column_config.NumberColumn("Coverage", format="%d%%", help=ui.DEFS["coverage_pct"]),
    })

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
                                        range=["#A3BE8C", "#7DA0CA", "#CC7A6F", "#6B7280"])),
        tooltip=["ca_name", "status", "count"],
    ).properties(height=26 * n)),
    use_container_width=True)
st.caption("Unknown = no outcome logged. Never assume held.")
