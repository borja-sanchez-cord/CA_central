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
    "Week-by-week movement of any measure, and CA-vs-CA comparison.")

MEASURES = ["total_counted", "emails", "auto_email", "manual_email", "dials",
            "pursuits", "conversations", "linkedin", "inbound_replies",
            "meetings_booked", "meetings_new_stakeholder", "meetings_follow_up",
            "accounts_touched", "contacts_touched", "coverage_pct"]

win = st.pills("Weeks", ["Last 4 weeks", "Last 12 weeks", "All weeks"],
               default="All weeks", key="trend_weeks", label_visibility="collapsed")
c1, c2 = st.columns([1, 2])
measure = c1.selectbox("Measure", MEASURES,
                       format_func=lambda m: ui.MEASURE_LABELS.get(m, m))
wk = ui.active_only(ui.week_label(db.q(queries.WEEKLY_TREND)))
# the 60-day meeting split (migration 007) rides along on (week, ca) — a
# display-only join of two approved surfaces, nothing recomputed
wk = wk.merge(
    db.q(queries.MEETING_BREAKDOWN_WEEKLY)[
        ["week_start", "ca_name", "meetings_new_stakeholder", "meetings_follow_up"]],
    on=["week_start", "ca_name"], how="left")
n_weeks = {"Last 4 weeks": 4, "Last 12 weeks": 12}.get(win)
if n_weeks:
    keep = sorted(wk.week_start.unique())[-n_weeks:]
    wk = wk[wk.week_start.isin(keep)]
# mark the latest (still-running) week with a * on its label — explained under
# the chart, so the "partial week" caveat rides the axis instead of a footer
_partial = wk.week_start.max()
wk.loc[wk.week_start == _partial, "week"] = wk.loc[wk.week_start == _partial, "week"] + " *"
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
    # drill-through: dots are clickable when the measure maps to raw rows
    # (channel measures + the 60-day meeting buckets; ratios/distinct-counts
    # like coverage % or accounts touched have no row-level equivalent)
    _bucket = {"meetings_new_stakeholder": "new_stakeholder",
               "meetings_follow_up": "follow_up"}.get(measure)
    drillable = measure in ui.DRILL_CHANNELS or _bucket is not None
    # pick always attached so every measure gets the same single-view look
    # (names in the legend below); only drillable measures listen for clicks
    ev = st.altair_chart(
        ui.trend_chart(d, measure, "who", order, who_domain,
                       palette[:len(who_domain)], height=360,
                       pick=ui.pick_param(["week", "week_start", "who"])),
        use_container_width=True, key="tr_pick",
        on_select="rerun" if drillable else "ignore")
    ui.centered_legend(list(zip(who_domain, palette[:len(who_domain)])))
    st.caption("\\* current week — partial until Sunday (Mon–Sun weeks)."
               + ("" if drillable else " Click-through isn't available for this measure "
                  "(it's a ratio or a distinct count, not a set of rows)."))
    picked = ui.read_pick(ev) if drillable else None
    if picked:
        ws = ui.datum_date(picked["week_start"])
        we = ws + pd.Timedelta(days=6)
        who = picked["who"]
        rep_f = "(all)" if who == TEAM else who
        if _bucket:
            rows = db.q(queries.DRILL_MEETING_ROWS, (ws, we, rep_f, rep_f, _bucket))
            chan = "meeting"
        else:
            chans = ui.DRILL_CHANNELS[measure]
            rows = db.q(queries.DRILL_ROWS,
                        (ws, we, rep_f, rep_f, chans, "(all)", "(all)",
                         "all", "all", "all", "all"))
            chan = chans[0] if len(chans) == 1 else "(all)"
        ui.drill_card(rows,
                      "%s — %s · %s" % (who, ui.MEASURE_LABELS.get(measure, measure),
                                        picked["week"]),   # week label = "Wk of 13 Jul"
                      {"start": ws, "end": we, "rep": rep_f, "channel": chan},
                      key="tr_card")

with st.expander("Table — weeks × CAs"):
    st.caption("Each cell = **%s** for that CA, that week."
               % ui.MEASURE_LABELS.get(measure, measure))
    pv = wk.pivot_table(index="ca_name", columns="week", values=measure)
    pv = pv[[w for w in order if w in pv.columns]]
    st.dataframe(pv, use_container_width=True)
