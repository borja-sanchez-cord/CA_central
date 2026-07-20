"""SAO vs activity — monthly effort next to monthly results. Context, not causation."""
import altair as alt
import pandas as pd
import streamlit as st

import db
import queries
import ui

first, last = ui.setup(
    "SAO vs activity",
    "Each CA's monthly activity next to the meetings and SAOs they produced — context, not causation.")

ms = db.q(queries.MONTHLY_SCORECARD)
sao = db.q(queries.SAO_MONTHLY)

c1, _ = st.columns([1, 3])
months = sorted(ms.month_start.unique())
month = c1.selectbox("Month", months, index=len(months) - 1,
                     format_func=lambda m: pd.Timestamp(m).strftime("%B %Y"))

mo_start = pd.Timestamp(month).date()
mo_end = (pd.Timestamp(month) + pd.offsets.MonthEnd(0)).date()
if mo_start <= first <= mo_end:
    ui.pill("<b>%s is partial</b> — activity covered only from %s"
            % (pd.Timestamp(month).strftime("%B %Y"), first), "red")
ui.pill("Directional only: an SAO this month usually comes from <b>earlier</b> outreach. "
        "Correlation views unlock ~Oct 2026 (3+ full months)", "purple")

m_act = ms[ms.month_start == month]
m_sao = sao[sao.month == month]
j = m_act.merge(m_sao, left_on="ca_name", right_on="rep_name", how="left")
j["saos_outbound"] = j.saos - j.saos_inbound.fillna(0) - j.saos_event.fillna(0)
j["attainment_pct"] = (j.saos / j.sao_target * 100).round(0)
j["ramping"] = j.is_ramping.fillna(False)
j["ca"] = j.ca_name + j.ramping.map(lambda x: " *" if x else "")

show = j[["ca", "total_counted", "emails", "dials", "conversations", "linkedin",
          "meetings_booked", "saos", "sao_target", "attainment_pct",
          "saos_inbound", "saos_event", "saos_outbound", "pipeline_usd"]].copy()
for c in ["total_counted", "emails", "dials", "conversations", "linkedin",
          "meetings_booked", "saos", "sao_target", "saos_inbound", "saos_event",
          "saos_outbound"]:
    show[c] = show[c].round().astype("Int64")   # counts: no decimals
ACT = "#8A55F7"   # activity family: purple tint
OUT = "#B3E249"   # results family: lime tint
FAMS = {"total_counted": ACT, "emails": ACT, "dials": ACT, "conversations": ACT,
        "linkedin": ACT, "meetings_booked": ACT,
        "saos": OUT, "sao_target": OUT, "attainment_pct": OUT,
        "saos_inbound": OUT, "saos_event": OUT, "saos_outbound": OUT,
        "pipeline_usd": OUT}
styled = (show.sort_values("saos", ascending=False)
              .style.apply(ui.family_tints(show.columns, FAMS), axis=None))
st.dataframe(
    styled, hide_index=True, use_container_width=True,
    column_config={
        "ca": st.column_config.TextColumn("CA", pinned=True,
                                          help="* = ramping (reduced target)"),
        "total_counted": st.column_config.NumberColumn("Activities", help=ui.DEFS["total_counted"]),
        "emails": "Emails", "dials": "Dials", "conversations": "Convos",
        "linkedin": "LinkedIn",
        "meetings_booked": st.column_config.NumberColumn("Meetings booked",
                                                         help=ui.DEFS["meetings_booked"]),
        "saos": st.column_config.NumberColumn("SAOs", help=ui.DEFS["saos"]),
        "sao_target": st.column_config.NumberColumn("Target", help=ui.DEFS["sao_target"]),
        "attainment_pct": st.column_config.NumberColumn("Attainment", format="%.0f%%"),
        "saos_inbound": "Inbound SAO", "saos_event": "Event SAO",
        "saos_outbound": st.column_config.NumberColumn("Outbound SAO",
                                                       help=ui.DEFS["saos_outbound"]),
        "pipeline_usd": st.column_config.NumberColumn("Pipeline $", format="$%d"),
    })
st.caption("Purple tint = activity (our data) | lime tint = results (Ray's tracker). "
           "* = ramping.")

# --- effort vs results, side by side -------------------------------------------
st.subheader("Meetings booked vs SAOs, per CA")
pair = j.dropna(subset=["saos"])[["ca", "meetings_booked", "saos"]].melt(
    "ca", var_name="what", value_name="n")
pair["what"] = pair.what.map({"meetings_booked": "Meetings booked", "saos": "SAOs"})
sort_order = j.dropna(subset=["saos"]).sort_values("saos", ascending=False).ca.tolist()
c1, c2 = st.columns(2)
with c1:
    st.altair_chart(ui.themed(
        alt.Chart(pair).mark_bar().encode(
            y=alt.Y("ca:N", sort=sort_order, title=None),
            x=alt.X("n:Q", title=None),
            yOffset=alt.YOffset("what:N"),
            color=alt.Color("what:N", title=None,
                            scale=alt.Scale(domain=["Meetings booked", "SAOs"],
                                            range=[ui.PURPLE, ui.LIME])),
            tooltip=["ca", "what", "n"],
        ).properties(height=26 * len(sort_order) * 2 // 2 + 60)),
        use_container_width=True)
    st.caption("Big purple, no lime = meetings that didn't convert yet (or the lag).")
with c2:
    st.altair_chart(ui.themed(
        alt.Chart(j.dropna(subset=["attainment_pct"])).mark_bar().encode(
            x=alt.X("attainment_pct:Q", title="SAO target attainment %"),
            y=alt.Y("ca:N", sort="-x", title=None),
            color=alt.condition("datum.attainment_pct >= 100",
                                alt.value(ui.LIME), alt.value(ui.PURPLE)),
            tooltip=["ca", "saos", "sao_target", "attainment_pct"],
        ).properties(height=26 * len(sort_order) + 60)),
        use_container_width=True)
    st.caption("Lime = target hit.")

with st.expander("One CA, month by month (incl. months before activity tracking)"):
    rep = st.selectbox("CA", sorted(sao[sao.rep_name.isin(ms.ca_name)].rep_name.unique()))
    hist = sao[sao.rep_name == rep].sort_values("month")
    st.dataframe(
        hist[["month", "saos", "sao_target", "saos_inbound", "saos_event", "pipeline_usd"]],
        hide_index=True, use_container_width=True,
        column_config={
            "month": st.column_config.DateColumn("Month", format="MMM YYYY"),
            "saos": "SAOs", "sao_target": "Target",
            "saos_inbound": "Inbound", "saos_event": "Event",
            "pipeline_usd": st.column_config.NumberColumn("Pipeline $", format="$%d"),
        })
