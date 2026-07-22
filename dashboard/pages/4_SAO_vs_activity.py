"""SAO vs activity — monthly effort next to monthly results. Context, not causation."""
import altair as alt
import pandas as pd
import streamlit as st

import db
import queries
import ui

first, last = ui.setup(
    "SAO vs activity",
    "Each CA's monthly activity next to the meetings and SAOs they produced.")

ms = db.q(queries.MONTHLY_SCORECARD)
sao = db.q(queries.SAO_MONTHLY)
mbm = db.q(queries.MEETING_BREAKDOWN_MONTHLY)  # new/follow-up/no-account per month

c1, _ = st.columns([1, 3])
months = sorted(ms.month_start.unique())
month = c1.selectbox("Month", months, index=len(months) - 1,
                     format_func=lambda m: pd.Timestamp(m).strftime("%B %Y"))

mo_start = pd.Timestamp(month).date()
mo_end = (pd.Timestamp(month) + pd.offsets.MonthEnd(0)).date()
if mo_start <= first <= mo_end:
    ui.pill("<b>%s is partial</b> — activity covered only from %s"
            % (pd.Timestamp(month).strftime("%B %Y"), first), "red")
# the two boilerplate "how to read this tab" pills collapsed into one subtle
# caption; full detail on hover (the "context not causation" line is also in
# the tab explainer up top).
st.caption("Per calendar month, from Ray's tracker · directional only — SAOs lag outreach.",
           help="This tab is per calendar month (SAOs are logged monthly in Ray's "
                "tracker — there are no 7/30-day windows here). Directional only: an SAO "
                "this month usually comes from earlier outreach, so read it as context, "
                "not cause-and-effect. Correlation views unlock ~Oct 2026 (3+ full months).")

m_act = ms[ms.month_start == month]
m_sao = sao[sao.month == month]
m_mb = mbm[mbm.month_start == month][
    ["ca_name", "meetings_new_stakeholder", "meetings_follow_up", "meetings_no_account"]]
j = m_act.merge(m_sao, left_on="ca_name", right_on="rep_name", how="left")
j = j.merge(m_mb, on="ca_name", how="left")  # display-only join of approved surfaces
j["saos_outbound"] = j.saos - j.saos_inbound.fillna(0) - j.saos_event.fillna(0)
j["attainment_pct"] = (j.saos / j.sao_target * 100).round(0)
j["ramping"] = j.is_ramping.fillna(False)
j["ca"] = j.ca_name + j.ramping.map(lambda x: " *" if x else "")   # charts: marker inline
j["ramp"] = j.ramping.map(lambda x: "*" if x else "")              # table: its own red column

# --- charts first (visual summary), detailed table below --------------------
# We compare NET NEW meetings (first meeting with an account in 60 days) to
# SAOs, NOT total booked — a follow-up meeting shouldn't count toward the
# meeting->SAO ratio (it would flatter reps whose meetings churn). Follow-up
# and no-account detail lives in the table below and on Team overview.
pair = j.dropna(subset=["saos"])[["ca", "meetings_new_stakeholder", "saos"]].melt(
    "ca", var_name="what", value_name="n")
pair["what"] = pair.what.map({"meetings_new_stakeholder": "New meetings", "saos": "SAOs"})
sort_order = j.dropna(subset=["saos"]).sort_values("saos", ascending=False).ca.tolist()
h = 26 * len(sort_order) + 40
RED_HIT, RED_MISS = "#C0554B", "#E3B0A8"
c1, c2 = st.columns(2, gap="small")
with c1:
    # each chart gets its OWN parallel title (a single spanning subheader read
    # as if it were only the left chart's); Altair's built-in legend is off —
    # ui.centered_legend draws a centered one below (native one anchors left).
    st.markdown("**New meetings vs SAOs**")
    st.altair_chart(ui.themed(
        alt.Chart(pair).mark_bar().encode(
            y=alt.Y("ca:N", sort=sort_order, title=None),
            x=alt.X("n:Q", title=None),
            yOffset=alt.YOffset("what:N"),
            color=alt.Color("what:N", legend=None,
                            scale=alt.Scale(domain=["New meetings", "SAOs"],
                                            range=[ui.PURPLE, ui.LIME])),
            tooltip=["ca", "what", "n"],
        ).properties(height=h)),
        use_container_width=True)
    ui.centered_legend([("New meetings", ui.PURPLE), ("SAOs", ui.LIME)])
    st.caption("New meetings vs the SAOs they'll eventually produce (SAOs lag outreach).")
with c2:
    # deliberately NOT purple/lime — those mean new-meetings/SAOs on the left
    # chart; reusing them here (where the split is below-target / target-hit)
    # would read as the same thing. Own scale: light red below, strong red hit.
    st.markdown("**SAO target attainment**")
    att = j.dropna(subset=["attainment_pct"]).copy()
    att["hit"] = att.attainment_pct.map(lambda v: "Target hit" if v >= 100 else "Below target")
    st.altair_chart(ui.themed(
        alt.Chart(att).mark_bar().encode(
            x=alt.X("attainment_pct:Q", title="Attainment %"),
            y=alt.Y("ca:N", sort="-x", title=None),
            color=alt.Color("hit:N", legend=None,
                            scale=alt.Scale(domain=["Target hit", "Below target"],
                                            range=[RED_HIT, RED_MISS])),
            tooltip=["ca", "saos", "sao_target", "attainment_pct"],
        ).properties(height=h)),
        use_container_width=True)
    ui.centered_legend([("Target hit", RED_HIT), ("Below target", RED_MISS)])

# --- the full per-CA table below the charts ---------------------------------
show = j[["ca_name", "ramp", "total_counted", "emails", "dials", "conversations", "linkedin",
          "meetings_booked", "meetings_new_stakeholder", "saos", "sao_target",
          "attainment_pct", "saos_inbound", "saos_event", "saos_outbound",
          "pipeline_usd"]].copy()
for c in ["total_counted", "emails", "dials", "conversations", "linkedin",
          "meetings_booked", "meetings_new_stakeholder", "saos", "sao_target",
          "saos_inbound", "saos_event", "saos_outbound", "pipeline_usd"]:
    show[c] = show[c].round().astype("Int64")   # counts/$: whole numbers, no decimals
ACT = "#8A55F7"   # activity family: purple tint
OUT = "#B3E249"   # results family: lime tint
FAMS = {"total_counted": ACT, "emails": ACT, "dials": ACT, "conversations": ACT,
        "linkedin": ACT, "meetings_booked": ACT, "meetings_new_stakeholder": ACT,
        "saos": OUT, "sao_target": OUT, "attainment_pct": OUT,
        "saos_inbound": OUT, "saos_event": OUT, "saos_outbound": OUT,
        "pipeline_usd": OUT}
# family tints on the value columns, plus a red ramping marker kept in its OWN
# narrow column (the cell holds only "*", so ONLY the asterisk is red — the CA
# name stays default). Partial-character colouring isn't possible inside a
# single st.dataframe cell, so the marker lives beside the name, not in it.
styled = (show.sort_values("saos", ascending=False)
              .style.apply(ui.family_tints(show.columns, FAMS), axis=None)
              .map(lambda v: "color: %s; font-weight: 700" % ui.RAMP_RED
                             if v == "*" else "", subset=["ramp"]))
st.dataframe(
    styled, hide_index=True, use_container_width=True,
    column_config={
        "ca_name": st.column_config.TextColumn("CA", pinned=True),
        "ramp": st.column_config.TextColumn("", width="small",
                                            help="* = ramping"),
        "total_counted": st.column_config.NumberColumn("Activities", help=ui.DEFS["total_counted"]),
        "emails": "Emails", "dials": "Dials", "conversations": "Convos",
        "linkedin": "LinkedIn",
        "meetings_booked": st.column_config.NumberColumn("Meetings booked",
                                                         help=ui.DEFS["meetings_booked"]),
        "meetings_new_stakeholder": st.column_config.NumberColumn(
            "New meetings", help=ui.DEFS["meetings_new_stakeholder"]),
        "saos": st.column_config.NumberColumn("SAOs", help=ui.DEFS["saos"]),
        "sao_target": st.column_config.NumberColumn("Target", help=ui.DEFS["sao_target"]),
        "attainment_pct": st.column_config.NumberColumn("Attainment", format="%.0f%%"),
        "saos_inbound": "Inbound SAO", "saos_event": "Event SAO",
        "saos_outbound": st.column_config.NumberColumn("Outbound SAO",
                                                       help=ui.DEFS["saos_outbound"]),
        # "localized" groups thousands with commas and (on an int column) shows
        # no decimals; the $ lives in the header (printf "$%,d" is NOT honored —
        # Streamlit's number format has no comma flag, verified live 2026-07-21).
        "pipeline_usd": st.column_config.NumberColumn("Pipeline $", format="localized"),
    })
st.caption("Purple tint = activity (our data)")
st.caption("Lime tint = results (Ray's tracker)")
st.caption(":red[\\*] = ramping")

with st.expander("One CA, month by month (incl. months before activity tracking)"):
    rep = st.selectbox("CA", sorted(sao[sao.rep_name.isin(ms.ca_name)].rep_name.unique()))
    hist = sao[sao.rep_name == rep].sort_values("month").copy()
    hist["pipeline_usd"] = hist["pipeline_usd"].round().astype("Int64")  # whole $ -> no decimals
    st.dataframe(
        hist[["month", "saos", "sao_target", "saos_inbound", "saos_event", "pipeline_usd"]],
        hide_index=True, use_container_width=True,
        column_config={
            "month": st.column_config.DateColumn("Month", format="MMM YYYY"),
            "saos": "SAOs", "sao_target": "Target",
            "saos_inbound": "Inbound", "saos_event": "Event",
            "pipeline_usd": st.column_config.NumberColumn("Pipeline $", format="localized"),
        })
