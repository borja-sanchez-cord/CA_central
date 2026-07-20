"""SAO vs activity — monthly, side by side. Context, not cause-and-effect (v1)."""
import altair as alt
import pandas as pd
import streamlit as st

import db
import queries
import ui

first, last = ui.page_setup("SAO vs activity", "🎯")

st.warning(
    "**Directional only.** Activity history starts %s (July is a *partial* month; the first "
    "cleanly comparable month is **August 2026**), and an SAO this month usually comes from "
    "*earlier* outreach. Read side by side as context — not as same-month cause→effect. "
    "Correlation views unlock ~October 2026 (3+ full months)." % first)

ms = db.q(queries.MONTHLY_SCORECARD)
sao = db.q(queries.SAO_MONTHLY)

months = sorted(ms.month_start.unique())
month = st.selectbox("Month", months, index=len(months) - 1,
                     format_func=lambda m: pd.Timestamp(m).strftime("%B %Y"))
m_act = ms[ms.month_start == month]
m_sao = sao[sao.month == month]

j = m_act.merge(m_sao, left_on="ca_name", right_on="rep_name", how="left")
j["saos_outbound"] = j.saos - j.saos_inbound.fillna(0) - j.saos_event.fillna(0)
j["attainment_pct"] = (j.saos / j.sao_target * 100).round(0)
j["ramping"] = j.is_ramping.fillna(False)

mo_start = pd.Timestamp(month).date()
if mo_start <= first <= (pd.Timestamp(month) + pd.offsets.MonthEnd(0)).date():
    st.caption("⚠️ **%s is partial**: activity covered only from %s."
               % (pd.Timestamp(month).strftime("%B %Y"), first))

show = j[["ca_name", "ramping", "total_counted", "emails", "dials", "conversations",
          "linkedin", "meetings_booked", "saos", "sao_target", "attainment_pct",
          "saos_inbound", "saos_event", "saos_outbound", "pipeline_usd"]]
st.dataframe(
    show.sort_values("saos", ascending=False), hide_index=True, use_container_width=True,
    column_config={
        "ca_name": st.column_config.TextColumn("CA", pinned=True),
        "ramping": st.column_config.CheckboxColumn("Ramping"),
        "total_counted": st.column_config.NumberColumn("Activity", help=ui.DEFS["total_counted"]),
        "meetings_booked": st.column_config.NumberColumn("Meetings booked",
                                                         help=ui.DEFS["meetings_booked"]),
        "saos": st.column_config.NumberColumn("SAOs", help=ui.DEFS["saos"]),
        "sao_target": st.column_config.NumberColumn("Target", help=ui.DEFS["sao_target"]),
        "attainment_pct": st.column_config.NumberColumn("Attainment", format="%.0f%%"),
        "saos_inbound": "SAO inbound",
        "saos_event": "SAO event",
        "saos_outbound": st.column_config.NumberColumn("SAO outbound",
                                                       help=ui.DEFS["saos_outbound"]),
        "pipeline_usd": st.column_config.NumberColumn("Pipeline $", format="$%d"),
    })
st.caption("SAO columns come verbatim from Ray's Global CA Performance Tracker (monthly); "
           "activity/meetings come from the verified activity data. Joined by rep — nothing recomputed.")

c1, c2 = st.columns(2)
with c1:
    st.altair_chart(
        alt.Chart(j.dropna(subset=["saos"])).mark_circle(size=120).encode(
            x=alt.X("meetings_booked:Q", title="Meetings booked"),
            y=alt.Y("saos:Q", title="SAOs"),
            color=alt.Color("ramping:N", title="Ramping"),
            tooltip=["ca_name", "meetings_booked", "saos", "total_counted"],
        ).properties(height=300, title="Meetings booked vs SAOs (context, not causation)"),
        use_container_width=True)
with c2:
    st.altair_chart(
        alt.Chart(j.dropna(subset=["attainment_pct"])).mark_bar().encode(
            x=alt.X("attainment_pct:Q", title="SAO attainment %"),
            y=alt.Y("ca_name:N", sort="-x", title=None),
            color=alt.condition("datum.attainment_pct >= 100",
                                alt.value("#2ca02c"), alt.value("#1f77b4")),
            tooltip=["ca_name", "saos", "sao_target", "attainment_pct"],
        ).properties(height=300, title="SAO target attainment"),
        use_container_width=True)

with st.expander("One rep, month by month (incl. months before activity tracking)"):
    rep = st.selectbox("CA", sorted(sao[sao.rep_name.isin(ms.ca_name)].rep_name.unique()))
    hist = sao[sao.rep_name == rep].sort_values("month")
    hist = hist[["month", "saos", "sao_target", "saos_inbound", "saos_event", "pipeline_usd"]]
    st.dataframe(hist, hide_index=True, use_container_width=True)
