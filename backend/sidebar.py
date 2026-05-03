"""
Shared sidebar widget used by all per-diagnosis pages.

The sidebar exposes:
  - Diagnose
  - Datum (start, eind)
  - Voorschrijver

Returns a dict with the selected values and writes them to st.session_state
so other widgets / pages can read them without re-rendering the sidebar.

Sankey CSV regeneration is triggered as a side-effect when the filter combo
changes — the on-disk sankey_original.csv / sankey_edited.csv are kept in
sync with the current filter window so any page (Samenvatting / Ontwerp /
Implementatie) reads consistent flows.
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.csv_builder import generate_sankey_csv

ALL_DIAGNOSES = "Alle diagnoses"


SHARED_CSS = """
<style>
[data-testid="stAppViewContainer"] { background-color: #F6F8FA; }
[data-testid="stSidebarNav"]       { display: none; }
[data-testid="stSidebar"]          { background-color: #003545 !important; }
[data-testid="stSidebar"] *        { color: #E0F2FE !important; }
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] textarea                       { color: #003545 !important; }
[data-testid="stSidebar"] button                         { color: #003545 !important; }
[data-testid="stSidebar"] [data-baseweb="select"] *      { color: #003545 !important; }
[data-testid="stSidebar"] [data-baseweb="input"] *       { color: #003545 !important; }
[data-testid="stSidebar"] [data-baseweb="datepicker"] *  { color: #003545 !important; }
[data-testid="stSidebar"] [data-testid="stAlert"],
[data-testid="stSidebar"] [data-testid="stAlert"] *      { color: inherit !important; }
h1, h2, h3 { color: #0F1923 !important; }
hr { border-color: #E5E7EB !important; }

.kpi-card {
    background: white; border-radius: 12px; padding: 18px 22px;
    box-shadow: 0 1px 6px rgba(0,0,0,.08); border-top: 4px solid #29B5E8;
    min-height: 110px;
}
.kpi-card .label { font-size: 0.78rem; font-weight: 600; color: #64748B;
                   text-transform: uppercase; letter-spacing: .04em; }
.kpi-card .value { font-size: 1.65rem; font-weight: 700; color: #0F1923; margin: 4px 0; }
.kpi-card .delta { font-size: 0.82rem; font-weight: 600; }
.kpi-card .delta.pos { color: #16a34a; }
.kpi-card .delta.neg { color: #dc2626; }

.info-box {
    background: #F0F9FF; border-left: 4px solid #29B5E8;
    border-radius: 8px; padding: 10px 16px;
    font-size: 0.88rem; color: #003545;
}
.warn-box {
    background: #FFF7ED; border-left: 4px solid #F59E0B;
    border-radius: 8px; padding: 10px 16px;
    font-size: 0.88rem; color: #7C2D12;
}
.section-header { font-size: 1.05rem; font-weight: 700; color: #11567F; margin-bottom: 4px; }

/* ── Floating AI chat widget on the Ontwerp page ──────────────────────── */
.st-key-chat_fab_open button {
    border-radius: 50% !important;
    width: 60px !important; height: 60px !important;
    min-height: 60px !important;
    font-size: 1.6rem !important;
    background: #003545 !important; color: #E0F2FE !important;
    border: none !important;
    box-shadow: 0 6px 18px rgba(0,0,0,.25);
    padding: 0 !important;
}
.st-key-chat_fab_open button:hover {
    background: #11567F !important; color: #FFFFFF !important;
}
.st-key-chat_fab_close button {
    border-radius: 50% !important;
    width: 32px !important; height: 32px !important;
    min-height: 32px !important;
    background: transparent !important;
    border: none !important;
    color: #64748B !important;
    padding: 0 !important;
}
.st-key-chat_fab_close button:hover {
    background: #F1F5F9 !important; color: #003545 !important;
}
/* Style the floating vertical block that holds the chat panel itself.
   We identify it by the presence of the close-button (only the panel has it). */
[data-testid="stVerticalBlock"]:has(> div .st-key-chat_fab_close) {
    background: #FFFFFF !important;
    border-radius: 14px !important;
    box-shadow: 0 10px 30px rgba(0,0,0,.22) !important;
    border: 1px solid #E5E7EB !important;
    padding: 10px 14px 8px 14px !important;
}
.chat-header {
    display: flex; justify-content: space-between; align-items: center;
    padding-bottom: 6px; margin-bottom: 6px;
    border-bottom: 1px solid #E5E7EB;
}
.chat-header .title {
    font-weight: 700; color: #003545; font-size: 0.95rem;
}
</style>
"""


def inject_shared_css() -> None:
    st.markdown(SHARED_CSS, unsafe_allow_html=True)


def render_sidebar(cache_df: pd.DataFrame) -> dict | None:
    """
    Render the shared sidebar (diagnose / datum / voorschrijver).

    Returns None when there is no patient data yet (caller should st.stop()).
    """
    # Touch widget keys so Streamlit doesn't drop them when the user
    # navigates to a page that doesn't render these widgets (e.g. Upload).
    for _k in ("diag_sel", "regen_start_date", "regen_end_date", "vrs_sel"):
        if _k in st.session_state:
            st.session_state[_k] = st.session_state[_k]

    with st.sidebar:
        st.page_link("pages/4_Upload.py", label="📂 Upload", icon="📂")
        st.markdown("---")
        st.markdown("## ⚗️ Sankey-Ideas")
        st.markdown("---")

        if cache_df.empty:
            st.warning("Geen patiëntendata gevonden.")
            st.page_link("pages/4_Upload.py", label="📂 Ga naar Upload", icon="📂")
            return None

        diagnoses = sorted(cache_df["Diag_omschr_EUK"].dropna().unique().tolist())
        if not diagnoses:
            st.warning("Geen diagnoses gevonden in patiëntendata.")
            return None

        diag_options = [ALL_DIAGNOSES] + diagnoses
        prev_diag    = st.session_state.get("selected_diag", diag_options[0])
        default_idx  = diag_options.index(prev_diag) if prev_diag in diag_options else 0
        selected_diag: str = st.selectbox("Diagnose", diag_options, index=default_idx,
                                          key="diag_sel")

        if selected_diag != st.session_state.get("selected_diag"):
            st.session_state["selected_diag"] = selected_diag
            st.session_state["selected_node"] = None
            st.session_state["selected_link"] = None
            st.session_state["_filter_key"]   = None
            st.session_state["design_state"]  = None  # reset Ontwerp draft

        is_all_diag = selected_diag == ALL_DIAGNOSES
        diag_idx = -1 if is_all_diag else diagnoses.index(selected_diag)

        if is_all_diag:
            diag_df = cache_df
        else:
            diag_df = cache_df[cache_df["Diag_omschr_EUK"].astype(str) == selected_diag]

        # Canonical persist-keys for dates — survive widget unmount on pages
        # that don't render these widgets (Upload). The widget keys re-seed
        # from these on remount.
        _data_min = (
            cache_df["Datum"].min().date()
            if "Datum" in cache_df.columns and not cache_df.empty else None
        )
        _data_max = (
            cache_df["Datum"].max().date()
            if "Datum" in cache_df.columns and not cache_df.empty else None
        )
        st.session_state.setdefault("persist_start_date", _data_min)
        st.session_state.setdefault("persist_end_date",   _data_max)

        if "regen_start_date" not in st.session_state:
            st.session_state["regen_start_date"] = st.session_state["persist_start_date"]
        if "regen_end_date" not in st.session_state:
            st.session_state["regen_end_date"]   = st.session_state["persist_end_date"]

        st.markdown("---")
        sd_col, ed_col = st.columns(2)
        with sd_col:
            regen_start = st.date_input("Start", key="regen_start_date")
        with ed_col:
            regen_end = st.date_input("Eind", key="regen_end_date")
        st.session_state["persist_start_date"] = regen_start
        st.session_state["persist_end_date"]   = regen_end

        vrs_opts = ["Alle voorschrijvers"]
        if "Voorschrijver_nm" in diag_df.columns:
            vrs_opts += sorted(diag_df["Voorschrijver_nm"].dropna().unique().tolist())

        prev_vrs = st.session_state.get("persist_vrs", "Alle voorschrijvers")
        if "vrs_sel" not in st.session_state or st.session_state["vrs_sel"] not in vrs_opts:
            st.session_state["vrs_sel"] = prev_vrs if prev_vrs in vrs_opts else "Alle voorschrijvers"
        regen_vrs: str = st.selectbox("Voorschrijver", vrs_opts, key="vrs_sel")
        st.session_state["persist_vrs"] = regen_vrs

        st.markdown("---")
        st.page_link("app.py",                     label="📊 Samenvatting",  icon="📊")
        st.page_link("pages/2_Ontwerp.py",         label="✏️ Ontwerp",       icon="✏️")
        st.page_link("pages/3_Implementatie.py",   label="🔍 Implementatie", icon="🔍")

    return {
        "selected_diag": selected_diag,
        "regen_start":   regen_start,
        "regen_end":     regen_end,
        "regen_vrs":     regen_vrs,
        "diag_idx":      diag_idx,
    }


def ensure_sankey_current(
    cache_df: pd.DataFrame,
    selected_diag: str,
    regen_start,
    regen_end,
    regen_vrs: str,
    grain: str = "prod_id",
) -> None:
    """
    Regenerate sankey_*_<grain>.csv when the (diag, dates, voorschrijver, grain)
    combo changes. Idempotent: cheap when nothing changed.

    The cache key is per-grain, so switching from medicine_name → group_name
    triggers a regeneration for the new grain (and won't re-regen the old one
    again unless its filters changed).
    """
    filter_key = (selected_diag, str(regen_start), str(regen_end), regen_vrs, grain)
    cache = st.session_state.setdefault("_filter_keys_by_grain", {})
    if cache.get(grain) == filter_key:
        return

    with st.spinner(f"Sankey berekenen ({grain}) …"):
        generate_sankey_csv(
            pat_df=cache_df,
            diag_omschr_euk=selected_diag,
            start_date=regen_start,
            end_date=regen_end,
            voorschrijver_nm=regen_vrs,
            append=True,
            grain=grain,
        )
    cache[grain] = filter_key
    st.session_state["_filter_keys_by_grain"] = cache
    st.session_state["selected_node"] = None
    st.session_state["selected_link"] = None


def filter_records_in_window(
    cache_df: pd.DataFrame,
    selected_diag: str,
    regen_start,
    regen_end,
    regen_vrs: str,
) -> pd.DataFrame:
    """
    Records (not patients) for `selected_diag` whose Datum falls in
    [regen_start, regen_end], optionally narrowed to one voorschrijver.

    This is what KPI totals + per-product totals on the Summary page consume —
    it includes ongoing patients started before the window, unlike the
    new-patient filter that drives the Sankey.
    """
    if cache_df.empty:
        return cache_df.copy()

    df = cache_df
    if selected_diag != ALL_DIAGNOSES and "Diag_omschr_EUK" in df.columns:
        df = df[df["Diag_omschr_EUK"].astype(str) == selected_diag]
    if "Datum" in df.columns:
        start_ts = pd.Timestamp(regen_start)
        end_ts   = pd.Timestamp(regen_end)
        df = df[(df["Datum"] >= start_ts) & (df["Datum"] <= end_ts)]
    if regen_vrs != "Alle voorschrijvers" and "Voorschrijver_nm" in df.columns:
        df = df[df["Voorschrijver_nm"].astype(str) == regen_vrs]
    return df.copy()
