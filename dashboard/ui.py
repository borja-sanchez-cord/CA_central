"""
Design system + shared page furniture (visual layer ONLY — every number on
screen still comes from the same queries; nothing here computes data).

Direction (PM, 2026-07-20): dark mode (a touch lighter); monospace; two
accents — Encord purple #6223E9 (interactive/selected) and lime #B3E249
(positives). Channels colour-coded BY FAMILY, muted/soft (greens = email,
reds = phone, blues = LinkedIn, purple = meetings, amber = inbound). Squared
objects, wide tables, no emoji. Timeframes live in the window pills, never in
titles; every tab opens with a one-sentence explainer.
"""
import datetime as dt
import html

import pandas as pd
import streamlit as st

import db
import queries

PURPLE = "#6223E9"
LIME = "#B3E249"
BG_CARD = "#20242E"
BORDER = "#2C313D"
TEXT_DIM = "#98A0AE"

# --- soft/muted channel palette (family-grouped) ---------------------------
CHANNEL_COLORS = {
    "auto_email": "#A7C957", "manual_email": "#6E8B3D",     # email: greens
    "call": "#CC7A6F",                                       # phone: soft red
    "li_connect": "#7DA0CA", "li_message": "#5E81AC", "li_other": "#A6C0E0",  # blues
    "inbound_email": "#E4C07A",                              # amber
    "meeting": "#B48EAD",                                    # purple
    "other": "#9AA1AF",                                      # grey
}
CHANNEL_LABELS = {
    "auto_email": "Automated email", "manual_email": "Manual email",
    "call": "Dial", "li_connect": "LinkedIn connect", "li_message": "LinkedIn message",
    "li_other": "LinkedIn other", "inbound_email": "Inbound reply",
    "meeting": "Meeting", "other": "Other",
}
MEASURE_COLORS = {
    "auto_email": "#A7C957", "manual_email": "#6E8B3D", "emails": "#8FB04E",
    "dials": "#CC7A6F", "conversations": "#A65A50", "pursuits": "#BF6A60",
    "linkedin": "#7DA0CA", "li_connect": "#7DA0CA", "li_message": "#5E81AC",
    "li_other": "#A6C0E0", "inbound_replies": "#E4C07A",
    "meetings_booked": "#B48EAD", "other_outreach": "#9AA1AF",
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
# which family colour a KPI card / measure belongs to
FAMILY = {
    "Activities": "#C7CCD6", "Emails": "#A7C957", "Dials": "#CC7A6F",
    "LinkedIn": "#7DA0CA", "Inbound": "#E4C07A", "Meetings": "#B48EAD",
    "Other": "#9AA1AF", "Coverage": PURPLE,
}

DEFS = {
    "auto_email": "Automated email — a sequence tool (AmpleMarket/Apollo) sent it on the rep's behalf.",
    "manual_email": "Manual email — a human actually wrote and sent it (best-effort label; see ontology note).",
    "emails": "Automated + manual emails added together.",
    "inbound_replies": "Emails prospects sent to the CA (bounces/auto-replies removed).",
    "dials": "Phone dials from AmpleMarket's dialer, answered or not. Dials outside it are invisible.",
    "pursuits": "One person chased by phone — repeated dials within 30 min bundle into one pursuit.",
    "conversations": "Dials where a real human answered (the tool's own flag).",
    "linkedin": "LinkedIn steps completed inside AmpleMarket sequences ONLY — native LinkedIn is not captured.",
    "other": "Sequence steps of an unrecognised type (custom AmpleMarket to-do steps — the tool logs only that they were done).",
    "other_outreach": "Sequence steps of unrecognised type (could be WhatsApp, research, anything).",
    "meetings_booked": "Every meeting in the window — booked, NOT held. Outcome is logged on only ~20%, so always read with the held/canceled/scheduled/unknown split.",
    "meetings_unknown": "Booked with no outcome logged — usually the biggest bucket. Never assume held.",
    "total_counted": "Every counted activity for the rep, each counted once.",
    "accounts_touched": "Distinct companies with at least one counted activity (misses the ~60% of activity with no matched company).",
    "contacts_touched": "Distinct people with at least one counted activity (same ~60% caveat).",
    "accounts_owned": "Companies where this rep is the HubSpot target-account owner.",
    "owned_touched": "Of the rep's owned accounts, how many they PERSONALLY touched in the window.",
    "coverage_pct": "Owned touched / accounts owned — likely a slight under-count; compare reps, watch the trend.",
    "touchpoints": "Counted activities filed under that account/person. Meetings excluded (can't be tied to accounts yet).",
    "saos": "SAOs achieved (from Ray's Global CA Performance Tracker, monthly).",
    "sao_target": "That rep's monthly SAO target (Ray's tracker).",
    "saos_outbound": "SAOs minus inbound minus event — the ones outbound activity can claim.",
}

_CSS = """
<style>
.block-container {padding-top: 2rem; max-width: 1680px;}
h1 {font-size: 1.6rem !important; letter-spacing: -.01em;}

.kpi {background:%(card)s; border:1px solid %(border)s; border-top:3px solid %(border)s;
      border-radius:5px; padding:12px 13px 10px; min-height:150px;
      display:flex; flex-direction:column; overflow:hidden;}
.kpi .lbl {font-size:.66rem; text-transform:uppercase; letter-spacing:.02em;
           margin-bottom:4px; font-weight:700; white-space:nowrap;}
.kpi .val {font-size:1.7rem; font-weight:700; color:#F2F3F6; line-height:1.1;}
.kpi .sub {font-size:.72rem; color:%(dim)s; margin-top:auto; padding-top:6px;
           line-height:1.35;}

.pill {display:inline-flex; align-items:center; gap:7px; background:%(card)s;
       border:1px solid %(border)s; border-radius:5px; padding:5px 12px;
       font-size:.8rem; color:#D7DAE0; margin-right:7px; margin-bottom:6px;
       white-space:nowrap;}
.pill.lime {border-color:%(lime)s;}
.pill.purple {border-color:%(purple)s;}
.pill.red {border-color:#CC7A6F;}
.pill b {color:#FFF; font-weight:600;}

.dot {width:8px; height:8px; border-radius:50%%; background:#E4574F;
      animation: blink 1.6s ease-in-out infinite; flex:none;}
.dot.lime {background:%(lime)s;}
@keyframes blink {50%% {opacity:.15;}}

.refresh-banner {display:flex; align-items:center; gap:10px;
  background:rgba(179,226,73,.10); border:1px solid %(lime)s; border-radius:5px;
  padding:10px 14px; margin:4px 0 14px; color:#E9EAEE; font-size:.9rem;}

.explain {color:%(dim)s; font-size:.95rem; margin:-4px 0 10px;}
.swatch {display:inline-block; width:11px; height:11px; border-radius:2px;
         margin:0 4px 0 12px; vertical-align:middle;}

/* squarer everything, tighter grid lines */
div[data-testid="stDataFrame"] {border-radius:5px;}
button, .stButton>button {border-radius:5px !important;}
</style>
""" % {"card": BG_CARD, "border": BORDER, "dim": TEXT_DIM, "lime": LIME, "purple": PURPLE}


def setup(title, explainer):
    st.set_page_config(page_title=title, layout="wide")
    st.markdown(_CSS, unsafe_allow_html=True)

    import os
    pw = os.environ.get("DASHBOARD_PASSWORD")
    if pw and not st.session_state.get("_authed"):
        st.title("CA Activity Dashboard")
        if st.text_input("Password", type="password") == pw:
            st.session_state["_authed"] = True
            st.rerun()
        st.stop()

    left, right = st.columns([3, 1.5])
    with left:
        st.title(title)
        st.markdown('<p class="explain">%s</p>' % html.escape(explainer),
                    unsafe_allow_html=True)
    with right:
        st.markdown(_status_pills(), unsafe_allow_html=True)

    run = db.q(queries.RUN_ACTIVE)
    if len(run) and run.iloc[0]["started_at"] is not None:
        st.markdown(
            '<div class="refresh-banner"><span class="dot lime"></span>'
            "The data is being refreshed right now (started %s UTC) — "
            "numbers may move for a few minutes.</div>"
            % run.iloc[0]["started_at"].strftime("%H:%M"),
            unsafe_allow_html=True)
    return data_range()


def _status_pills():
    dr = db.q(queries.LAST_DAILY_RUN)
    action = dr.iloc[0]["finished_at"] if len(dr) else None
    action_s = action.strftime("%a %d %b, %H:%M UTC") if action is not None else "unknown"
    first, last = data_range()
    return ('<div style="text-align:right; padding-top:10px;">'
            '<span class="pill"><span class="dot"></span>'
            'Last sync: <b>%s</b></span><br>'
            '<span class="pill">Data through: <b>%s</b></span></div>'
            % (action_s, last.strftime("%d %b %Y")))


@st.cache_data(ttl=300, show_spinner=False)
def data_range():
    df = db.q(queries.DATA_RANGE)
    return df.iloc[0]["first_day"], df.iloc[0]["last_day"]


def window_pills(first, last, key="win"):
    choice = st.pills("Time window",
                      ["Last 7 days", "Last 30 days", "All time", "Custom"],
                      default="Last 7 days", key=key, label_visibility="collapsed")
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
    return start, end, "%s to %s" % (start, end)


def kpi_row(cards):
    """cards: list of {label, value, sub?, help?}. Card tint from FAMILY[label]."""
    cols = st.columns(len(cards))
    for col, c in zip(cols, cards):
        colour = FAMILY.get(c["label"], "#C7CCD6")
        tip = html.escape(c.get("help", ""), quote=True)
        sub = ('<div class="sub">%s</div>' % html.escape(str(c["sub"]))) if c.get("sub") else ""
        col.markdown(
            '<div class="kpi" title="%s" style="border-top-color:%s">'
            '<div class="lbl" style="color:%s">%s</div>'
            '<div class="val">%s</div>%s</div>'
            % (tip, colour, colour, html.escape(c["label"]), html.escape(str(c["value"])), sub),
            unsafe_allow_html=True)


def pill(text, color=""):
    st.markdown('<span class="pill %s">%s</span>' % (color, text), unsafe_allow_html=True)


def channel_legend():
    st.markdown(
        '<div style="font-size:.8rem;color:%s;margin-top:4px">Channel families:'
        '<span class="swatch" style="background:#A7C957"></span>email'
        '<span class="swatch" style="background:#CC7A6F"></span>phone'
        '<span class="swatch" style="background:#7DA0CA"></span>LinkedIn'
        '<span class="swatch" style="background:#E4C07A"></span>inbound'
        '<span class="swatch" style="background:#B48EAD"></span>meetings</div>' % TEXT_DIM,
        unsafe_allow_html=True)


def themed(chart):
    return (chart
            .configure(background="transparent")
            .configure_axis(labelColor=TEXT_DIM, titleColor=TEXT_DIM,
                            gridColor="#232833", domainColor="#232833", tickColor="#232833")
            .configure_legend(labelColor="#C9CDD6", titleColor=TEXT_DIM)
            .configure_view(strokeOpacity=0))


def week_label(df, col="week_start"):
    out = df.copy()
    out["week"] = pd.to_datetime(out[col]).dt.strftime("Wk of %d %b")
    return out


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def heat_styler(df, cols):
    """Red(low) -> amber -> green(high) per-column background heatmap, muted,
    with dark text for legibility. Dependency-free (no matplotlib)."""
    LO, MID, HI = (191, 97, 106), (228, 192, 122), (163, 190, 140)
    mins = {c: df[c].min() for c in cols}
    maxs = {c: df[c].max() for c in cols}

    def _cell(col, v):
        if pd.isna(v):
            return ""
        lo, hi = mins[col], maxs[col]
        t = 0.5 if hi == lo else (v - lo) / (hi - lo)
        rgb = _lerp(LO, MID, t / 0.5) if t < 0.5 else _lerp(MID, HI, (t - 0.5) / 0.5)
        return "background-color: rgb(%d,%d,%d); color:#14171E" % rgb

    def _apply(data):
        out = pd.DataFrame("", index=data.index, columns=data.columns)
        for c in cols:
            if c in data.columns:
                out[c] = data[c].map(lambda v: _cell(c, v))
        return out
    return df.style.apply(_apply, axis=None)


def family_tints(columns, families, alpha=0.09):
    def _rgba(h):
        return "background-color: rgba(%d,%d,%d,%.2f)" % (
            int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16), alpha)
    tint = {c: _rgba(h) for c, h in families.items()}

    def apply(df):
        return pd.DataFrame([[tint.get(c, "") for c in df.columns]] * len(df),
                            index=df.index, columns=df.columns)
    return apply


def trend_chart(df, value_col, series_col, order, domain, rng, height=320):
    """Line chart with the series name attached at its last dot (no side legend).
    df must carry 'week' (ordinal label) and 'week_start' (date)."""
    import altair as alt
    scale = alt.Scale(domain=domain, range=rng)
    base = alt.Chart(df)
    line = base.mark_line(
        strokeWidth=1.4, strokeOpacity=0.5,
        point=alt.OverlayMarkDef(size=150, filled=True, opacity=1),
    ).encode(
        x=alt.X("week:O", sort=order, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("%s:Q" % value_col, title=None),
        color=alt.Color("%s:N" % series_col, scale=scale, legend=None),
        tooltip=["week", series_col, value_col])
    last = df[df.week_start == df.week_start.max()]
    labels = alt.Chart(last).mark_text(align="left", dx=8, fontSize=11, fontWeight=600).encode(
        x=alt.X("week:O", sort=order),
        y=alt.Y("%s:Q" % value_col),
        text=alt.Text("%s:N" % series_col),
        color=alt.Color("%s:N" % series_col, scale=scale, legend=None))
    return themed((line + labels).properties(height=height).resolve_scale(color="shared"))
