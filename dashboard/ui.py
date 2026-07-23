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
BG_CARD = "#272D39"
BORDER = "#39404E"
TEXT_DIM = "#A2A9B6"
RAMP_RED = "#CC7A6F"   # ramping marker (muted red, matches the phone/call family)

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
    "conversations": "Conversations", "linkedin": "All LinkedIn",
    "inbound_replies": "Inbound replies", "meetings_booked": "Meetings booked",
    "meetings_new_stakeholder": "New-stakeholder meetings",
    "meetings_follow_up": "Follow-up meetings",
    "other_outreach": "Other", "total_counted": "Activities",
    "accounts_touched": "Accounts touched", "contacts_touched": "Contacts touched",
    "coverage_pct": "Coverage %",
}
# which family colour a KPI card / measure belongs to
FAMILY = {
    "Activities": "#C7CCD6", "Emails": "#A7C957", "Dials": "#CC7A6F",
    "LinkedIn": "#7DA0CA", "Inbound": "#E4C07A", "Meetings": "#B48EAD",
    "New meetings": "#7A5195", "Other": "#9AA1AF", "Coverage": PURPLE,
}

DEFS = {
    "auto_email": "Sequence tool (AmpleMarket/Apollo) sent it for the rep. From HubSpot. Note: a manual email sent *through* the tool still counts as auto.",
    "manual_email": "A human wrote and sent it — we know from HubSpot's manual/auto flag. Imperfect: a sequenced send can slip in as auto.",
    "emails": "Automated + manual emails added together.",
    "inbound_replies": "Emails prospects sent to the CA (bounces/auto-replies removed).",
    "dials": "Phone dials from AmpleMarket's dialer, answered or not. Dials outside it are invisible.",
    "pursuits": "One person chased by phone — repeat dials within 30 min count as one pursuit. From AmpleMarket's dialer.",
    "conversations": "Dials AmpleMarket marked as connected — a real human picked up.",
    "linkedin": "LinkedIn steps run inside AmpleMarket sequences only. We cannot see native LinkedIn (manual messages, InMails) — there's no feed for it.",
    "other": "Sequence steps of an unrecognised type (custom AmpleMarket to-do steps — the tool logs only that they were done).",
    "other_outreach": "Sequence steps of unrecognised type (could be WhatsApp, research, anything).",
    "meetings_booked": "Every meeting booked in the window (not held). Most now have a known outcome — the rep marked it, or Gong verified it; the rest stay unknown. Read with the held / Gong-held / canceled / unknown split.",
    "meetings_held": "The rep marked the meeting as happened in HubSpot.",
    "meetings_canceled": "The rep marked the meeting as canceled in HubSpot.",
    "meetings_scheduled": "Upcoming — marked scheduled or rescheduled in HubSpot.",
    "meetings_unknown": "No outcome logged and no Gong recording — so we can't tell if it happened. NOT the same as a no-show.",
    "meetings_gong_verified": "The rep logged no outcome, but a completed Gong recording of the meeting exists — same time slot, same contact — so it demonstrably happened. Carved out of 'unknown' (the two always sum to the old unknown count). Nightly from HubSpot's Gong integration.",
    "meetings_new_stakeholder": "First meeting with an account in a rolling 60 days — a follow-up or a colleague's later meeting doesn't re-count.",
    "meetings_follow_up": "A meeting on an account already met within the previous 60 days — booked and visible, but not a new conversation.",
    "meetings_no_account": "No attendee we can tie to a known account — still counted, shown honestly (~5% of meetings).",
    "total_counted": "Every counted activity for the rep, each counted once.",
    "accounts_touched": "Distinct companies with at least one counted activity. ~5% of activity has no company matched (not shown here).",
    "contacts_touched": "Distinct people with at least one counted activity. ~3% of activity has no contact matched.",
    "accounts_owned": "Companies where this rep is the HubSpot target-account owner.",
    "owned_touched": "Of the rep's owned accounts, how many they PERSONALLY touched in the window.",
    "coverage_pct": "Coverage = the owned accounts this CA personally touched, out of every account they own. It counts ALL owned accounts — including customers, open deals, and recently lost/churned ones they may be right to leave alone — so it is NOT deal-aware like the neglected list. Read a low % as a prompt to look, not a verdict.",
    "touchpoints": "Counted activities filed under that account/person. Meetings excluded (can't be tied to accounts yet).",
    "saos": "SAOs achieved (from Ray's Global CA Performance Tracker, monthly).",
    "sao_target": "That rep's monthly SAO target (Ray's tracker).",
    "saos_outbound": "SAOs minus inbound minus event — the ones outbound activity can claim.",
    "neglect_status": "From HubSpot deals (nightly). Customer = won deal, not churned since — never flagged. Open deal = mid-deal, never flagged while open (age shown so stale deals stay auditable). Lost = closed-lost, rests 60 days. Churned (incl. lost renewals) rests 9 months. Neglected = none of those apply.",
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
    if not pw:                       # Streamlit Cloud exposes secrets via st.secrets
        try:
            pw = st.secrets["DASHBOARD_PASSWORD"]
        except Exception:
            pw = None
    if pw and not st.session_state.get("_authed"):
        st.title("CA Activity Dashboard")
        if st.text_input("Password", type="password") == pw:
            st.session_state["_authed"] = True
            st.rerun()
        st.stop()

    left, right = st.columns([3, 1.5])
    with left:
        st.title(title)
        if explainer:   # some pages let the charts tell the story — no subtitle
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


@st.cache_data(ttl=300, show_spinner=False)
def inactive_reps():
    """CAs who left the team — kept in reports (history) but no longer active."""
    df = db.q(queries.INACTIVE_REPS)
    return set(df["name"]) if len(df) else set()


def active_only(df, col="ca_name"):
    """Display-only: drop rows for departed CAs (dim_ca.is_active=false) so the
    live view is the CURRENT team. Their history stays in the DB and still
    counts in the model — nothing is deleted. Uses the departed set as a
    denylist (not an active allowlist), so a name from another source that
    isn't in dim_ca at all is never wrongly dropped. Future leavers vanish
    automatically once resolve.py flags them inactive."""
    if col not in df.columns:
        return df
    return df[~df[col].isin(inactive_reps())].reset_index(drop=True)


def window_pills(first, last, key="win"):
    """7/30-day rolling windows, calendar months (Ray's SAO clock), all, custom."""
    months = []          # newest first: (label, month_start)
    m = last.replace(day=1)
    while m >= first.replace(day=1):
        months.append((m.strftime("%b %Y"), m))
        m = (m - dt.timedelta(days=1)).replace(day=1)
    month_lbls = [lbl for lbl, _ in months]

    # seed the default through session state, never the widget arg — a drill
    # handoff pre-sets this key, and mixing both triggers Streamlit's
    # "default value + Session State API" warning
    if key not in st.session_state:
        st.session_state[key] = "Last 7 days"
    choice = st.pills("Time window",
                      ["Last 7 days", "Last 30 days"] + month_lbls + ["All time", "Custom"],
                      key=key, label_visibility="collapsed")
    choice = choice or "Last 7 days"
    if choice == "Last 7 days":
        return max(last - dt.timedelta(days=6), first), last, choice
    if choice == "Last 30 days":
        return max(last - dt.timedelta(days=29), first), last, choice
    if choice == "All time":
        return first, last, choice
    for lbl, ms in months:
        if choice == lbl:
            me = (ms + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
            start, end = max(ms, first), min(me, last)
            if ms < first:      # month truncated by history start (July: from Jul 6)
                st.caption("%s is partial in our data — covered from %s." % (lbl, first))
            return start, end, lbl
    c1, c2, _ = st.columns([1, 1, 3])
    st.session_state.setdefault(key + "_a", first)   # same seed-via-session
    st.session_state.setdefault(key + "_b", last)    # pattern as the pills
    start = c1.date_input("From", min_value=first, max_value=last, key=key + "_a")
    end = c2.date_input("To", min_value=first, max_value=last, key=key + "_b")
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


def centered_legend(items):
    """A horizontally-centered swatch legend from (label, color) pairs. Used
    where Altair's built-in bottom legend anchors to the left of the plot
    (the long y-axis labels push the plot right, so its legend looks off)."""
    chips = "".join(
        '<span style="display:inline-block;margin:0 12px;white-space:nowrap;">'
        '<span style="display:inline-block;width:11px;height:11px;background:%s;'
        'border-radius:2px;vertical-align:middle;margin-right:6px;"></span>'
        '<span style="vertical-align:middle;">%s</span></span>' % (c, html.escape(l))
        for l, c in items)
    st.markdown('<div style="text-align:center;font-size:.8rem;color:%s;margin:-4px 0 2px;">%s</div>'
                % (TEXT_DIM, chips), unsafe_allow_html=True)


def legend_help(items):
    """A vertical swatch legend for a chart's right-hand column, where hovering
    an item reveals its definition (native title tooltip). Feeds on the same
    DEFS as the table headers, so the legend teaches the exact same words.
    items: (label, color, definition) triples."""
    rows = "".join(
        '<div title="%s" style="white-space:nowrap;margin:3px 0;cursor:help;">'
        '<span style="display:inline-block;width:11px;height:11px;background:%s;'
        'border-radius:2px;vertical-align:middle;margin-right:7px;"></span>'
        '<span style="vertical-align:middle;border-bottom:1px dotted %s;">%s</span></div>'
        % (html.escape(defn, quote=True), c, TEXT_DIM, html.escape(l))
        for l, c, defn in items)
    st.markdown('<div style="font-size:.8rem;color:#C9CDD6;padding-top:6px;">%s</div>'
                % rows, unsafe_allow_html=True)


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


def heat_styler(df, good_cols, bad_cols=()):
    """Direction-aware per-column heatmap, muted, dependency-free.
    good_cols: green when HIGH (effort/results). bad_cols: red when HIGH
    (e.g. canceled meetings, unlogged outcomes) — high is not always good.
    Columns in neither list stay unshaded (no value judgment)."""
    LO, MID, HI = (191, 97, 106), (228, 192, 122), (163, 190, 140)
    cols = list(good_cols) + list(bad_cols)
    mins = {c: df[c].min() for c in cols}
    maxs = {c: df[c].max() for c in cols}
    flip = set(bad_cols)

    def _cell(col, v):
        if pd.isna(v):
            return ""
        lo, hi = mins[col], maxs[col]
        t = 0.5 if hi == lo else (v - lo) / (hi - lo)
        if col in flip:
            t = 1 - t
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


# ---------------------------------------------------------------- drill-through
# Click a bar/dot -> a compact peek card of the real rows behind it (display
# layer only; the card re-reads activity_flat with the mark's own filters —
# nothing is recomputed). From the card you can jump to Raw data pre-filtered.

# the measure->filter maps live in queries.py (importable without streamlit,
# so the reconciliation test can audit them on every push); aliased here for
# the pages, which address everything drill-related through ui.*
ALL_CHANNELS = queries.ALL_CHANNELS
DRILL_CHANNELS = queries.DRILL_CHANNELS
OUTCOME_PARAM = queries.OUTCOME_PARAM


def datum_date(v):
    """A date field coming back from a Vega click is ms-epoch or ISO text —
    and the browser stamps it at LOCAL midnight, which lands a few hours off
    UTC midnight. Round to the nearest day or every drill window shifts by
    one day for viewers east of UTC (caught live: 5,978 vs the mark's 5,986)."""
    import pandas as pd
    ts = pd.to_datetime(v, unit="ms") if isinstance(v, (int, float)) else pd.to_datetime(v)
    return ts.round("D").date()


def pick_param(fields):
    """The click-selection every drillable chart carries (empty=False so a
    click on blank canvas selects nothing rather than everything)."""
    import altair as alt
    return alt.selection_point(name="pick", fields=fields, on="click", empty=False)


def read_pick(event):
    """The clicked datum (dict of the pick fields) from st.altair_chart's
    on_select return value, or None."""
    try:
        pts = event.selection.pick
    except Exception:
        return None
    return dict(pts[0]) if pts else None


def drill_chart(chart, key, fields):
    """Render a (bar) chart click-selectable and return the clicked datum."""
    return read_pick(st.altair_chart(themed(chart.add_params(pick_param(fields))),
                                     use_container_width=True, key=key,
                                     on_select="rerun"))


def drill_card(df, header, prefill, key):
    """The compact peek: up to 8 rows + true total, never a full table.
    Deeper digging hands off to Raw data — the button (or clicking a row)
    jumps there pre-filtered to this exact slice."""
    with st.container(border=True):
        total = int(df.total.iloc[0]) if len(df) else 0
        st.markdown("**%s** — %d %s%s" % (
            header, total, "activity" if total == 1 else "activities",
            " · latest %d below" % len(df) if total > len(df) else ""))
        if not len(df):
            st.caption("No underlying rows in this slice.")
            return
        disp = df[["activity_date", "channel", "account_name", "subject",
                   "contact_email"]].copy()
        disp["channel"] = disp.channel.map(lambda c: CHANNEL_LABELS.get(c, c))
        disp = disp.fillna("—")   # None cells read as data; a dash reads as empty
        ev = st.dataframe(
            disp, hide_index=True, use_container_width=True,
            height=min(38 + 35 * len(disp), 320),
            on_select="rerun", selection_mode="single-row", key=key + "_rows",
            column_config={
                "activity_date": st.column_config.DateColumn("Date"),
                "channel": "Channel", "account_name": "Account",
                "subject": "Subject", "contact_email": "Contact"})
        picked_rows = ev.selection.rows if ev and ev.selection else []
        go = st.button("Open in Raw data →", key=key + "_go",
                       help="The full audit view, pre-filtered to this slice.")
        if go or picked_rows:
            if picked_rows:   # a clicked activity narrows the jump to itself
                r = df.iloc[picked_rows[0]]
                prefill = dict(prefill,
                               search=(r.subject or r.account_name or ""))
            # drop the sticky row-selection state, else coming BACK to this
            # page would immediately re-trigger the jump (selection persists)
            st.session_state.pop(key + "_rows", None)
            st.session_state["raw_prefill"] = prefill
            st.switch_page("pages/5_Raw_data.py")


def trend_chart(df, value_col, series_col, order, domain, rng, height=320,
                pick=None):
    """Line chart. Default: the series name attached at its last dot (no side
    legend) — a line layer + a text layer. With pick (a pick_param()): dots are
    click-selectable, and Streamlit does NOT support selections on layered
    charts, so the text layer is dropped — SINGLE view, param on the line —
    and the page draws ui.centered_legend() below instead."""
    import altair as alt
    scale = alt.Scale(domain=domain, range=rng)
    base = alt.Chart(df)
    line = base.mark_line(
        strokeWidth=1.4, strokeOpacity=0.5,
        point=alt.OverlayMarkDef(size=150, filled=True, opacity=1),
    ).encode(
        x=alt.X("week:O", sort=order, title=None,
                axis=alt.Axis(labelAngle=0, labelLimit=1000)),  # don't clip the " *"
        y=alt.Y("%s:Q" % value_col, title=None),
        color=alt.Color("%s:N" % series_col, scale=scale, legend=None),
        tooltip=["week", series_col, value_col])
    if pick is not None:
        return themed(line.add_params(pick).properties(
            height=height,
            padding={"left": 5, "right": 20, "top": 5, "bottom": 30}))
    last = df[df.week_start == df.week_start.max()]
    labels = alt.Chart(last).mark_text(align="left", dx=8, fontSize=11, fontWeight=600).encode(
        x=alt.X("week:O", sort=order),
        y=alt.Y("%s:Q" % value_col),
        text=alt.Text("%s:N" % series_col),
        color=alt.Color("%s:N" % series_col, scale=scale, legend=None))
    return themed((line + labels)
                  .properties(height=height,
                              padding={"left": 5, "right": 120, "top": 5, "bottom": 30})
                  .resolve_scale(color="shared"))
