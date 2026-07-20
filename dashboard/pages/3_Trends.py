"""Trends — week-by-week movement of any measure, per CA or whole team."""
import altair as alt
import streamlit as st

import db
import queries
import ui

TEAM = "🌐 Whole team"

first, last = ui.setup(
    "Trends",
    "Week-by-week movement of any measure — watch whether coaching is changing behaviour.",
    "📈")

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
    import pandas as pd
    d = pd.concat(frames, ignore_index=True)
    who_domain = [w for w in [TEAM] + reps if w in d.who.unique()]
    palette = [ui.LIME] + ["#8A55F7", "#5B8DEF", "#E4574F", "#E8A33D", "#5E9C33",
                           "#2F5FC4", "#A32C26", "#9DBEFF", "#C74841", "#8CC63F",
                           "#B77CFF", "#4E9F8A", "#D96BC1", "#7A8699", "#E4C64F",
                           "#6ACAE0", "#B3E249"]
    st.altair_chart(ui.themed(
        alt.Chart(d).mark_line(
            strokeWidth=1.3, strokeOpacity=0.45,
            point=alt.OverlayMarkDef(size=150, filled=True, opacity=1),
        ).encode(
            x=alt.X("week:O", sort=order, title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("%s:Q" % measure, title=None),
            color=alt.Color("who:N", title=None,
                            scale=alt.Scale(domain=who_domain,
                                            range=palette[:len(who_domain)])),
            tooltip=["week", "who", measure],
        ).properties(height=360)),
        use_container_width=True)
    st.caption("One dot per week of data — the dots are the data; lines just connect them. "
               "The latest week is partial until Sunday. The lime line is the whole team.")

with st.expander("Table — weeks × CAs"):
    pv = wk.pivot_table(index="ca_name", columns="week", values=measure)
    pv = pv[[w for w in order if w in pv.columns]]
    st.dataframe(pv, use_container_width=True)
