"""App entry point — defines the sidebar navigation (with Material icons) and
runs the selected page. The page bodies live in pages/; this file stays named
Team_overview.py because that's the file Streamlit Cloud is configured to run.

Using st.navigation disables Streamlit's automatic pages/ discovery, so the
sidebar shows exactly the pages listed here, in this order, with these icons.
Each page still calls ui.setup()/st.set_page_config() itself (supported after
st.navigation). st.switch_page("pages/5_Raw_data.py") in the drill handoff
matches the path declared below, so the Raw-data jump keeps working."""
import os

import streamlit as st

# Encord logo at the top of the sidebar (above the nav). App-wide: set here in
# the entry file so it shows on every page. Guarded so the app still runs if the
# asset isn't present. Drop the PNG at dashboard/assets/encord_logo.png.
_LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "encord_logo.png")
if os.path.exists(_LOGO):
    st.logo(_LOGO, size="large")

PAGES = [
    st.Page("pages/0_Overview.py", title="Team overview",
            icon=":material/group:", default=True),
    st.Page("pages/1_Per_CA.py", title="Per CA",
            icon=":material/person:"),
    st.Page("pages/2_Account_coverage_&_neglects.py", title="Account coverage & neglects",
            icon=":material/hub:"),
    st.Page("pages/3_Trends_and_comps.py", title="Trends and comps",
            icon=":material/trending_up:"),
    st.Page("pages/4_SAO_vs_activity.py", title="SAO vs activity",
            icon=":material/adjust:"),
    st.Page("pages/5_Raw_data.py", title="Raw data",
            icon=":material/description:"),
]

st.navigation(PAGES).run()
