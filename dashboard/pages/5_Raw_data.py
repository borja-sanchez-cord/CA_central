"""Raw data — the live activity log behind every number (read-only)."""
import re

import pandas as pd
import streamlit as st

import db
import queries
import ui

first, last = ui.setup(
    "Raw data",
    "The live, read-only view of the activity database — one row per real activity. "
    "Every number on every other tab can be traced to rows here.")

# A chart drill-through hands off here with its slice (ui.drill_card): land
# pre-filtered to the exact window/CA/channel the clicked mark drew. Session
# keys must be set BEFORE the widgets below are instantiated.
pf = st.session_state.pop("raw_prefill", None)
if pf:
    st.session_state["win"] = "Custom"
    st.session_state["win_a"] = max(pd.Timestamp(pf["start"]).date(), first)
    st.session_state["win_b"] = min(pd.Timestamp(pf["end"]).date(), last)
    st.session_state["raw_search"] = pf.get("search", "")

start, end, label = ui.window_pills(first, last)

reps = ["(all)"] + db.q(queries.REPS)["name"].tolist()
channels = ["(all)"] + db.q(queries.CHANNELS)["channel"].tolist()
if pf:   # only prefill values the selectboxes actually offer
    if pf.get("rep") in reps:
        st.session_state["raw_rep"] = pf["rep"]
    if pf.get("channel") in channels:
        st.session_state["raw_channel"] = pf["channel"]

c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.6, 1.2])
rep = c1.selectbox("CA", reps, key="raw_rep")
channel = c2.selectbox("Channel", channels, key="raw_channel",
                       format_func=lambda c: ui.CHANNEL_LABELS.get(c, c))
counted = c3.radio("Show", ["counted", "excluded", "all"], horizontal=True,
                   format_func={"counted": "Counted (in the numbers)",
                                "excluded": "Not counted (kept for audit)",
                                "all": "All rows"}.get)
search = c4.text_input("Subject / account contains", key="raw_search")

_COUNTED_HELP = (
    "Nothing is ever deleted — every raw tool record lands in exactly one row here. "
    "Counted in reports = real CA activity (the rows every other tab adds up). "
    "Excluded (kept for audit) = rows that would double-count or aren't CA outreach "
    "(duplicate to-do shadows, bounces, calendar invites, non-CA senders), kept "
    "visible with the exact reason so any number can be audited.")
tot = db.q(queries.AUDIT_COUNT, (start, end, rep, rep, channel, channel)).iloc[0]
st.markdown(
    '<span class="pill lime"><b>%d</b> counted in reports</span>'
    '<span class="pill purple"><b>%d</b> excluded — kept for audit</span>'
    % (tot.counted, tot.excluded), unsafe_allow_html=True)
st.caption("What do counted / excluded mean?", help=_COUNTED_HELP)

like = "%%%s%%" % search if search else ""
rows = db.q(queries.AUDIT_ROWS,
            (start, end, rep, rep, channel, channel,
             counted, counted, counted, search, like, like))

disp = rows.drop(columns=["activity_id"]).copy()
# meetings carry a default direction stamp that means nothing — show n/a
disp.loc[disp.channel == "meeting", "direction"] = "n/a"
disp["channel"] = disp["channel"].map(lambda c: ui.CHANNEL_LABELS.get(c, c))
# what a sales leader scans for first, left to right; plumbing last
disp = disp[["activity_date", "ca_name", "channel", "account_name", "subject",
             "contact_email", "counts", "excluded_reason", "occurred_at",
             "direction", "is_automated", "dup_count", "logged_by", "source"]]
event = st.dataframe(
    disp, hide_index=True, use_container_width=True, height=380,
    on_select="rerun", selection_mode="single-row", key="raw_rows",
    column_config={
        "activity_date": st.column_config.DateColumn("Date"),
        "ca_name": st.column_config.TextColumn("CA"),
        "channel": "Channel",
        "account_name": "Account",
        "subject": "Subject",
        "contact_email": "Contact",
        "counts": st.column_config.CheckboxColumn("Counted"),
        "excluded_reason": "Why excluded",
        "occurred_at": st.column_config.DatetimeColumn("Exact time"),
        "is_automated": st.column_config.CheckboxColumn("Automated"),
        "dup_count": st.column_config.NumberColumn(
            "Copies", help="Duplicate tool records collapsed into this one row."),
        "logged_by": st.column_config.ListColumn("Logged by"),
    })
if len(rows) == 500:
    st.caption("Showing the most recent 500 — narrow the filters for the rest.")

# --- click a row -> full detail ------------------------------------------------
sel = event.selection.rows if event and event.selection else []
if not sel:
    ui.pill("Click any row above to inspect it — full detail, incl. the email body, appears here")
    st.stop()

detail = db.q(queries.AUDIT_DETAIL, (rows.iloc[sel[0]]["activity_id"],)).iloc[0]

st.subheader("This activity, in full")
left, right = st.columns(2)
with left:
    for f in ["occurred_at", "activity_date", "ca_name", "ca_email", "channel",
              "direction", "is_automated", "automated_confidence", "counts",
              "excluded_reason", "source", "logged_by", "dup_count"]:
        val = detail[f]
        if f == "direction" and detail["channel"] == "meeting":
            val = "n/a (meetings aren't classified inbound/outbound)"
        st.write("**%s:** %s" % (f, val))
with right:
    for f in ["subject", "account_name", "account_domain", "account_icp_tier_validated",
              "contact_email", "contact_firstname", "contact_lastname",
              "contact_jobtitle", "outcome", "call_group_id", "is_conversation"]:
        st.write("**%s:** %s" % (f, detail[f]))

st.write("**Raw source record ids** (`source_ids` — the tool records collapsed into this row):")
st.code(str(detail["source_ids"]))

if detail["direction"] == "inbound":
    st.info("Inbound reply — these are a prospect's words. Internal use only.")
body = detail["body_preview"]
if not body and detail["body_html"]:
    body = re.sub(r"<[^>]+>", " ", detail["body_html"])
    body = re.sub(r"\s+", " ", body).strip()
if body:
    st.text_area("Email body", body, height=260, disabled=True)
elif detail["channel"].startswith("li_"):
    st.caption("LinkedIn message text is not captured (AmpleMarket v2 webhook — future).")
else:
    st.caption("No body stored for this row (non-email channel, or empty).")
