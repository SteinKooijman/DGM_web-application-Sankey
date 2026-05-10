"""
Aandacht nodig — overzichtspagina (placeholder).

Toont in één oogopslag welke diagnoses aandacht nodig hebben:
  - Ontwerp:        designs die aanpassing behoeven
  - Implementatie:  uitvoering die te veel afwijkt van het plan

Voor nu gevuld met dummy data; echte detectielogica volgt nog.
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.sidebar import inject_shared_css, render_sidebar_nav

st.set_page_config(
    page_title="Aandacht nodig | Sankey-Ideas",
    page_icon="⚠️",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_shared_css()
render_sidebar_nav()

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.title("⚠️ Aandacht nodig")
st.markdown(
    "Overzicht van diagnoses waar het ontwerp herzien moet worden of waar de "
    "implementatie te veel afwijkt van het plan — zodat niet alles handmatig "
    "doorgelopen hoeft te worden."
)

st.info("Placeholder — dummy data. Echte detectielogica volgt nog.")

# ---------------------------------------------------------------------------
# Section 1 — Ontwerp
# ---------------------------------------------------------------------------

st.markdown(
    '<div class="section-header">✏️ Ontwerp — designs die aanpassing nodig hebben</div>',
    unsafe_allow_html=True,
)

ontwerp_df = pd.DataFrame(
    [
        {"Diagnose": "Hypertensie",     "Reden": "Marge < drempel",                      "Laatst gewijzigd": "2026-04-12", "Status": "Te herzien"},
        {"Diagnose": "Diabetes Type 2", "Reden": "Vergoeding ontbreekt voor 3 producten", "Laatst gewijzigd": "2026-03-28", "Status": "Te herzien"},
        {"Diagnose": "COPD",            "Reden": "Geen ontwerp aanwezig",                "Laatst gewijzigd": "—",          "Status": "Ontbreekt"},
        {"Diagnose": "Astma",           "Reden": "Drempelwijziging in catalogus",        "Laatst gewijzigd": "2026-04-30", "Status": "Te herzien"},
    ]
)
st.dataframe(ontwerp_df, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Section 2 — Implementatie
# ---------------------------------------------------------------------------

st.markdown(
    '<div class="section-header">📈 Implementatie — afwijkingen van het plan</div>',
    unsafe_allow_html=True,
)

impl_df = pd.DataFrame(
    [
        {"Diagnose": "Hypertensie",     "Afwijking (%)": 18.4, "Belangrijkste verschil": "Meer ACE-remmer dan gepland",       "Periode": "2026-Q1"},
        {"Diagnose": "Diabetes Type 2", "Afwijking (%)": 27.1, "Belangrijkste verschil": "Metformine onderbenut",             "Periode": "2026-Q1"},
        {"Diagnose": "COPD",            "Afwijking (%)": 12.6, "Belangrijkste verschil": "LABA/ICS combinatie afwijkend",     "Periode": "2026-Q1"},
        {"Diagnose": "Astma",           "Afwijking (%)":  9.2, "Belangrijkste verschil": "Binnen tolerantie — informatief",  "Periode": "2026-Q1"},
    ]
)
st.dataframe(
    impl_df.style.format({"Afwijking (%)": "{:,.1f}"}),
    use_container_width=True,
    hide_index=True,
)
