"""Trends and comps — week-by-week movement of any measure, per CA or whole team."""
import altair as alt
import pandas as pd
import streamlit as st

import db
import queries
import ui

TEAM = "Whole team"

first, last = ui.setup(
    "Trends and comps",
    "Week-by-week movement of any measure, and CA-vs-CA comparison — watch whether "
    "coaching is changing behaviour.")

MEASURES = ["total_counted", "emails", "auto_email", "manual_email", "dials",
            "pursuits", "conversations", "linkedin", "inbound_replies",
            "meetings_booked", "accounts_touched", "contacts_touched", "coverage_pct"]

c1, c2 = st.columns([1, 2])
measure = c1.selectbox("Measure", MEASURES,
                       format_func=lambda m: ui.MEASURE_LABELS.get(m, m))
wk = ui.week_label(db.q(queries.WEEKLY_TREND))
reps = sorted(wk.ca_name.unique())
sel = c2.multiselect("Who", [TEAM] + reps, default=[TEAM])
if ui.DEFS.get(measure):
    st.caption(ui.DEFS[measure])
if not sel:
    sel = [TEAM]

order = [w for w in wk.sort_values("week_start").week.unique()]
frames = []
if TEAM in sel:
    if measure == "coverage_pct":
        st.info("Coverage % is per-CA — a team average would mislead. Pick CAs instead.")
    else:
        team = wk.groupby(["week", "week_start"], as_index=False)[measure].sum()
        team["who"] = TEAM
        frames.append(team)
for name in [s for s in sel if s != TEAM]:
    one = wk[wk.ca_name == name][["week", "week_start", measure]].copy()
    one["who"] = name
    frames.append(one)

if frames:
    d = pd.concat(frames, ignore_index=True)
    who_domain = [w for w in [TEAM] + reps if w in d.who.unique()]
    palette = [ui.LIME] + ["#B48EAD", "#7DA0CA", "#CC7A6F", "#E4C07A", "#6E8B3D",
                           "#5E81AC", "#A65A50", "#A6C0E0", "#BF6A60", "#8FB04E",
                           "#B77CFF", "#4E9F8A", "#D08BC0", "#8A93A5", "#D9C06B",
                           "#6ACAE0", "#A7C957"]
    st.altair_chart(
        ui.trend_chart(d, measure, "who", order, who_domain, palette[:len(who_domain)], height=360),
        use_container_width=True)
    st.caption("Latest week is partial until Sunday.")

with st.expander("Table — weeks × CAs"):
    pv = wk.pivot_table(index="ca_name", columns="week", values=measure)
    pv = pv[[w for w in order if w in pv.columns]]
    st.dataframe(pv, use_container_width=True)
