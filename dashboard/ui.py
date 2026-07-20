"""
Shared page furniture: password gate, window picker, data-as-of stamp, and
the measure definitions (verbatim-faithful shortenings of docs/ontology.md —
the dashboard invents no definitions).
"""
import datetime as dt
import os

import streamlit as st

import db
import queries

# One-line definitions surfaced as tooltips/captions. Source: docs/ontology.md.
DEFS = {
    "auto_email": "Automated email — a sequence tool (AmpleMarket/Apollo) sent it on the rep's behalf.",
    "manual_email": "Manual email — a human actually wrote and sent it (best-effort label; see ontology note).",
    "emails": "Automated + manual emails added together.",
    "inbound_replies": "Emails prospects sent to the CA (bounces/auto-replies removed).",
    "dials": "Phone dials from AmpleMarket's dialer, answered or not. Dials outside it are invisible.",
    "pursuits": "One person chased by phone — repeated dials within 30 min bundle into one pursuit.",
    "conversations": "Dials where a real human answered (the tool's own flag).",
    "linkedin": "LinkedIn steps completed inside AmpleMarket sequences ONLY — native LinkedIn is not captured.",
    "other_outreach": "Sequence steps of unrecognised type (could be WhatsApp, research, anything).",
    "meetings_booked": "Every meeting object in the window — booked, NOT held. Always read with the split below.",
    "meetings_held": "Meetings marked COMPLETED. Only ~20% get an outcome logged — a floor, not the truth.",
    "meetings_canceled": "Meetings marked CANCELED.",
    "meetings_scheduled": "Upcoming meetings (SCHEDULED/RESCHEDULED).",
    "meetings_unknown": "Booked with no outcome logged — usually the biggest bucket. Never assume held.",
    "total_counted": "Every counted activity for the rep, each counted once.",
    "accounts_touched": "Distinct companies with at least one counted activity (misses the ~60% of activity with no matched company).",
    "contacts_touched": "Distinct people with at least one counted activity (same ~60% caveat).",
    "contacts_per_account": "Contacts touched ÷ accounts touched — a depth (multi-threading) signal.",
    "accounts_owned": "Companies where this rep is the HubSpot target-account owner.",
    "owned_touched": "Of the rep's owned accounts, how many they PERSONALLY touched in the window.",
    "coverage_pct": "Owned touched ÷ accounts owned — likely a slight under-count, compare reps / watch trend.",
    "touchpoints": "Counted activities filed under that account/person. Meetings excluded (can't be tied to accounts yet).",
    "saos": "SAOs achieved (from Ray's Global CA Performance Tracker, monthly).",
    "sao_target": "That rep's monthly SAO target (Ray's tracker).",
    "saos_outbound": "SAOs minus inbound minus event — the ones outbound activity can claim.",
}

MEETINGS_NOTE = ("Meetings are **booked, not held** — outcome is logged on only ~20%, "
                 "so the booked/held/canceled/scheduled/unknown split is always shown together.")
NO_ACCOUNT_NOTE = ("~60% of counted activity (mostly LinkedIn/calls) has no matched company; "
                   "it is shown as an explicit “(no account matched)” row, so totals still reconcile.")


def page_setup(title, icon="📊"):
    st.set_page_config(page_title=title, page_icon=icon, layout="wide")
    pw = os.environ.get("DASHBOARD_PASSWORD")
    if pw and not st.session_state.get("_authed"):
        st.title("CA Activity Dashboard")
        entered = st.text_input("Password", type="password")
        if entered == pw:
            st.session_state["_authed"] = True
            st.rerun()
        st.stop()
    st.title(title)
    first, last = data_range()
    st.caption("Data as of **%s** (updates each morning with the previous day). "
               "History starts **%s** — there is no earlier activity data. "
               "Definitions: docs/ontology.md." % (last, first))
    return first, last


def data_range():
    df = db.q(queries.DATA_RANGE)
    return df.iloc[0]["first_day"], df.iloc[0]["last_day"]


def window_picker(first, last, key="win"):
    """Sidebar window picker -> (start, end, label)."""
    choice = st.sidebar.radio(
        "Time window", ["Last 7 days", "Last 30 days", "All time", "Custom"], key=key)
    if choice == "Last 7 days":
        start = last - dt.timedelta(days=6)
        return max(start, first), last, choice
    if choice == "Last 30 days":
        start = last - dt.timedelta(days=29)
        return max(start, first), last, choice
    if choice == "All time":
        return first, last, choice
    val = st.sidebar.date_input(
        "Custom range", (first, last), min_value=first, max_value=last, key=key + "_c")
    if not (isinstance(val, tuple) and len(val) == 2):
        st.info("Pick both ends of the range in the sidebar.")
        st.stop()
    return val[0], val[1], "%s → %s" % val
