"""Audit — drill any number down to the raw activities behind it (incl. email bodies).

This page is why the numbers can be trusted: no aggregate is a black box.
Excluded rows are equally visible, with their exact exclusion reason.
"""
import re

import streamlit as st

import db
import queries
import ui

first, last = ui.page_setup("Audit — drill to raw", "🔍")
start, end, label = ui.window_picker(first, last)

reps = ["(all)"] + db.q(queries.REPS)["name"].tolist()
channels = ["(all)"] + db.q(queries.CHANNELS)["channel"].tolist()

c1, c2, c3, c4 = st.columns(4)
rep = c1.selectbox("CA", reps)
channel = c2.selectbox("Channel", channels)
counted = c3.radio("Rows", ["counted", "excluded", "all"], horizontal=True)
search = c4.text_input("Subject / account contains")

tot = db.q(queries.AUDIT_COUNT, (start, end, rep, rep, channel, channel)).iloc[0]
st.caption("In this window/filter: **%d counted** activities and **%d kept-but-excluded** rows "
           "(each with its reason). Every raw tool record lands in exactly one row — "
           "the daily build aborts if that ever breaks." % (tot.counted, tot.excluded))

like = "%%%s%%" % search if search else ""
rows = db.q(queries.AUDIT_ROWS,
            (start, end, rep, rep, channel, channel,
             counted, counted, counted, search, like, like))

st.dataframe(
    rows.drop(columns=["activity_id"]), hide_index=True, use_container_width=True,
    height=380,
    column_config={
        "counts": st.column_config.CheckboxColumn("Counted"),
        "excluded_reason": "Why excluded",
        "dup_count": st.column_config.NumberColumn(
            "Copies", help="How many duplicate tool records were collapsed into this one row."),
        "logged_by": st.column_config.ListColumn("Logged by"),
    })
if len(rows) == 500:
    st.caption("Showing the most recent 500 — narrow the filters for the rest.")

st.subheader("Inspect one activity")
if rows.empty:
    st.info("Nothing matches the filters.")
    st.stop()

labels = {
    aid: "%s — %s — %s — %s" % (r.occurred_at, r.ca_name, r.channel,
                                (r.subject or "(no subject)")[:80])
    for aid, r in zip(rows.activity_id, rows.itertuples())
}
aid = st.selectbox("Pick a row", rows.activity_id.tolist(),
                   format_func=lambda a: labels.get(a, a))
detail = db.q(queries.AUDIT_DETAIL, (aid,)).iloc[0]

left, right = st.columns(2)
with left:
    for f in ["occurred_at", "activity_date", "ca_name", "ca_email", "channel",
              "direction", "is_automated", "automated_confidence", "counts",
              "excluded_reason", "source", "logged_by", "dup_count"]:
        st.write("**%s:** %s" % (f, detail[f]))
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
