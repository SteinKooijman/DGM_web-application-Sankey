"""
Implementatie — vergelijk een opgeslagen ontwerp met de data Sankey.

Naast elkaar:
  - links:  ontwerp (van geselecteerd JSON-bestand)
  - rechts: data Sankey (huidige filter, medicijn-niveau)

Daaronder:
  - KPI-strip Δ uitgaven / Δ vergoeding / Δ marge over alle afwijkende flows
  - Tabel met alle afwijkende flows
  - Voorschrijver-ranglijst (wie wijkt het meest af)
  - Tekstuele suggesties met de grootste financiële impact
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.csv_builder import load_catalogue_csv, load_patient_cache, load_sankey_csv
from backend.design_io import design_to_flows_df, list_designs, load_design
from backend.divergence import compute_divergence, voorschrijver_breakdown
from backend.sidebar import (
    ensure_sankey_current,
    filter_records_in_window,
    inject_shared_css,
    render_sidebar,
)
from sankey.sankey_from_csv import aggregate_flows, build_sankey_from_csv

st.set_page_config(
    page_title="Implementatie | Sankey-Ideas",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_shared_css()


def _fmt_eur(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"€ {v/1_000_000:,.2f}M"
    return f"€ {v:,.0f}"


# ---------------------------------------------------------------------------
# Data + sidebar
# ---------------------------------------------------------------------------

cache_df     = load_patient_cache()
catalogue_df = load_catalogue_csv()

state = render_sidebar(cache_df)
if state is None:
    st.stop()

selected_diag = state["selected_diag"]
regen_start   = state["regen_start"]
regen_end     = state["regen_end"]
regen_vrs     = state["regen_vrs"]

ensure_sankey_current(
    cache_df, selected_diag, regen_start, regen_end, regen_vrs, grain="medicine_name",
)

# ---------------------------------------------------------------------------
# Header + design selector
# ---------------------------------------------------------------------------

st.markdown(f"### 🔍 Implementatie — {selected_diag}")

designs = list_designs(selected_diag)
if not designs:
    st.warning(
        "Nog geen opgeslagen ontwerp voor deze diagnose. "
        "Ga naar de Ontwerp-pagina en sla er één op."
    )
    st.page_link("pages/2_Ontwerp.py", label="✏️ Naar Ontwerp", icon="✏️")
    st.stop()

opts = [f"{d['filename']}  ·  {d['created_at']}" for d in designs]
sel_idx = st.selectbox(
    "Kies een ontwerp om te vergelijken",
    list(range(len(designs))),
    format_func=lambda i: opts[i],
    key="impl_design_sel",
)
design = load_design(designs[sel_idx]["path"])

# ---------------------------------------------------------------------------
# Side-by-side Sankeys
# ---------------------------------------------------------------------------

data_flows_raw = load_sankey_csv(edited=True, grain="medicine_name")
data_flows_diag = data_flows_raw[data_flows_raw["diag_omschr_euk"] == selected_diag]
data_agg = aggregate_flows(data_flows_diag, level="medicine_name")

design_flows = design_to_flows_df(design)

cl, cr = st.columns(2, gap="large")
with cl:
    st.markdown('<div class="section-header">Ontwerp</div>', unsafe_allow_html=True)
    fig_design = build_sankey_from_csv(
        flows_df=design_flows,
        level="medicine_name",
        diag_omschr_euk=f"{selected_diag} (ontwerp)",
    )
    st.plotly_chart(fig_design, use_container_width=True, key="impl_sankey_design")
with cr:
    st.markdown('<div class="section-header">Data (huidige filter)</div>',
                unsafe_allow_html=True)
    fig_data = build_sankey_from_csv(
        flows_df=data_agg,
        level="medicine_name",
        diag_omschr_euk=f"{selected_diag} (data)",
    )
    st.plotly_chart(fig_data, use_container_width=True, key="impl_sankey_data")

# ---------------------------------------------------------------------------
# Divergence analysis
# ---------------------------------------------------------------------------

st.markdown("---")
st.markdown('<div class="section-header">Financiële impact van afwijkingen</div>',
            unsafe_allow_html=True)

pat_window = filter_records_in_window(
    cache_df, selected_diag, regen_start, regen_end, regen_vrs
)
divergence = compute_divergence(design, data_agg, pat_window, catalogue_df)

if divergence.empty:
    st.info("Geen overlappende flows tussen ontwerp en data om te vergelijken.")
    st.stop()

# Materially divergent flows = > 0.5 percentpunt afwijking
material = divergence[divergence["delta_share"].abs() > 0.5].copy()

tot_uit  = float(material["delta_uitgaven"].sum())
tot_verg = float(material["delta_vergoeding"].sum())
tot_mar  = float(material["delta_marge"].sum())

k1, k2, k3 = st.columns(3)
with k1:
    st.metric("Δ Uitgaven (data − ontwerp)",   _fmt_eur(tot_uit),  delta_color="inverse")
with k2:
    st.metric("Δ Vergoeding (data − ontwerp)", _fmt_eur(tot_verg))
with k3:
    st.metric("Δ Marge (data − ontwerp)",      _fmt_eur(tot_mar))
st.caption(
    "Negatieve marge betekent: de praktijk wijkt af van het ontwerp en dat "
    "kost marge. Berekening: Δ behandeldagen × (data marge/dag − ontwerp marge/dag)."
)

# ── Tabel afwijkende flows ────────────────────────────────────────────────
st.markdown('<div class="section-header">Afwijkende flows</div>',
            unsafe_allow_html=True)

if material.empty:
    st.info("Geen materieel afwijkende flows (drempel 0.5 procentpunt).")
else:
    disp = material.copy()
    disp["Bron"]            = disp["source"]
    disp["Doel"]            = disp["target"]
    disp["Ontwerp %"]       = disp["design_share"].round(1)
    disp["Actueel %"]       = disp["actual_share"].round(1)
    disp["Δ %"]             = disp["delta_share"].round(1)
    disp["Δ behandeldagen"] = disp["delta_days"].round(0)
    disp["Δ uitgaven"]      = disp["delta_uitgaven"]
    disp["Δ vergoeding"]    = disp["delta_vergoeding"]
    disp["Δ marge"]         = disp["delta_marge"]
    disp = disp[["Bron", "Doel", "Ontwerp %", "Actueel %", "Δ %",
                  "Δ behandeldagen", "Δ uitgaven", "Δ vergoeding", "Δ marge"]]
    st.dataframe(
        disp.style.format({
            "Ontwerp %":      "{:.1f}%",
            "Actueel %":      "{:.1f}%",
            "Δ %":            "{:+.1f}%",
            "Δ behandeldagen": "{:+,.0f}",
            "Δ uitgaven":     "€ {:+,.0f}",
            "Δ vergoeding":   "€ {:+,.0f}",
            "Δ marge":        "€ {:+,.0f}",
        }),
        use_container_width=True,
        hide_index=True,
    )

# ── Voorschrijver-ranglijst ───────────────────────────────────────────────
st.markdown("---")
st.markdown('<div class="section-header">Voorschrijver-ranglijst — wie wijkt het meest af</div>',
            unsafe_allow_html=True)

# Voor de ranglijst gebruiken we ALLE voorschrijvers in het venster, niet
# alleen de huidige sidebar-keuze; daarom filteren we alleen op diagnose+venster.
pat_all_vrs = filter_records_in_window(
    cache_df, selected_diag, regen_start, regen_end, "Alle voorschrijvers"
)
vrs_df = voorschrijver_breakdown(design, pat_all_vrs, catalogue_df)

if vrs_df.empty:
    st.info("Onvoldoende data om voorschrijvers te vergelijken.")
else:
    fig_vrs = px.bar(
        vrs_df.head(15),
        x="abs_marge",
        y="voorschrijver",
        orientation="h",
        labels={"abs_marge": "|Δ marge| (€)", "voorschrijver": ""},
        height=max(280, 28 * min(len(vrs_df), 15) + 80),
        text=vrs_df.head(15)["abs_marge"].round(0),
    )
    fig_vrs.update_traces(
        marker_color="#F59E0B",
        texttemplate="€ %{x:,.0f}",
        textposition="outside",
        cliponaxis=False,
    )
    fig_vrs.update_layout(
        margin=dict(l=10, r=70, t=10, b=10),
        paper_bgcolor="#F6F8FA",
        plot_bgcolor="white",
        showlegend=False,
        yaxis=dict(autorange="reversed"),
        xaxis=dict(showgrid=True, gridcolor="#E5E7EB"),
        font=dict(size=11, family="Inter, sans-serif"),
    )
    st.plotly_chart(fig_vrs, use_container_width=True)

    with st.expander("Volledig overzicht per voorschrijver", expanded=False):
        disp_v = vrs_df.copy()
        disp_v["Voorschrijver"]    = disp_v["voorschrijver"]
        disp_v["Totaal dagen"]     = disp_v["totaal_dagen"].round(0)
        disp_v["Δ uitgaven"]       = disp_v["delta_uitgaven"]
        disp_v["Δ vergoeding"]     = disp_v["delta_vergoeding"]
        disp_v["Δ marge"]          = disp_v["delta_marge"]
        st.dataframe(
            disp_v[["Voorschrijver", "Totaal dagen",
                    "Δ uitgaven", "Δ vergoeding", "Δ marge"]].style.format({
                "Totaal dagen": "{:,.0f}",
                "Δ uitgaven":   "€ {:+,.0f}",
                "Δ vergoeding": "€ {:+,.0f}",
                "Δ marge":      "€ {:+,.0f}",
            }),
            use_container_width=True,
            hide_index=True,
        )

# ── Suggesties ────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown('<div class="section-header">Suggesties: waar kon meer marge gehaald worden?</div>',
            unsafe_allow_html=True)

# Positive Δ marge means actual margin > design — design itself was less profitable;
# we want flows where the DESIGN is more profitable than the actual: that's where
# delta_marge is NEGATIVE (data − design < 0).
suggestions = material[material["delta_marge"] < 0].copy()
suggestions = suggestions.sort_values("delta_marge").head(8)

if suggestions.empty:
    st.info("Geen flows waar het ontwerp meer marge had opgeleverd dan de praktijk.")
else:
    for _, row in suggestions.iterrows():
        days = abs(row["delta_days"])
        marge_lost = abs(row["delta_marge"])
        st.markdown(
            f"- Behandeling van **{row['target']}** is in de praktijk "
            f"{row['actual_share']:.1f}% i.p.v. ontwerp-aandeel "
            f"{row['design_share']:.1f}% (vanuit *{row['source']}*). "
            f"Dat is **{days:,.0f} behandeldagen** anders en "
            f"kostte naar schatting **€ {marge_lost:,.0f} marge**."
        )
