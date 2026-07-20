"""
Design system + shared page furniture (visual layer ONLY — every number on
screen still comes from the same queries; nothing here computes data).

Direction (PM, 2026-07-20): dark mode; two accents only — Encord purple
rgb(98,35,233) for everything interactive/selected, lime rgb(179,226,73) for
outcomes/positives. Channels are colour-coded BY FAMILY so any chart or table
reads at a glance: greens = email, reds = phone, blues = LinkedIn,
purple = meetings, amber = inbound replies, grey = other. Everything lives in
cards and pills, never bare text; timeframes live in the window pills, never
in titles.
"""
import datetime as dt
import html
import os

import pandas as pd
import streamlit as st

import db
import queries

PURPLE = "#6223E9"
LIME = "#B3E249"
BG_CARD = "#191C23"
BORDER = "#262B36"
TEXT_DIM = "#9AA1AF"

# --- channel colour system (family-grouped) ---------------------------------
CHANNEL_COLORS = {
    "auto_email": "#B3E249",      # email family: greens
    "manual_email": "#5E9C33",
    "call": "#E4574F",            # phone family: reds
    "li_connect": "#5B8DEF",      # linkedin family: blues
    "li_message": "#2F5FC4",
    "li_other": "#9DBEFF",
    "inbound_email": "#E8A33D",   # prospect engagement: amber
    "meeting": "#8A55F7",         # meetings: purple family
    "other": "#8A8F9C",
}
CHANNEL_LABELS = {
    "auto_email": "Automated email",
    "manual_email": "Manual email",
    "call": "Dial",
    "li_connect": "LinkedIn connect",
    "li_message": "LinkedIn message",
    "li_other": "LinkedIn other",
    "inbound_email": "Inbound reply",
    "meeting": "Meeting",
    "other": "Other",
}
# same families, keyed by scorecard column names (for melted chart data)
MEASURE_COLORS = {
    "auto_email": "#B3E249", "manual_email": "#5E9C33", "emails": "#8CC63F",
    "dials": "#E4574F", "conversations": "#A32C26", "pursuits": "#C74841",
    "linkedin": "#5B8DEF", "li_connect": "#5B8DEF", "li_message": "#2F5FC4",
    "li_other": "#9DBEFF", "inbound_replies": "#E8A33D",
    "meetings_booked": "#8A55F7", "other_outreach": "#8A8F9C",
}
MEASURE_LABELS = {
    "auto_email": "Automated emails", "manual_email": "Manual emails",
    "emails": "Emails", "dials": "Dials", "pursuits": "Pursuits",
    "conversations": "Conversations", "linkedin": "LinkedIn",
    "inbound_replies": "Inbound replies", "meetings_booked": "Meetings booked",
    "other_outreach": "Other", "total_counted": "Activities",
    "accounts_touched": "Accounts touched", "contacts_touched": "Contacts touched",
    "coverage_pct": "Coverage %",
}

# One-line definitions (verbatim-faithful shortenings of docs/ontology.md).
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
    "meetings_booked": "Every meeting in the window — booked, NOT held. Outcome is logged on only ~20%, so always read with the held/canceled/scheduled/unknown split.",
    "meetings_unknown": "Booked with no outcome logged — usually the biggest bucket. Never assume held.",
    "total_counted": "Every counted activity for the rep, each counted once.",
    "accounts_touched": "Distinct companies with at least one counted activity (misses the ~60% of activity with no matched company).",
    "contacts_touched": "Distinct people with at least one counted activity (same ~60% caveat).",
    "accounts_owned": "Companies where this rep is the HubSpot target-account owner.",
    "owned_touched": "Of the rep's owned accounts, how many they PERSONALLY touched in the window.",
    "coverage_pct": "Owned touched ÷ accounts owned — likely a slight under-count; compare reps, watch the trend.",
    "touchpoints": "Counted activities filed under that account/person. Meetings excluded (can't be tied to accounts yet).",
    "saos": "SAOs achieved (from Ray's Global CA Performance Tracker, monthly).",
    "sao_target": "That rep's monthly SAO target (Ray's tracker).",
    "saos_outbound": "SAOs minus inbound minus event — the ones outbound activity can claim.",
}

_CSS = """
<style>
.block-container {padding-top: 2.2rem; max-width: 1250px;}
h1 {font-size: 1.7rem !important; letter-spacing: -.02em;}
h3 {letter-spacing: -.01em;}

.kpi {background:%(card)s; border:1px solid %(border)s; border-radius:14px;
      padding:14px 16px 12px; height:100%%;}
.kpi .lbl {font-size:.72rem; color:%(dim)s; text-transform:uppercase;
           letter-spacing:.07em; margin-bottom:2px;}
.kpi .val {font-size:1.85rem; font-weight:700; color:#F2F3F6; line-height:1.15;}
.kpi .sub {font-size:.78rem; color:%(dim)s; margin-top:2px;}

.pill {display:inline-flex; align-items:center; gap:8px; background:%(card)s;
       border:1px solid %(border)s; border-radius:999px; padding:5px 14px;
       font-size:.8rem; color:#D7DAE0; margin-right:8px; margin-bottom:6px;
       white-space:nowrap;}
.pill.lime {border-color:%(lime)s;}
.pill.purple {border-color:%(purple)s;}
.pill.red {border-color:#E4574F;}
.pill b {color:#FFF; font-weight:600;}

.dot {width:8px; height:8px; border-radius:50%%; background:#FF4B4B;
      animation: blink 1.5s ease-in-out infinite; flex:none;}
.dot.lime {background:%(lime)s;}
@keyframes blink {50%% {opacity:.15;}}

.refresh-banner {display:flex; align-items:center; gap:10px;
  background:rgba(179,226,73,.10); border:1px solid %(lime)s; border-radius:12px;
  padding:10px 16px; margin:4px 0 14px; color:#E9EAEE; font-size:.9rem;}

.explain {color:%(dim)s; font-size:.95rem; margin:-6px 0 10px;}
</style>
""" % {"card": BG_CARD, "border": BORDER, "dim": TEXT_DIM,
       "lime": LIME, "purple": PURPLE}


def setup(title, explainer, icon="📊"):
    """Page scaffold: title, one-line explainer, live last-update pill,
    active-refresh banner. Returns (first_day, last_day) of the data."""
    st.set_page_config(page_title=title, page_icon=icon, layout="wide")
    st.markdown(_CSS, unsafe_allow_html=True)

    pw = os.environ.get("DASHBOARD_PASSWORD")
    if pw and not st.session_state.get("_authed"):
        st.title("CA Activity Dashboard")
        if st.text_input("Password", type="password") == pw:
            st.session_state["_authed"] = True
            st.rerun()
        st.stop()

    left, right = st.columns([3, 1.4])
    with left:
        st.title(title)
        st.markdown('<p class="explain">%s</p>' % html.escape(explainer),
                    unsafe_allow_html=True)
    with right:
        st.markdown(_status_pills(), unsafe_allow_html=True)

    run = db.q(queries.RUN_ACTIVE)
    if len(run):
        st.markdown(
            '<div class="refresh-banner"><span class="dot lime"></span>'
            "The data is being refreshed right now (started %s UTC) — "
            "numbers may move for a few minutes.</div>"
            % run.iloc[0]["started_at"].strftime("%H:%M"),
            unsafe_allow_html=True)

    return data_range()


def _status_pills():
    lr = db.q(queries.LAST_RUN)
    if len(lr) and lr.iloc[0]["finished_at"] is not None:
        ts = lr.iloc[0]["finished_at"].strftime("%a %d %b, %H:%M UTC")
    else:
        ts = "unknown"
    return ('<div style="text-align:right; padding-top:14px;">'
            '<span class="pill"><span class="dot"></span>'
            "Last update: <b>%s</b></span></div>" % ts)


def data_range():
    df = db.q(queries.DATA_RANGE)
    return df.iloc[0]["first_day"], df.iloc[0]["last_day"]


def window_pills(first, last, key="win"):
    """In-tab time window selector (pills). Returns (start, end, label)."""
    choice = st.pills("Time window",
                      ["Last 7 days", "Last 30 days", "All time", "Custom"],
                      default="Last 7 days", key=key,
                      label_visibility="collapsed")
    choice = choice or "Last 7 days"
    if choice == "Last 7 days":
        return max(last - dt.timedelta(days=6), first), last, choice
    if choice == "Last 30 days":
        return max(last - dt.timedelta(days=29), first), last, choice
    if choice == "All time":
        return first, last, choice
    c1, c2, _ = st.columns([1, 1, 3])
    start = c1.date_input("From", first, min_value=first, max_value=last, key=key + "_a")
    end = c2.date_input("To", last, min_value=first, max_value=last, key=key + "_b")
    if start > end:
        st.info("Pick a start date on or before the end date.")
        st.stop()
    return start, end, "%s → %s" % (start, end)


def kpi_row(cards):
    """cards: list of dicts {label, value, sub (optional), help (optional)}."""
    cols = st.columns(len(cards))
    for col, c in zip(cols, cards):
        tip = html.escape(c.get("help", ""), quote=True)
        sub = ('<div class="sub">%s</div>' % html.escape(str(c["sub"]))) if c.get("sub") else ""
        col.markdown(
            '<div class="kpi" title="%s"><div class="lbl">%s</div>'
            '<div class="val">%s</div>%s</div>'
            % (tip, html.escape(c["label"]), html.escape(str(c["value"])), sub),
            unsafe_allow_html=True)


def pill(text, color=""):
    """Inline status pill. color in {'', 'lime', 'purple', 'red'}."""
    st.markdown('<span class="pill %s">%s</span>' % (color, text),
                unsafe_allow_html=True)


def themed(chart):
    """Dark-mode Altair chart config (visual only)."""
    return (chart
            .configure(background="transparent")
            .configure_axis(labelColor=TEXT_DIM, titleColor=TEXT_DIM,
                            gridColor="#20242D", domainColor="#20242D",
                            tickColor="#20242D")
            .configure_legend(labelColor="#C9CDD6", titleColor=TEXT_DIM)
            .configure_view(strokeOpacity=0))


def week_label(df, col="week_start"):
    """Ordinal week labels ('Wk of 06 Jul') so the axis never shows fake
    hours/days between real data points."""
    out = df.copy()
    out["week"] = pd.to_datetime(out[col]).dt.strftime("Wk of %d %b")
    return out


def family_tints(columns, families):
    """pandas Styler helper: subtle family background per column.
    families: {column_name: hex}. Returns a function for Styler.apply(axis=None)."""
    def _rgba(h, a=0.16):
        return "background-color: rgba(%d,%d,%d,%.2f)" % (
            int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16), a)
    tint = {c: _rgba(h) for c, h in families.items()}

    def apply(df):
        return pd.DataFrame(
            [[tint.get(c, "") for c in df.columns]] * len(df),
            index=df.index, columns=df.columns)
    return apply
