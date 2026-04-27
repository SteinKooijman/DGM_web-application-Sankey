"""
Sankey-Ideas  —  Live filter edition
-------------------------------------
Patient data and product catalogue are uploaded once via the Upload page
(pages/Upload.py).  This page reads the cached patient data and always
computes the Sankey live from the sidebar filters (diagnosis, date range,
voorschrijver).  No pre-baked Sankey CSV is needed.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.csv_builder import (
    CATALOGUE_CSV,
    LEVEL_COLS,
    SANKEY_EDITED_CSV,
    SANKEY_ORIGINAL_CSV,
    cascade_and_save_flow,
    generate_sankey_csv,
    get_node_capacity,
    load_catalogue_csv,
    load_patient_cache,
    load_sankey_csv,
    reset_edited_for_diagnosis,
)
from backend.feedback_utils import load_feedback, save_idea
from backend.financial_utils import compute_kpis_from_patients
from sankey.sankey_from_csv import aggregate_flows, build_sankey_from_csv

# ---------------------------------------------------------------------------
# Page config + styling
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Sankey-Ideas | Apotheek",
    page_icon="⚗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    [data-testid="stAppViewContainer"] { background-color: #F6F8FA; }
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
    .section-header { font-size: 1.05rem; font-weight: 700; color: #11567F; margin-bottom: 4px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LEVEL_LABELS = {
    "prod_id":       "Prod_ID (meest gedetailleerd)",
    "prod_nm":       "Prod_nm",
    "medicine_name": "Medicijn",
    "group_name":    "Therapeutische groep",
}

REDEN_OPTIONS = sorted([
    "Volgens richtlijn", "Financieel voordeel", "Patiëntvoorkeur",
    "Te hoge stijging in uitgaven en/of vergoedingen", "Actie verkoop", "Overig",
])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_eur(val: float) -> str:
    if abs(val) >= 1_000_000:
        return f"€ {val/1_000_000:,.2f}M"
    return f"€ {val:,.0f}"


def _delta_html(delta: float, invert: bool = False) -> str:
    if abs(delta) < 1:
        return ""
    good  = (delta > 0) != invert
    cls   = "pos" if good else "neg"
    arrow = "▲" if delta > 0 else "▼"
    return f'<span class="delta {cls}">{arrow} {_fmt_eur(abs(delta))}</span>'


def _kpi_card(label: str, value: float, delta: float = 0.0, invert: bool = False) -> str:
    return (
        f'<div class="kpi-card">'
        f'<div class="label">{label}</div>'
        f'<div class="value">{_fmt_eur(value)}</div>'
        f'<div>{_delta_html(delta, invert)}</div>'
        f'</div>'
    )


def _norm_pid(val) -> str:
    try:
        return str(int(float(str(val))))
    except (ValueError, TypeError):
        return ""


def compute_per_product_days(
    pat_df: pd.DataFrame,
    cat_df: pd.DataFrame,
    diag_omschr_euk: str,
    level: str,
) -> pd.DataFrame:
    """
    Compute total Aantal and behandeldagen per node label at the chosen
    aggregation level.  Days are derived per Prod_ID (Aantal / catalogue
    stuks_dag) and then aggregated up to the requested level — this avoids
    losing precision when multiple Prod_IDs share a Prod_nm with different
    stuks_dag values.

    Returns a DataFrame with columns: label, aantal, days, sorted by days desc.
    """
    if pat_df.empty or cat_df.empty:
        return pd.DataFrame(columns=["label", "aantal", "days"])

    # Sum Aantal per Prod_ID, capture grouping labels
    pat = pat_df.copy()
    pat["pid_norm"] = pat["Prod_ID"].apply(_norm_pid)
    grouped = pat.groupby("pid_norm").agg(
        aantal=("Aantal",   "sum"),
        prod_nm=("Prod_nm", "first"),
        med_nm=("Med_nm",   "first"),
        group_nm=("Group_nm", "first"),
    ).reset_index()

    # Catalogue lookup, scoped to this diagnosis when possible
    cat = cat_df.copy()
    cat["prod_id"] = cat["prod_id"].astype(str).str.strip()
    if "diag_omschr_euk" in cat.columns:
        cat_diag = cat[cat["diag_omschr_euk"].astype(str) == diag_omschr_euk]
        if not cat_diag.empty:
            cat = cat_diag
    cat_lup = cat.drop_duplicates("prod_id").set_index("prod_id")

    # Compute days per prod_id; drop rows where stuks_dag is unknown/zero
    def _days(row) -> float | None:
        pid = row["pid_norm"]
        if not pid or pid not in cat_lup.index:
            return None
        std_raw = cat_lup.loc[pid].get("stuks_dag", 0)
        try:
            std = float(std_raw)
        except (TypeError, ValueError):
            return None
        if std <= 0 or std != std:  # NaN or non-positive
            return None
        return float(row["aantal"]) / std

    grouped["days"] = grouped.apply(_days, axis=1)
    grouped = grouped.dropna(subset=["days"])
    if grouped.empty:
        return pd.DataFrame(columns=["label", "aantal", "days"])

    # Roll up to the chosen level
    label_col = {
        "prod_id":       "pid_norm",
        "prod_nm":       "prod_nm",
        "medicine_name": "med_nm",
        "group_name":    "group_nm",
    }.get(level, "prod_nm")

    out = (
        grouped.groupby(label_col)
        .agg(aantal=("aantal", "sum"), days=("days", "sum"))
        .reset_index()
        .rename(columns={label_col: "label"})
    )
    out["label"] = out["label"].astype(str).str.strip().replace("", "?")
    return out.sort_values("days", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Load patient cache + catalogue
# ---------------------------------------------------------------------------

cache_df     = load_patient_cache()
catalogue_df = load_catalogue_csv()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## ⚗️ Sankey-Ideas")
    st.markdown("---")

    # ── No data yet ─────────────────────────────────────────────────────────
    if cache_df.empty:
        st.warning("Geen patiëntendata gevonden.")
        st.page_link("pages/Upload.py", label="📂 Ga naar Upload", icon="📂")
        st.stop()

    # ── Diagnose ─────────────────────────────────────────────────────────────
    diagnoses = sorted(cache_df["Diag_omschr_EUK"].dropna().unique().tolist())
    if not diagnoses:
        st.warning("Geen diagnoses gevonden in patiëntendata.")
        st.stop()

    prev_diag   = st.session_state.get("selected_diag", diagnoses[0])
    default_idx = diagnoses.index(prev_diag) if prev_diag in diagnoses else 0
    selected_diag: str = st.selectbox("Diagnose", diagnoses, index=default_idx, key="diag_sel")

    # Reset all per-diagnosis state when diagnosis changes
    if selected_diag != st.session_state.get("selected_diag"):
        st.session_state["selected_diag"] = selected_diag
        st.session_state["selected_node"] = None
        st.session_state["selected_link"] = None
        st.session_state["kpi_baseline"]  = None
        st.session_state["_filter_key"]   = None
        # Clear per-diagnosis voorschrijver key (options differ per diagnosis);
        # dates are global and intentionally preserved across diagnosis switches.
        diag_idx_old = st.session_state.get("_diag_idx", -1)
        st.session_state.pop(f"regen_vrs_{diag_idx_old}", None)

    diag_idx = diagnoses.index(selected_diag)
    st.session_state["_diag_idx"] = diag_idx

    # ── Date range + voorschrijver ────────────────────────────────────────────
    diag_df = cache_df[cache_df["Diag_omschr_EUK"].astype(str) == selected_diag]

    # Global date defaults — initialised once from overall cache range so they
    # persist when the user switches between diagnoses.
    if "regen_start_date" not in st.session_state:
        st.session_state["regen_start_date"] = (
            cache_df["Datum"].min().date()
            if "Datum" in cache_df.columns and not cache_df.empty else None
        )
    if "regen_end_date" not in st.session_state:
        st.session_state["regen_end_date"] = (
            cache_df["Datum"].max().date()
            if "Datum" in cache_df.columns and not cache_df.empty else None
        )

    st.markdown("---")

    sd_col, ed_col = st.columns(2)
    with sd_col:
        regen_start = st.date_input("Start", key="regen_start_date")
    with ed_col:
        regen_end = st.date_input("Eind", key="regen_end_date")

    vrs_opts = ["Alle voorschrijvers"]
    if "Voorschrijver_nm" in diag_df.columns:
        vrs_opts += sorted(diag_df["Voorschrijver_nm"].dropna().unique().tolist())
    regen_vrs: str = st.selectbox(
        "Voorschrijver", vrs_opts,
        key=f"regen_vrs_{diag_idx}",
    )

    # ── Weergaveniveau ───────────────────────────────────────────────────────
    st.markdown("---")
    _level_pairs   = sorted(LEVEL_LABELS.items(), key=lambda x: x[1])
    level_keys     = [k for k, _ in _level_pairs]
    level_names    = [v for _, v in _level_pairs]
    prev_level     = st.session_state.get("level", "medicine_name")
    level_idx      = level_keys.index(prev_level) if prev_level in level_keys else level_keys.index("medicine_name")
    sel_level_name = st.selectbox("Weergaveniveau", level_names, index=level_idx, key="level_sel")
    level: str     = level_keys[level_names.index(sel_level_name)]
    if level != st.session_state.get("level"):
        st.session_state["level"]         = level
        st.session_state["selected_node"] = None
        st.session_state["selected_link"] = None

    st.markdown("---")

    if st.button("↺ Herstel origineel", use_container_width=True):
        reset_edited_for_diagnosis(selected_diag)
        st.session_state.update({"selected_node": None, "selected_link": None,
                                  "kpi_baseline": None})
        st.rerun()


# ---------------------------------------------------------------------------
# Auto-compute Sankey when filter state changes
# ---------------------------------------------------------------------------

filter_key = (selected_diag, str(regen_start), str(regen_end), regen_vrs)

if st.session_state.get("_filter_key") != filter_key:
    with st.spinner("Sankey berekenen …"):
        generate_sankey_csv(
            pat_df=cache_df,
            diag_omschr_euk=selected_diag,
            start_date=regen_start,
            end_date=regen_end,
            voorschrijver_nm=regen_vrs,
            append=True,
        )
    st.session_state["_filter_key"]  = filter_key
    st.session_state["kpi_baseline"] = None
    st.session_state["selected_node"] = None
    st.session_state["selected_link"] = None

# ---------------------------------------------------------------------------
# Load computed flows
# ---------------------------------------------------------------------------

level         = st.session_state.get("level", "medicine_name")
selected_diag = st.session_state.get("selected_diag", diagnoses[0])

all_flows  = load_sankey_csv(edited=True)
flows_diag = all_flows[all_flows["diag_omschr_euk"] == selected_diag].copy()

orig_flows = load_sankey_csv(edited=False)
orig_diag  = orig_flows[orig_flows["diag_omschr_euk"] == selected_diag].copy()

flows_agg = aggregate_flows(flows_diag, level)
orig_agg  = aggregate_flows(orig_diag,  level)

# ── Filter patient data to the current sidebar selection ─────────────────
# Uses the SAME patient-selection logic as the Sankey generator
# (_apply_diag_voorschrijver_time_filter): select patients whose first-ever
# record falls within [regen_start, regen_end] AND who have the selected
# diagnosis AND voorschrijver, then return ALL records for those patients.
# Filtering individual records by date (as was done before) produced a far
# larger dataset than the Sankey uses and caused KPI/Sankey mismatches.
def _filter_patients(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "Pat_id" not in df.columns:
        return df.copy()

    # 1. Patients who have this diagnosis (at any date)
    if "Diag_omschr_EUK" in df.columns:
        pat_with_diag = set(
            df[df["Diag_omschr_EUK"].astype(str) == selected_diag]["Pat_id"].unique()
        )
    else:
        pat_with_diag = set(df["Pat_id"].unique())

    # 2. Patients whose very first record is within [regen_start, regen_end]
    #    (mirrors Start_Datum_pat logic in _apply_diag_voorschrijver_time_filter)
    if "Datum" in df.columns:
        start_ts = pd.Timestamp(regen_start)
        end_ts   = pd.Timestamp(regen_end)
        first_date = df.groupby("Pat_id")["Datum"].min()
        pat_in_range = set(
            first_date[(first_date >= start_ts) & (first_date <= end_ts)].index
        )
    else:
        pat_in_range = set(df["Pat_id"].unique())

    # 3. Patients with the selected voorschrijver (if filter active)
    if regen_vrs != "Alle voorschrijvers" and "Voorschrijver_nm" in df.columns:
        pat_with_vrs = set(
            df[df["Voorschrijver_nm"].astype(str) == regen_vrs]["Pat_id"].unique()
        )
    else:
        pat_with_vrs = set(df["Pat_id"].unique())

    qualifying = pat_with_diag & pat_in_range & pat_with_vrs
    return df[df["Pat_id"].isin(qualifying)].copy()


pat_filtered = _filter_patients(cache_df)

# KPI baseline: all patients with this diagnosis whose first-ever record falls
# within the full cache date range (same patient-selection principle, no extra filter).
if st.session_state.get("kpi_baseline") is None:
    if "Diag_omschr_EUK" in cache_df.columns and "Pat_id" in cache_df.columns:
        pat_with_diag_all = set(
            cache_df[cache_df["Diag_omschr_EUK"].astype(str) == selected_diag]["Pat_id"].unique()
        )
        pat_baseline = cache_df[cache_df["Pat_id"].isin(pat_with_diag_all)].copy()
    else:
        pat_baseline = cache_df.copy()
    st.session_state["kpi_baseline"] = compute_kpis_from_patients(
        pat_baseline, catalogue_df, selected_diag, filter_to_diag=False
    )

baseline       = st.session_state["kpi_baseline"]
current_totals = compute_kpis_from_patients(pat_filtered, catalogue_df, selected_diag, filter_to_diag=False)

# ---------------------------------------------------------------------------
# KPI bar
# ---------------------------------------------------------------------------

# Build subtitle: active filter summary
vrs_label = regen_vrs if regen_vrs != "Alle voorschrijvers" else "alle voorschrijvers"
st.markdown(
    f"### 📊 Financieel overzicht — {selected_diag}  "
    f"<small style='font-weight:400;color:#64748B;'>  {regen_start} → {regen_end} · {vrs_label}</small>",
    unsafe_allow_html=True,
)

kc1, kc2, kc3 = st.columns(3)
with kc1:
    st.markdown(_kpi_card("Vergoeding / jaar", current_totals["vergoeding"],
                           current_totals["vergoeding"] - baseline["vergoeding"]),
                unsafe_allow_html=True)
with kc2:
    st.markdown(_kpi_card("Uitgaven / jaar", current_totals["uitgaven"],
                           current_totals["uitgaven"] - baseline["uitgaven"], invert=True),
                unsafe_allow_html=True)
with kc3:
    st.markdown(_kpi_card("Marge / jaar", current_totals["marge"],
                           current_totals["marge"] - baseline["marge"]),
                unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Two-column layout
# ---------------------------------------------------------------------------

col_sankey, col_panel = st.columns([3, 2], gap="large")

# ════════════════════════════════════════════════════════════════════════════
# LEFT: Sankey
# ════════════════════════════════════════════════════════════════════════════
with col_sankey:
    level_display = LEVEL_LABELS.get(level, level)
    st.markdown(f'<div class="section-header">Patiëntenstroom ({level_display})</div>',
                unsafe_allow_html=True)

    fig = build_sankey_from_csv(
        flows_df=flows_agg,
        level=level,
        selected_node=st.session_state.get("selected_node"),
        selected_link=st.session_state.get("selected_link"),
        diag_omschr_euk=selected_diag,
    )

    event = st.plotly_chart(fig, use_container_width=True, on_select="rerun",
                            key="sankey_chart")

    # ── Parse click event ────────────────────────────────────────────────
    if event and hasattr(event, "selection"):
        sel    = event.selection
        points = sel.get("points", []) if isinstance(sel, dict) else getattr(sel, "points", [])
        if points:
            pt = points[0]
            cd = pt.get("customdata") if isinstance(pt, dict) else getattr(pt, "customdata", None)
            # Link click: customdata[0] = "src||src_layer||tgt||tgt_layer||level"
            if isinstance(cd, (list, tuple)) and len(cd) >= 1 and "||" in str(cd[0]):
                parts = str(cd[0]).split("||")
                if len(parts) == 5:
                    new_link   = (parts[0], int(parts[1]), parts[2], int(parts[3]))
                    link_level = parts[4]
                    if new_link != st.session_state.get("selected_link"):
                        st.session_state["selected_link"]       = new_link
                        st.session_state["selected_link_level"] = link_level
                        st.session_state["selected_node"]       = None
                        st.rerun()
            else:
                # Node click
                lbl = pt.get("label") if isinstance(pt, dict) else getattr(pt, "label", None)
                if not lbl:
                    lbl = (pt.get("pointLabel") if isinstance(pt, dict)
                           else getattr(pt, "pointLabel", None))
                if lbl and lbl != st.session_state.get("selected_node"):
                    st.session_state["selected_node"] = lbl
                    st.session_state["selected_link"] = None
                    st.rerun()

    st.markdown(
        '<div class="info-box">Klik op een <b>flow</b> om behandeldagen aan te passen. '
        'Klik op een <b>node</b> om alternatieven te vergelijken.</div>',
        unsafe_allow_html=True,
    )

    # ── Flow adjustment panel ────────────────────────────────────────────
    sel_link  = st.session_state.get("selected_link")
    sel_level = st.session_state.get("selected_link_level", level)

    if sel_link:
        src_lbl, src_lay, tgt_lbl, tgt_lay = sel_link
        agg_mask = (
            (flows_agg["source_label"] == src_lbl)
            & (flows_agg["source_layer"].astype(int) == int(src_lay))
            & (flows_agg["target_label"] == tgt_lbl)
            & (flows_agg["target_layer"].astype(int) == int(tgt_lay))
        )
        if agg_mask.any():
            cur_days = float(flows_agg.loc[agg_mask, "days_treated"].sum())
            capacity = get_node_capacity(flows_agg, src_lbl, src_lay, sel_level)

            st.markdown(f"#### ↔ Flow aanpassen: **{src_lbl}** → **{tgt_lbl}**")
            new_days = st.number_input(
                "Behandeldagen op deze flow",
                min_value=0.0,
                max_value=float(max(capacity, cur_days)),
                value=float(cur_days),
                step=1.0, format="%.1f",
                key="flow_num_input",
            )
            fc1, fc2 = st.columns(2)
            with fc1:
                if st.button("✅ Toepassen", key="apply_flow_btn", use_container_width=True):
                    cascade_and_save_flow(
                        selected_diag,
                        src_lbl, src_lay, tgt_lbl, tgt_lay,
                        sel_level, new_days,
                    )
                    st.session_state["selected_link"] = None
                    st.rerun()
            with fc2:
                if st.button("✖ Annuleren", key="cancel_flow_btn", use_container_width=True):
                    st.session_state["selected_link"] = None
                    st.rerun()

    # ── Flows table ──────────────────────────────────────────────────────
    with st.expander("Alle flows (huidige weergave)", expanded=False):
        show_cols = [c for c in [
            "source_label", "source_layer", "target_label", "target_layer",
            "pat_count", "sum_aantal", "gem_aantal_per_pat",
            "stuks_dag", "days_treated", "days_per_pat",
            "heritage",
        ] if c in flows_agg.columns]
        disp = flows_agg[show_cols].copy()
        disp.columns = [c.replace("_", " ").title() for c in show_cols]
        st.dataframe(disp, use_container_width=True, hide_index=True)

    # ── Fallback node selector ───────────────────────────────────────────
    with st.expander("Of selecteer een node via dropdown", expanded=False):
        all_node_labels = sorted(
            set(flows_agg["target_label"].tolist() + flows_agg["source_label"].tolist())
            - {"Source"}
        )
        sel_fb = st.selectbox("Node", ["— selecteer —"] + all_node_labels,
                              key="fallback_node_sel")
        if sel_fb != "— selecteer —":
            if st.button("Toon alternatieven", key="fallback_btn"):
                st.session_state["selected_node"] = sel_fb
                st.session_state["selected_link"] = None
                st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# RIGHT: Behandeldagen-per-product chart + Product alternatives
# ════════════════════════════════════════════════════════════════════════════
with col_panel:
    # ── Bar chart: total behandeldagen per product (ground-truth method) ──
    st.markdown('<div class="section-header">Behandeldagen per product</div>',
                unsafe_allow_html=True)

    days_df = compute_per_product_days(pat_filtered, catalogue_df, selected_diag, level)
    if days_df.empty:
        st.info("Geen producten met bekende stuks/dag voor de huidige filter.")
    else:
        bar_df = days_df.head(20).copy()
        bar_df = bar_df.sort_values("days", ascending=True)  # asc → top of bar = highest
        fig_bar = px.bar(
            bar_df,
            x="days",
            y="label",
            orientation="h",
            text=bar_df["days"].round(0),
            labels={"days": "Behandeldagen", "label": ""},
            height=max(260, 28 * len(bar_df) + 80),
            hover_data={"aantal": ":,.1f", "days": ":,.1f"},
        )
        fig_bar.update_traces(
            marker_color="#29B5E8",
            texttemplate="%{x:,.0f}",
            textposition="outside",
            cliponaxis=False,
        )
        fig_bar.update_layout(
            margin=dict(l=10, r=70, t=10, b=10),
            paper_bgcolor="#F6F8FA",
            plot_bgcolor="white",
            showlegend=False,
            xaxis=dict(showgrid=True, gridcolor="#E5E7EB"),
            yaxis=dict(showgrid=False),
            font=dict(size=11, family="Inter, sans-serif"),
        )
        st.plotly_chart(fig_bar, use_container_width=True)
        st.caption(
            f"Som van Aantal × (1 / stuks-per-dag) per {LEVEL_LABELS.get(level, level)}, "
            "berekend uit alle records van de geselecteerde patiënten."
        )

    st.markdown("---")

    # ── Product alternatives panel ───────────────────────────────────────
    sel_node = st.session_state.get("selected_node")

    if not sel_node:
        st.markdown('<div class="section-header">Productenvergelijking</div>',
                    unsafe_allow_html=True)
        st.markdown(
            '<div class="info-box" style="margin-top:8px;">'
            'Klik op een node in het Sankey-diagram om alternatieven te vergelijken.'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(f'<div class="section-header">Vergelijking: {sel_node}</div>',
                    unsafe_allow_html=True)

        if catalogue_df.empty:
            st.warning("product_diagnosis_catalogue.csv is leeg of niet gevonden.")
        else:
            # Filter catalogue to the selected diagnosis using the numeric diag_id_euk
            cat = catalogue_df.copy()
            flow_diag_id = ""
            if "diag_id_euk" in flows_diag.columns:
                ids = flows_diag["diag_id_euk"].dropna().replace("", float("nan")).dropna().unique()
                if len(ids) > 0:
                    flow_diag_id = str(ids[0]).strip()
            if flow_diag_id and "diag_id_euk" in cat.columns:
                cat_diag = cat[cat["diag_id_euk"].astype(str).str.strip() == flow_diag_id]
                if not cat_diag.empty:
                    cat = cat_diag

            nm_lower = sel_node.lower().strip()

            # At prod_id level labels are "Prod_omschr (prod_id)" — extract the raw id
            lookup_key = nm_lower
            if level == "prod_id" and "(" in nm_lower and nm_lower.rstrip().endswith(")"):
                lookup_key = nm_lower.rsplit("(", 1)[1].rstrip(")").strip()

            level_to_cat_col = {
                "prod_id":       "prod_id",
                "prod_nm":       "prod_nm",
                "medicine_name": "medicine_name",
                "group_name":    "group_name",
            }
            match_col = level_to_cat_col.get(level, "prod_nm")
            if match_col in cat.columns:
                cur_match = cat[cat[match_col].astype(str).str.lower().str.strip() == lookup_key]
            else:
                cur_match = pd.DataFrame()

            if cur_match.empty:
                for fb_col in ["prod_nm", "medicine_name"]:
                    if fb_col in cat.columns:
                        cur_match = cat[cat[fb_col].astype(str).str.lower().str.strip() == lookup_key]
                        if not cur_match.empty:
                            break

            cur_row = cur_match.iloc[0] if not cur_match.empty else None
            cur_pid = str(cur_row["prod_id"])      if cur_row is not None else None
            cur_grp = str(cur_row["group_name"])   if cur_row is not None else None
            cur_did = str(cur_row.get("diag_id_euk", "")) if cur_row is not None else ""
            cur_md  = float(cur_row.get("margin_dag",     0) or 0) if cur_row is not None else 0.0
            cur_ud  = float(cur_row.get("prijs_dag",      0) or 0) if cur_row is not None else 0.0
            cur_vd  = float(cur_row.get("vergoeding_dag", 0) or 0) if cur_row is not None else 0.0

            if cur_row is not None:
                mc1, mc2, mc3 = st.columns(3)
                with mc1:
                    st.metric("Marge/dag",    f"€ {cur_md:.4f}")
                with mc2:
                    st.metric("Uitgaven/dag", f"€ {cur_ud:.4f}")
                with mc3:
                    st.metric("Vergoed/dag",  f"€ {cur_vd:.4f}")
            else:
                st.info(f"'{sel_node}' niet gevonden in product_diagnosis_catalogue.csv.")

            st.markdown("---")

            if cur_grp and "group_name" in cat.columns:
                alts = cat[cat["group_name"] == cur_grp].copy()
                if cur_did and "diag_id_euk" in alts.columns:
                    alts_diag = alts[alts["diag_id_euk"].astype(str) == cur_did]
                    if not alts_diag.empty:
                        alts = alts_diag
                if cur_pid:
                    alts = alts[alts["prod_id"].astype(str) != cur_pid]
                alts = alts.sort_values(
                    [c for c in ["medicine_name", "prod_nm", "admin_method"] if c in alts.columns]
                ).reset_index(drop=True)
            else:
                alts = pd.DataFrame()

            if alts.empty:
                st.info("Geen alternatieven gevonden in dezelfde productgroep.")
            else:
                st.markdown(f"**{len(alts)} alternatieven** in groep *{cur_grp}*")

                disp_cols = [c for c in [
                    "group_name", "medicine_name", "prod_nm", "admin_method",
                    "margin_dag", "prijs_dag", "vergoeding_dag",
                ] if c in alts.columns]
                alts_disp = alts[disp_cols].rename(columns={
                    "group_name": "Groep", "medicine_name": "Medicijn",
                    "prod_nm": "Product", "admin_method": "Toediening",
                    "margin_dag": "Marge/dag (€)", "prijs_dag": "Uitgaven/dag (€)",
                    "vergoeding_dag": "Vergoed/dag (€)",
                })
                float_cols = [c for c in ["Marge/dag (€)", "Uitgaven/dag (€)", "Vergoed/dag (€)"]
                              if c in alts_disp.columns]

                def _style_alts(df: pd.DataFrame) -> pd.DataFrame:
                    styles = pd.DataFrame("", index=df.index, columns=df.columns)
                    for col, ref, lb in [
                        ("Marge/dag (€)",    cur_md, False),
                        ("Uitgaven/dag (€)", cur_ud, True),
                        ("Vergoed/dag (€)",  cur_vd, False),
                    ]:
                        if col not in df.columns or ref == 0:
                            continue
                        def _c(v, ref=ref, lb=lb):
                            if lb:
                                return ("background-color:#dcfce7;color:#16a34a;font-weight:600"
                                        if v < ref * 0.95 else
                                        ("background-color:#fee2e2;color:#dc2626;font-weight:600"
                                         if v > ref * 1.05 else "background-color:#fef9c3"))
                            else:
                                return ("background-color:#dcfce7;color:#16a34a;font-weight:600"
                                        if v > ref * 1.05 else
                                        ("background-color:#fee2e2;color:#dc2626;font-weight:600"
                                         if v < ref * 0.95 else "background-color:#fef9c3"))
                        styles[col] = df[col].apply(_c)
                    return styles

                styled = alts_disp.style.apply(_style_alts, axis=None).format(
                    {c: "€ {:.4f}" for c in float_cols}
                )
                sel_rows = st.dataframe(styled, use_container_width=True, hide_index=True,
                                        on_select="rerun", selection_mode="single-row",
                                        key="alts_table")

                rows_idx = (sel_rows.selection.get("rows", [])
                            if isinstance(sel_rows.selection, dict)
                            else getattr(sel_rows.selection, "rows", []))

                if rows_idx:
                    alt_row = alts.iloc[rows_idx[0]]
                    alt_md  = float(alt_row.get("margin_dag",     0) or 0)
                    alt_ud  = float(alt_row.get("prijs_dag",      0) or 0)
                    alt_vd  = float(alt_row.get("vergoeding_dag", 0) or 0)

                    st.markdown("#### 💡 Idee opslaan")
                    st.markdown(
                        f"**Huidig:** {sel_node} &nbsp;→&nbsp; "
                        f"**Alternatief:** {alt_row.get('prod_nm', '–')}"
                    )
                    ac1, ac2, ac3 = st.columns(3)
                    with ac1:
                        st.metric("Δ Marge/dag",    f"€ {alt_md:.4f}",
                                  delta=f"{alt_md - cur_md:+.4f} €")
                    with ac2:
                        st.metric("Δ Uitgaven/dag", f"€ {alt_ud:.4f}",
                                  delta=f"{alt_ud - cur_ud:+.4f} €", delta_color="inverse")
                    with ac3:
                        st.metric("Δ Vergoed/dag",  f"€ {alt_vd:.4f}",
                                  delta=f"{alt_vd - cur_vd:+.4f} €")

                    notities = st.text_area("Notities", value="", key="save_notities")
                    reden    = st.selectbox("Reden beslissing", REDEN_OPTIONS, key="save_reden")

                    if st.button("💾 Opslaan als Idee", type="primary",
                                 use_container_width=True, key="save_idea_btn"):
                        row_dict = {
                            "timestamp":                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "prod_id_huidig":           str(cur_pid) if cur_pid else "",
                            "diag_id_euk":              "",
                            "diag_omschr_euk":          selected_diag,
                            "group":                    cur_grp or "",
                            "medicine_name":            str(cur_row.get("medicine_name", "")) if cur_row is not None else "",
                            "prod_nm":                  str(cur_row.get("prod_nm", sel_node)) if cur_row is not None else sel_node,
                            "admin_method_huidig":      str(cur_row.get("admin_method", "")) if cur_row is not None else "",
                            "admin_method_alternatief": str(alt_row.get("admin_method", "")),
                            "medicine_name_max":        str(alt_row.get("medicine_name", "")),
                            "group_name_max":           str(alt_row.get("group_name", "")),
                            "prod_nm_max":              str(alt_row.get("prod_nm", "")),
                            "prod_id_max":              str(alt_row.get("prod_id", "")),
                            "doen": 1, "verdere_studie": 0, "niet_doen": 0,
                            "niveau":                   level,
                            "notities":                 notities,
                            "Reden_beslissing":         reden,
                            "auto_classified":          False,
                        }
                        try:
                            save_idea(row_dict)
                            st.success(
                                f"✅ Opgeslagen: **{sel_node}** → **{alt_row.get('prod_nm', '–')}**"
                            )
                        except Exception as exc:
                            st.error(f"Fout bij opslaan: {exc}")

        with st.expander("Opgeslagen ideeën (lokaal)", expanded=False):
            fb = load_feedback()
            if fb.empty:
                st.info("Nog geen ideeën opgeslagen.")
            else:
                show_cols = [c for c in ["timestamp", "prod_nm", "prod_nm_max",
                                         "diag_omschr_euk", "Reden_beslissing"] if c in fb.columns]
                st.dataframe(fb[show_cols].tail(20), use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# CSV viewer at the bottom
# ---------------------------------------------------------------------------

st.markdown("---")
with st.expander("📋 CSV-inhoud bekijken", expanded=False):
    tab1, tab2, tab3 = st.tabs(["sankey_original", "sankey_edited", "product_catalogue"])
    with tab1:
        df_o = load_sankey_csv(edited=False)
        st.dataframe(df_o[df_o["diag_omschr_euk"] == selected_diag],
                     use_container_width=True, hide_index=True)
    with tab2:
        df_e = load_sankey_csv(edited=True)
        st.dataframe(df_e[df_e["diag_omschr_euk"] == selected_diag],
                     use_container_width=True, hide_index=True)
    with tab3:
        st.dataframe(load_catalogue_csv(), use_container_width=True, hide_index=True)
