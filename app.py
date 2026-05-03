"""
Sankey-Ideas — Samenvatting (per-diagnose overzicht)
----------------------------------------------------
Eerste van drie pagina's per diagnose:
  - Samenvatting   (deze pagina) — financieel overzicht + Sankey + per-product
  - Ontwerp        (pages/2_Ontwerp.py)
  - Implementatie  (pages/3_Implementatie.py)

KPI-totalen rekenen met ALLE behandelrecords binnen het filtervenster
(niet alleen nieuwe patiënten); de Sankey toont nog steeds NIEUWE patiënten.
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.csv_builder import (
    LEVEL_COLS,
    cascade_and_save_flow,
    get_node_capacity,
    load_catalogue_csv,
    load_patient_cache,
    load_prod_omschr_lookup,
    load_sankey_csv,
)
from backend.financial_utils import compute_kpis_from_patients
from backend.sidebar import (
    ALL_DIAGNOSES,
    ensure_sankey_current,
    filter_records_in_window,
    inject_shared_css,
    render_sidebar,
)
from sankey.sankey_from_csv import aggregate_flows, build_sankey_from_csv

# ---------------------------------------------------------------------------
# Page config + styling
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Samenvatting | Sankey-Ideas",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_shared_css()

LEVEL_LABELS = {
    "prod_id":       "Prod_ID (meest gedetailleerd)",
    "prod_nm":       "Prod_nm",
    "medicine_name": "Medicijn",
    "group_name":    "Therapeutische groep",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_eur(val: float) -> str:
    if abs(val) >= 1_000_000:
        return f"€ {val/1_000_000:,.2f}M"
    return f"€ {val:,.0f}"


def _kpi_card(label: str, value: float) -> str:
    return (
        f'<div class="kpi-card">'
        f'<div class="label">{label}</div>'
        f'<div class="value">{_fmt_eur(value)}</div>'
        f'</div>'
    )


def _norm_pid(val) -> str:
    try:
        return str(int(float(str(val))))
    except (ValueError, TypeError):
        return ""


def _safe_float(val) -> float:
    try:
        x = float(val)
        return 0.0 if x != x else x
    except (TypeError, ValueError):
        return 0.0


def _per_pid_breakdown(
    pat_df: pd.DataFrame,
    cat_df: pd.DataFrame,
    diag_omschr_euk: str,
) -> pd.DataFrame:
    """Per-Prod_ID days + financiën, exact dezelfde rekenwijze als de KPI.

    Aggregaties op een hoger niveau (medicijn / groep) zijn vervolgens
    simpele sommen van deze kolommen — daardoor matcht Σ-bars altijd de KPI.
    """
    empty_cols = [
        "pid_norm", "prod_nm", "med_nm", "group_nm",
        "aantal", "days", "uitgaven", "vergoeding", "marge", "has_rates",
    ]
    if pat_df.empty or cat_df.empty:
        return pd.DataFrame(columns=empty_cols)

    pat = pat_df.copy()
    pat["pid_norm"] = pat["Prod_ID"].apply(_norm_pid)
    grouped = pat.groupby("pid_norm").agg(
        aantal=("Aantal",   "sum"),
        prod_nm=("Prod_nm", "first"),
        med_nm=("Med_nm",   "first"),
        group_nm=("Group_nm", "first"),
    ).reset_index()

    cat = cat_df.copy()
    cat["prod_id"] = cat["prod_id"].astype(str).str.strip()
    if "diag_omschr_euk" in cat.columns:
        cat_diag = cat[cat["diag_omschr_euk"].astype(str) == diag_omschr_euk]
        if not cat_diag.empty:
            cat = cat_diag
    cat_lup = cat.drop_duplicates("prod_id").set_index("prod_id")

    days_col, uitg_col, verg_col, marge_col, hr_col = [], [], [], [], []
    for _, row in grouped.iterrows():
        pid = row["pid_norm"]
        if not pid or pid not in cat_lup.index:
            days_col.append(None)
            uitg_col.append(0.0); verg_col.append(0.0); marge_col.append(0.0)
            hr_col.append(False)
            continue
        cat_row = cat_lup.loc[pid]
        if isinstance(cat_row, pd.DataFrame):
            cat_row = cat_row.iloc[0]
        std = _safe_float(cat_row.get("stuks_dag", 0))
        if std <= 0:
            days_col.append(None)
            uitg_col.append(0.0); verg_col.append(0.0); marge_col.append(0.0)
            hr_col.append(False)
            continue
        d = float(row["aantal"]) / std
        prijs = _safe_float(cat_row.get("prijs_dag",      0))
        verg  = _safe_float(cat_row.get("vergoeding_dag", 0))
        mrg   = _safe_float(cat_row.get("margin_dag",     0))
        days_col.append(d)
        uitg_col.append(d * prijs)
        verg_col.append(d * verg)
        marge_col.append(d * mrg)
        hr_col.append((prijs != 0.0) or (verg != 0.0) or (mrg != 0.0))

    grouped["days"]       = days_col
    grouped["uitgaven"]   = uitg_col
    grouped["vergoeding"] = verg_col
    grouped["marge"]      = marge_col
    grouped["has_rates"]  = hr_col
    return grouped.dropna(subset=["days"]).reset_index(drop=True)


def _label_columns(level: str) -> tuple[str, str]:
    """Return (groupby column for patient data, fallback used as label)."""
    return {
        "prod_id":       ("pid_norm", "pid_norm"),
        "prod_nm":       ("prod_nm",  "prod_nm"),
        "medicine_name": ("med_nm",   "med_nm"),
        "group_name":    ("group_nm", "group_nm"),
    }.get(level, ("prod_nm", "prod_nm"))


def _attach_prod_id_label(out: pd.DataFrame) -> pd.DataFrame:
    """Voor prod_id-niveau: omschrijving + (Prod_ID) als label."""
    out = out.copy()
    out["prod_id"] = out["prod_id"].astype(str).str.strip().replace("", "?")
    omschr_map = load_prod_omschr_lookup()
    prod_nm_clean = (
        out["prod_nm"].astype(str).str.strip().replace({"": "?", "nan": "?"})
    )
    omschr_series = out["prod_id"].map(omschr_map).fillna("")
    omschr_series = omschr_series.where(omschr_series.str.strip() != "", prod_nm_clean)
    out["label"] = omschr_series.astype(str).str.strip() + " (" + out["prod_id"] + ")"
    return out


def compute_per_product_days(
    pat_df: pd.DataFrame,
    cat_df: pd.DataFrame,
    diag_omschr_euk: str,
    level: str,
) -> pd.DataFrame:
    """
    Behandeldagen per productlabel op het gekozen aggregatieniveau.

    Days worden per Prod_ID afgeleid (Aantal / catalogus stuks_dag) en daarna
    geaggregeerd naar het gekozen niveau.
    """
    pid_df = _per_pid_breakdown(pat_df, cat_df, diag_omschr_euk)
    if pid_df.empty:
        return pd.DataFrame(columns=["label", "prod_id", "aantal", "days"])

    if level == "prod_id":
        out = (
            pid_df.groupby("pid_norm")
            .agg(aantal=("aantal", "sum"),
                 days=("days", "sum"),
                 prod_nm=("prod_nm", "first"))
            .reset_index()
            .rename(columns={"pid_norm": "prod_id"})
        )
        out = _attach_prod_id_label(out).drop(columns=["prod_nm"])
    else:
        group_col, _ = _label_columns(level)
        out = (
            pid_df.groupby(group_col)
            .agg(aantal=("aantal", "sum"), days=("days", "sum"),
                 prod_id=("pid_norm", "first"))
            .reset_index()
            .rename(columns={group_col: "label"})
        )
        out["label"] = out["label"].astype(str).str.strip().replace("", "?")
    return out.sort_values("days", ascending=False).reset_index(drop=True)


def per_product_financials(
    pat_df: pd.DataFrame,
    cat_df: pd.DataFrame,
    diag_omschr_euk: str,
    level: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Uitgaven / vergoeding / marge per productlabel.

    Berekent eerst per Prod_ID (zelfde formule als de KPI) en sommeert daarna
    naar het gekozen niveau, zodat Σ-bars exact gelijk is aan de KPI-totalen.

    Returns: (fin_df, missing_rate_labels) — labels met behandeldagen > 0 maar
    zonder bekende tarieven (catalogusgat).
    """
    empty_cols = ["Product", "Aantal", "Behandeldagen", "Uitgaven (€)", "Vergoeding (€)", "Marge (€)"]
    pid_df = _per_pid_breakdown(pat_df, cat_df, diag_omschr_euk)
    if pid_df.empty:
        return pd.DataFrame(columns=empty_cols), []

    if level == "prod_id":
        agg = (
            pid_df.groupby("pid_norm")
            .agg(aantal=("aantal", "sum"),
                 days=("days", "sum"),
                 uitgaven=("uitgaven", "sum"),
                 vergoeding=("vergoeding", "sum"),
                 marge=("marge", "sum"),
                 has_rates=("has_rates", "max"),
                 prod_nm=("prod_nm", "first"))
            .reset_index()
            .rename(columns={"pid_norm": "prod_id"})
        )
        agg = _attach_prod_id_label(agg)
    else:
        group_col, _ = _label_columns(level)
        agg = (
            pid_df.groupby(group_col)
            .agg(aantal=("aantal", "sum"),
                 days=("days", "sum"),
                 uitgaven=("uitgaven", "sum"),
                 vergoeding=("vergoeding", "sum"),
                 marge=("marge", "sum"),
                 has_rates=("has_rates", "max"))
            .reset_index()
            .rename(columns={group_col: "label"})
        )
        agg["label"] = agg["label"].astype(str).str.strip().replace("", "?")

    missing = [
        str(r["label"])
        for _, r in agg.iterrows()
        if float(r["days"]) > 0 and not bool(r["has_rates"])
    ]

    out = pd.DataFrame({
        "Product":        agg["label"].astype(str),
        "Aantal":         agg["aantal"].astype(float),
        "Behandeldagen":  agg["days"].astype(float).round(1),
        "Uitgaven (€)":   agg["uitgaven"].astype(float),
        "Vergoeding (€)": agg["vergoeding"].astype(float),
        "Marge (€)":      agg["marge"].astype(float),
    })
    return out.sort_values("Marge (€)", ascending=False).reset_index(drop=True), missing


# ---------------------------------------------------------------------------
# Load patient cache + catalogue
# ---------------------------------------------------------------------------

cache_df     = load_patient_cache()
catalogue_df = load_catalogue_csv()

# ---------------------------------------------------------------------------
# Sidebar (shared) + Sankey regeneration
# ---------------------------------------------------------------------------

state = render_sidebar(cache_df)
if state is None:
    st.stop()

selected_diag = state["selected_diag"]
regen_start   = state["regen_start"]
regen_end     = state["regen_end"]
regen_vrs     = state["regen_vrs"]
diag_idx      = state["diag_idx"]
is_all_diag   = selected_diag == ALL_DIAGNOSES

# Page-specific level selector lives below the shared sidebar
with st.sidebar:
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

level = st.session_state.get("level", "medicine_name")

if not is_all_diag:
    ensure_sankey_current(
        cache_df, selected_diag, regen_start, regen_end, regen_vrs, grain=level,
    )

# ---------------------------------------------------------------------------
# Load computed flows
# ---------------------------------------------------------------------------

if is_all_diag:
    flows_agg = pd.DataFrame()
else:
    all_flows  = load_sankey_csv(edited=True, grain=level)
    flows_diag = all_flows[all_flows["diag_omschr_euk"] == selected_diag].copy()
    flows_agg  = aggregate_flows(flows_diag, level)

# ── Records-in-window for KPI + per-product totals (item 1, 3) ─────────────
records_window = filter_records_in_window(
    cache_df, selected_diag, regen_start, regen_end, regen_vrs
)

current_totals = compute_kpis_from_patients(
    records_window, catalogue_df, selected_diag, filter_to_diag=False
)

# ---------------------------------------------------------------------------
# KPI bar — totalen voor ALLE behandeling in het venster
# ---------------------------------------------------------------------------

vrs_label = regen_vrs if regen_vrs != "Alle voorschrijvers" else "alle voorschrijvers"
st.markdown(
    f"### 📊 Financieel overzicht — {selected_diag}  "
    f"<small style='font-weight:400;color:#64748B;'>  "
    f"alle behandeling in {regen_start} → {regen_end} · {vrs_label}</small>",
    unsafe_allow_html=True,
)

kc1, kc2, kc3 = st.columns(3)
with kc1:
    st.markdown(_kpi_card("Vergoeding", current_totals["vergoeding"]),
                unsafe_allow_html=True)
with kc2:
    st.markdown(_kpi_card("Uitgaven", current_totals["uitgaven"]),
                unsafe_allow_html=True)
with kc3:
    st.markdown(_kpi_card("Marge", current_totals["marge"]),
                unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sankey — NIEUWE patiënten (alleen voor één diagnose)
# ---------------------------------------------------------------------------

if not is_all_diag:
    level_display = LEVEL_LABELS.get(level, level)
    st.markdown(
        f'<div class="section-header">🔄 Stroom van NIEUWE patiënten in de geselecteerde periode</div>'
        f'<small style="color:#64748B;">{regen_start} → {regen_end} · niveau: {level_display}</small>',
        unsafe_allow_html=True,
    )

    fig = build_sankey_from_csv(
        flows_df=flows_agg,
        level=level,
        selected_node=None,
        selected_link=st.session_state.get("selected_link"),
        diag_omschr_euk=selected_diag,
    )

    event = st.plotly_chart(fig, use_container_width=True, on_select="rerun",
                            key="sankey_chart")

    # Parse click event — link clicks open the flow-adjustment panel
    if event and hasattr(event, "selection"):
        sel    = event.selection
        points = sel.get("points", []) if isinstance(sel, dict) else getattr(sel, "points", [])
        if points:
            pt = points[0]
            cd = pt.get("customdata") if isinstance(pt, dict) else getattr(pt, "customdata", None)
            if isinstance(cd, (list, tuple)) and len(cd) >= 1 and "||" in str(cd[0]):
                parts = str(cd[0]).split("||")
                if len(parts) == 5:
                    new_link   = (parts[0], int(parts[1]), parts[2], int(parts[3]))
                    link_level = parts[4]
                    if new_link != st.session_state.get("selected_link"):
                        st.session_state["selected_link"]       = new_link
                        st.session_state["selected_link_level"] = link_level
                        st.rerun()

    st.markdown(
        '<div class="info-box">Klik op een <b>flow</b> om behandeldagen aan te passen.</div>',
        unsafe_allow_html=True,
    )

    # ── Flow adjustment panel ──────────────────────────────────────────────
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

# ---------------------------------------------------------------------------
# Behandeldagen per product — alle behandeling in het venster
# ---------------------------------------------------------------------------

st.markdown("---")
st.markdown(
    '<div class="section-header">💊 Behandeldagen per product '
    '— alle behandeling in de geselecteerde periode</div>',
    unsafe_allow_html=True,
)

days_df = compute_per_product_days(records_window, catalogue_df, selected_diag, level)

if days_df.empty:
    st.info("Geen producten met bekende stuks/dag voor de huidige filter.")
else:
    fin_df, missing_rate_labels = per_product_financials(
        records_window, catalogue_df, selected_diag, level
    )

    bar_df = days_df.head(20).copy().sort_values("days", ascending=True)
    fin_chart_df = (
        fin_df.set_index("Product")
        .reindex(bar_df["label"].tolist())
        .rename_axis("Product")
        .reset_index()
    )

    product_order = bar_df["label"].tolist()

    fig = make_subplots(
        rows=1,
        cols=2,
        shared_yaxes=True,
        horizontal_spacing=0.015,
        subplot_titles=(
            "Behandeldagen per product",
            "Financiën per product — uitgaven, vergoeding & marge",
        ),
        column_widths=[0.45, 0.55],
    )

    fig.add_trace(
        go.Bar(
            x=bar_df["days"],
            y=bar_df["label"],
            orientation="h",
            marker_color="#29B5E8",
            cliponaxis=False,
            customdata=bar_df[["aantal"]].values,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Behandeldagen: %{x:,.1f}<br>"
                "Aantal: %{customdata[0]:,.1f}<extra></extra>"
            ),
            showlegend=False,
            name="Behandeldagen",
        ),
        row=1,
        col=1,
    )

    metric_colors = {
        "Uitgaven (€)":   "#EF4444",
        "Vergoeding (€)": "#10B981",
        "Marge (€)":      "#29B5E8",
    }
    for metric, color in metric_colors.items():
        fig.add_trace(
            go.Bar(
                x=fin_chart_df[metric],
                y=fin_chart_df["Product"],
                orientation="h",
                marker_color=color,
                name=metric,
                legendgroup=metric,
                hovertemplate=(
                    f"<b>%{{y}}</b><br>{metric}: € %{{x:,.0f}}<extra></extra>"
                ),
                cliponaxis=False,
            ),
            row=1,
            col=2,
        )

    fig.update_yaxes(
        automargin=True,
        showgrid=False,
        categoryorder="array",
        categoryarray=product_order,
        row=1,
        col=1,
    )
    fig.update_yaxes(
        automargin=True,
        showgrid=False,
        showticklabels=False,
        categoryorder="array",
        categoryarray=product_order,
        row=1,
        col=2,
    )
    fig.update_xaxes(
        title_text="Behandeldagen",
        showgrid=True,
        gridcolor="#E5E7EB",
        row=1,
        col=1,
    )
    fig.update_xaxes(
        title_text="Bedrag (€)",
        tickprefix="€ ",
        tickformat=",.0f",
        showgrid=True,
        gridcolor="#E5E7EB",
        row=1,
        col=2,
    )

    zebra_shapes = []
    for idx, _label in enumerate(product_order):
        if idx % 2 == 0:
            continue
        for col in (1, 2):
            zebra_shapes.append(
                dict(
                    type="rect",
                    xref=f"x{'' if col == 1 else col} domain",
                    yref=f"y{'' if col == 1 else col}",
                    x0=0,
                    x1=1,
                    y0=idx - 0.5,
                    y1=idx + 0.5,
                    fillcolor="#F1F5F9",
                    line=dict(width=0),
                    layer="below",
                )
            )

    fig.update_layout(
        barmode="group",
        bargap=0.25,
        bargroupgap=0.05,
        margin=dict(l=20, r=40, t=70, b=60),
        height=max(320, 32 * len(bar_df) + 120),
        paper_bgcolor="#F6F8FA",
        plot_bgcolor="white",
        font=dict(size=11, family="Inter, sans-serif"),
        shapes=zebra_shapes,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1.0,
            bgcolor="rgba(255,255,255,0.85)",
            title_text="",
        ),
    )

    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"Behandeldagen = Som van Aantal × (1 / stuks-per-dag) per "
        f"{LEVEL_LABELS.get(level, level)}. Financiën tonen uitgaven, vergoeding "
        "en marge per product over het filtervenster."
    )
    if missing_rate_labels:
        st.warning(
            "Geen tarieven bekend voor: "
            + ", ".join(missing_rate_labels)
            + " — controleer catalogus."
        )

    st.markdown("**Financieel per product (tabel)**")
    st.dataframe(
        fin_df.style.format({
            "Aantal":         "{:,.1f}",
            "Behandeldagen":  "{:,.1f}",
            "Uitgaven (€)":   "€ {:,.0f}",
            "Vergoeding (€)": "€ {:,.0f}",
            "Marge (€)":      "€ {:,.0f}",
        }),
        use_container_width=True,
        hide_index=True,
        height=max(280, 28 * min(len(fin_df), 20) + 80),
    )
