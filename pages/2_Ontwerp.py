"""
Ontwerp — interactieve editor voor het 'ideale' patiëntenpad per diagnose.

De gebruiker werkt met 100 hypothetische patiënten en verdeelt percentages
per bron-knoop over de doel-medicijnen. Per medicijn moet ze tenminste één
Prod_ID kiezen (met optionele gewichten als ze meerdere selecteert) — dat
geeft de Implementatie-pagina concrete prijs/vergoeding/marge per dag.
"""
from __future__ import annotations

import copy
import os
import sys

import pandas as pd
import streamlit as st
from streamlit_float import float_init

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.budget_impact import compute_budget_impact
from backend.chat_llm import chat_turn, is_configured
from backend.csv_builder import load_catalogue_csv, load_patient_cache, load_sankey_csv
from backend.design_io import (
    design_to_flows_df,
    diff_designs,
    latest_design_for,
    list_designs,
    load_design,
    prefill_from_data_sankey,
    save_design,
    validate_design,
)
from backend.sidebar import (
    ensure_sankey_current,
    filter_records_in_window,
    inject_shared_css,
    render_sidebar,
)
from sankey.sankey_from_csv import aggregate_flows, build_sankey_from_csv

st.set_page_config(
    page_title="Ontwerp | Sankey-Ideas",
    page_icon="✏️",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_shared_css()
float_init()

# ---------------------------------------------------------------------------
# Data
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
# Initialise / load design state
# ---------------------------------------------------------------------------

design_state_key   = "design_state"
loaded_diag_key    = "design_state_diag"
baseline_state_key = "design_baseline"

def _seed_from_data() -> dict:
    """Build a fresh design from the current data Sankey (fallback)."""
    flows_df = load_sankey_csv(edited=True, grain="medicine_name")
    flows_df = flows_df[flows_df["diag_omschr_euk"] == selected_diag]
    agg = aggregate_flows(flows_df, level="medicine_name")
    pat_window = filter_records_in_window(
        cache_df, selected_diag, regen_start, regen_end, regen_vrs
    )
    return prefill_from_data_sankey(agg, pat_window, selected_diag)


def _initial_seed() -> tuple[dict, dict | None]:
    """
    Prefer the latest saved design for this diagnosis. Fall back to seeding
    from the data Sankey when no saved design exists.

    Returns (editable_design, baseline_or_None). Baseline is None when there
    is no saved design — that suppresses the comparison UI until first save.
    """
    saved = latest_design_for(selected_diag)
    if saved is not None:
        return copy.deepcopy(saved), copy.deepcopy(saved)
    return _seed_from_data(), None


if (
    st.session_state.get(design_state_key) is None
    or st.session_state.get(loaded_diag_key) != selected_diag
):
    seed, baseline_seed = _initial_seed()
    st.session_state[design_state_key]    = seed
    st.session_state[baseline_state_key]  = baseline_seed
    st.session_state[loaded_diag_key]     = selected_diag
    # Reset chat history when the user switches diagnosis — context changes.
    st.session_state["chat_messages"] = []

design: dict           = st.session_state[design_state_key]
design_baseline: dict | None = st.session_state.get(baseline_state_key)

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.markdown(
    f"### ✏️ Ontwerp — {selected_diag}  "
    f"<small style='font-weight:400;color:#64748B;'> 100 hypothetische patiënten · medicijn-niveau</small>",
    unsafe_allow_html=True,
)

ctrl_a, ctrl_b, ctrl_c = st.columns([1, 1, 2])
with ctrl_a:
    if st.button("↺ Vul vanuit data", use_container_width=True):
        # Resets *current edits* to the data Sankey; baseline stays — the
        # diff will then visualise how data Sankey differs from the saved plan.
        st.session_state[design_state_key] = _seed_from_data()
        st.rerun()
with ctrl_b:
    if st.button("🗑 Leeg ontwerp", use_container_width=True):
        st.session_state[design_state_key] = {
            "diagnosis":         selected_diag,
            "diag_id_euk":       design.get("diag_id_euk", ""),
            "flows":             [],
            "medicine_products": {},
        }
        st.rerun()
with ctrl_c:
    saved_designs = list_designs(selected_diag)
    if saved_designs:
        opts = ["— laad eerder ontwerp —"] + [f"{d['filename']}" for d in saved_designs]
        sel = st.selectbox("Eerder opgeslagen ontwerpen", opts, key="load_design_sel")
        if sel != opts[0]:
            chosen = next(d for d in saved_designs if d["filename"] == sel)
            if st.button("Laad gekozen ontwerp", key="load_design_btn"):
                loaded = load_design(chosen["path"])
                # Loading an explicit saved design promotes it to the baseline:
                # comparison resets to "no changes" and edits diff against it.
                st.session_state[design_state_key]   = copy.deepcopy(loaded)
                st.session_state[baseline_state_key] = copy.deepcopy(loaded)
                st.rerun()

# ════════════════════════════════════════════════════════════════════════════
# Year-1 budget-impact KPIs — newly made design vs. actual; coloured by
# how that compares to the saved baseline design's impact vs. actual.
# ════════════════════════════════════════════════════════════════════════════

def _fmt_eur(val: float) -> str:
    if abs(val) >= 1_000_000:
        return f"€ {val/1_000_000:,.2f}M"
    return f"€ {val:,.0f}"


def _render_kpi_card(label: str, cur: float, base: float, lower_is_better: bool) -> str:
    delta = cur - base
    pct   = (delta / base * 100.0) if abs(base) > 1e-9 else 0.0
    if abs(delta) < 0.5:
        delta_html = '<div class="delta">— gelijk aan opgeslagen ontwerp</div>'
    else:
        improving = (delta < 0) if lower_is_better else (delta > 0)
        cls   = "pos" if improving else "neg"
        arrow = "↓" if delta < 0 else "↑"
        delta_html = (
            f'<div class="delta {cls}">{arrow} {_fmt_eur(delta)} '
            f'({pct:+.1f}%) vs opgeslagen</div>'
        )
    return (
        '<div class="kpi-card">'
        f'<div class="label">{label}</div>'
        f'<div class="value">{_fmt_eur(cur)}</div>'
        f'{delta_html}'
        '</div>'
    )


if design_baseline is None:
    st.markdown(
        '<div class="info-box">Nog geen opgeslagen ontwerp — sla eerst op om '
        'vergelijking te activeren.</div>',
        unsafe_allow_html=True,
    )
else:
    _data_flows_all = load_sankey_csv(edited=True, grain="medicine_name")
    _data_flows_diag = _data_flows_all[_data_flows_all["diag_omschr_euk"] == selected_diag]
    _data_agg = aggregate_flows(_data_flows_diag, level="medicine_name")
    _pat_window = filter_records_in_window(
        cache_df, selected_diag, regen_start, regen_end, regen_vrs
    )

    _imp_cur  = compute_budget_impact(design,          _data_agg, _pat_window, catalogue_df)
    _imp_base = compute_budget_impact(design_baseline, _data_agg, _pat_window, catalogue_df)

    if _imp_cur["baseline_starter_days"] <= 0:
        st.markdown(
            '<div class="info-box">Geen actuele starter-data om de jaar-1 '
            'impact tegen af te zetten — pas filter aan.</div>',
            unsafe_allow_html=True,
        )
    else:
        k1, k2, k3 = st.columns(3)
        with k1:
            st.markdown(
                _render_kpi_card("Uitgaven / jaar",
                                 _imp_cur["design_uitgaven"],
                                 _imp_base["design_uitgaven"],
                                 lower_is_better=True),
                unsafe_allow_html=True,
            )
        with k2:
            st.markdown(
                _render_kpi_card("Vergoeding / jaar",
                                 _imp_cur["design_vergoeding"],
                                 _imp_base["design_vergoeding"],
                                 lower_is_better=False),
                unsafe_allow_html=True,
            )
        with k3:
            st.markdown(
                _render_kpi_card("Marge / jaar",
                                 _imp_cur["design_marge"],
                                 _imp_base["design_marge"],
                                 lower_is_better=False),
                unsafe_allow_html=True,
            )
        st.caption(
            "Per-medicijn jaar-1 projectie: elk medicijn gebruikt zijn eigen actuele "
            "starter-dagen × prijs/dag · vergoeding/dag · marge/dag (prod_id-mix van het "
            "ontwerp). Medicijnen die het ontwerp niet adresseert behouden hun actuele tarief. "
            "Kleine regel toont verschil t.o.v. opgeslagen ontwerp."
        )

# ---------------------------------------------------------------------------
# Layout: Sankey (links) + Prod_ID-picker per medicijn (rechts);
#         daaronder collapsible flow-editor en de productcatalogus.
# ---------------------------------------------------------------------------

# Resolve the diagnose-specific catalogus subset once — used by both the
# right-side picker and the Productcatalogus block below.
# Resolution order: design.diag_id_euk → catalogue lookup by name →
# patient cache lookup by name (source of truth, tolerant to T4 gaps).
diag_id = str(design.get("diag_id_euk", "") or "").strip()
_sel_norm = str(selected_diag or "").strip().casefold()

if not diag_id and _sel_norm and "diag_omschr_euk" in catalogue_df.columns:
    _m = catalogue_df.loc[
        catalogue_df["diag_omschr_euk"].astype(str).str.strip().str.casefold() == _sel_norm,
        "diag_id_euk",
    ].dropna().astype(str).str.strip()
    if not _m.empty:
        diag_id = _m.iloc[0]

if (
    not diag_id
    and _sel_norm
    and "Diag_omschr_EUK" in cache_df.columns
    and "Diag_ID_EUK" in cache_df.columns
):
    _pat_ids = cache_df.loc[
        cache_df["Diag_omschr_EUK"].astype(str).str.strip().str.casefold() == _sel_norm,
        "Diag_ID_EUK",
    ].dropna()
    for _v in _pat_ids:
        _s = str(_v).strip()
        if not _s or _s.lower() == "nan":
            continue
        try:
            diag_id = str(int(float(_s)))
        except (TypeError, ValueError):
            diag_id = _s
        break

if diag_id and not str(design.get("diag_id_euk", "") or "").strip():
    design["diag_id_euk"] = diag_id
    st.session_state[design_state_key] = design

cat_for_diag = catalogue_df.copy()
cat_for_diag["prod_id"] = cat_for_diag["prod_id"].astype(str).str.strip()
if diag_id and "diag_id_euk" in cat_for_diag.columns:
    _cat_diag = cat_for_diag[cat_for_diag["diag_id_euk"].astype(str).str.strip() == diag_id]
    if not _cat_diag.empty:
        cat_for_diag = _cat_diag

targets = sorted({f["target"] for f in design.get("flows", []) if f.get("target")})

# ════════════════════════════════════════════════════════════════════════════
# Hoofd-rij: Sankey | Prod_ID-picker per medicijn
# ════════════════════════════════════════════════════════════════════════════
sankey_col, picker_col = st.columns([2, 1])

with sankey_col:
    st.markdown('<div class="section-header">Ontwerp Sankey (100 patiënten)</div>',
                unsafe_allow_html=True)

    if not design.get("flows"):
        st.info("Nog geen flows. Gebruik *Vul vanuit data* of voeg rijen toe via *Manually edit flows*.")
    else:
        flows_for_chart = design_to_flows_df(design)
        fig = build_sankey_from_csv(
            flows_df=flows_for_chart,
            level="medicine_name",
            diag_omschr_euk=f"{selected_diag} (ontwerp)",
        )
        st.plotly_chart(fig, use_container_width=True, key="design_sankey")

with picker_col:
    st.markdown('<div class="section-header">Prod_ID-mix per medicijn</div>',
                unsafe_allow_html=True)
    st.caption(
        "Vink per medicijn aan welke Prod_IDs gebruikt worden — "
        "geselecteerde Prod_IDs krijgen automatisch een gelijk aandeel."
    )

    mp = design.setdefault("medicine_products", {})

    if not targets:
        st.info("Nog geen doelmedicijnen — voeg flows toe.")

    for med in targets:
        med_options = cat_for_diag[
            cat_for_diag["medicine_name"].astype(str).str.lower() == med.lower()
        ]
        if med_options.empty:
            st.markdown(f"**{med}** — _geen Prod_IDs in catalogus voor deze diagnose._")
            continue

        med_options = med_options.drop_duplicates("prod_id").reset_index(drop=True)
        all_pids = med_options["prod_id"].astype(str).tolist()

        current = mp.get(med, [])
        current_pids = [p["prod_id"] for p in current if p["prod_id"] in all_pids]
        if not current_pids and all_pids:
            current_pids = [all_pids[0]]

        with st.expander(
            f"💊 {med}  ({len(current_pids)} prod_id geselecteerd)",
            expanded=False,
        ):
            n_chosen = max(len(current_pids), 1)
            equal_share = round(100.0 / n_chosen, 1)

            rows = med_options[[
                "prod_id", "prod_nm", "prod_omschr",
                "prijs_dag", "vergoeding_dag", "margin_dag",
                "freq_dosage", "stuks_per_toediening",
            ]].copy()
            rows.insert(0, "Use", rows["prod_id"].astype(str).isin(current_pids))
            # Read-only display: shows the auto-equal split for ticked rows.
            rows["Share %"] = rows["Use"].map(lambda used: equal_share if used else 0.0)
            rows = rows.rename(columns={
                "prod_id":              "Prod_ID",
                "prod_nm":              "Merknaam",
                "prod_omschr":          "Omschrijving",
                "prijs_dag":            "Prijs/dag",
                "vergoeding_dag":       "Vergoed/dag",
                "margin_dag":           "Marge/dag",
                "freq_dosage":          "Freq dosering",
                "stuks_per_toediening": "Stuks/toediening",
            })

            edited_pick = st.data_editor(
                rows,
                hide_index=True,
                use_container_width=True,
                num_rows="fixed",
                column_config={
                    "Use":              st.column_config.CheckboxColumn("Gebruik", width="small"),
                    "Prod_ID":          st.column_config.TextColumn("Prod_ID", disabled=True),
                    "Merknaam":         st.column_config.TextColumn("Merknaam", disabled=True),
                    "Omschrijving":     st.column_config.TextColumn("Omschrijving", disabled=True),
                    "Prijs/dag":        st.column_config.NumberColumn("Prijs/dag",   disabled=True, format="€ %.2f"),
                    "Vergoed/dag":      st.column_config.NumberColumn("Vergoed/dag", disabled=True, format="€ %.2f"),
                    "Marge/dag":        st.column_config.NumberColumn("Marge/dag",   disabled=True, format="€ %.2f"),
                    "Freq dosering":    st.column_config.NumberColumn("Freq dosering",    disabled=True, format="%.0f"),
                    "Stuks/toediening": st.column_config.NumberColumn("Stuks/toediening", disabled=True, format="%.1f"),
                    "Share %":          st.column_config.NumberColumn(
                        "Share %", disabled=True, format="%.1f",
                        help="Automatisch gelijk verdeeld over de geselecteerde Prod_IDs.",
                    ),
                },
                key=f"prodpick_{med}",
            )

            chosen_df = edited_pick[edited_pick["Use"] == True]  # noqa: E712
            chosen = chosen_df["Prod_ID"].astype(str).tolist()
            if not chosen:
                st.warning("Kies minstens één Prod_ID.")
                continue

            # Equal split across the just-checked Prod_IDs. Largest entry
            # absorbs the rounding remainder so the persisted shares sum to
            # exactly 100 (validate_design tolerates 0.5%, but stay precise).
            new_share = round(100.0 / len(chosen), 1)
            shares = [new_share] * len(chosen)
            shares[0] = round(shares[0] + (100.0 - sum(shares)), 1)
            new_mp_entry = [
                {"prod_id": pid, "share": shares[i]}
                for i, pid in enumerate(chosen)
            ]
            if new_mp_entry != mp.get(med):
                mp[med] = new_mp_entry
                design["medicine_products"] = mp
                st.session_state[design_state_key] = design

    # Drop entries for medicines no longer in flows
    for stale_med in list(mp.keys()):
        if stale_med not in targets:
            mp.pop(stale_med, None)

# ════════════════════════════════════════════════════════════════════════════
# Wijzigingen t.o.v. opgeslagen ontwerp + Origineel-Sankey expander
# (alleen wanneer er een baseline is)
# ════════════════════════════════════════════════════════════════════════════
if design_baseline is not None:
    _diff = diff_designs(design_baseline, design)
    with st.expander(
        "📋 Wijzigingen t.o.v. opgeslagen ontwerp",
        expanded=_diff["any_change"],
    ):
        if not _diff["any_change"]:
            st.success("Geen wijzigingen — huidig ontwerp is identiek aan baseline.")
        else:
            if _diff["flow_changes"]:
                st.markdown("**Aandelen per medicijn**")
                _fc_df = pd.DataFrame([
                    {
                        "Medicijn": r["medicine"],
                        "Source":   r["source"],
                        "Oud %":    round(r["old_share"], 1),
                        "Nieuw %":  round(r["new_share"], 1),
                        "Δ %":      round(r["delta"], 1),
                    }
                    for r in _diff["flow_changes"]
                ])
                st.dataframe(_fc_df, hide_index=True, use_container_width=True)
            if _diff["added_medicines"]:
                st.markdown(
                    f"**Toegevoegd**: {', '.join(_diff['added_medicines'])}"
                )
            if _diff["removed_medicines"]:
                st.markdown(
                    f"**Verwijderd**: {', '.join(_diff['removed_medicines'])}"
                )
            if _diff["product_changes"]:
                st.markdown("**Prod_ID-mix gewijzigd**")
                for _med, _rows in _diff["product_changes"].items():
                    with st.expander(f"💊 {_med}"):
                        _pc_df = pd.DataFrame([
                            {
                                "Prod_ID":  r["prod_id"],
                                "Oud %":    round(r["old_share"], 1),
                                "Nieuw %":  round(r["new_share"], 1),
                                "Δ %":      round(r["delta"], 1),
                            }
                            for r in _rows
                        ])
                        st.dataframe(_pc_df, hide_index=True, use_container_width=True)

    with st.expander(
        "📊 Origineel ontwerp Sankey (opgeslagen baseline)",
        expanded=False,
    ):
        if design_baseline.get("flows"):
            _base_flows = design_to_flows_df(design_baseline)
            _base_fig   = build_sankey_from_csv(
                flows_df=_base_flows,
                level="medicine_name",
                diag_omschr_euk=f"{selected_diag} (origineel)",
            )
            st.plotly_chart(
                _base_fig, use_container_width=True, key="baseline_sankey",
            )
        else:
            st.info("Origineel ontwerp bevat geen flows.")

# ════════════════════════════════════════════════════════════════════════════
# Manually edit flows (collapsible, klein)
# ════════════════════════════════════════════════════════════════════════════
with st.expander("Manually edit flows", expanded=False):
    flows_df = pd.DataFrame(design.get("flows", []))
    if flows_df.empty:
        flows_df = pd.DataFrame(columns=["source", "source_layer",
                                         "target", "target_layer", "share"])
    flows_df = flows_df.rename(columns={
        "source": "Source", "source_layer": "Source_layer",
        "target": "Target", "target_layer": "Target_layer",
        "share":  "Share %",
    })
    edited = st.data_editor(
        flows_df,
        num_rows="dynamic",
        use_container_width=True,
        key="flows_editor",
        column_config={
            "Source":       st.column_config.TextColumn("Source", required=True),
            "Source_layer": st.column_config.NumberColumn("Source_layer", min_value=0, step=1, required=True),
            "Target":       st.column_config.TextColumn("Target", required=True),
            "Target_layer": st.column_config.NumberColumn("Target_layer", min_value=1, step=1, required=True),
            "Share %":      st.column_config.NumberColumn("Share %", min_value=0.0, max_value=100.0, step=1.0, format="%.1f"),
        },
    )
    new_flows = []
    for _, row in edited.iterrows():
        try:
            new_flows.append({
                "source":       str(row["Source"]).strip(),
                "source_layer": int(row["Source_layer"]),
                "target":       str(row["Target"]).strip(),
                "target_layer": int(row["Target_layer"]),
                "share":        float(row["Share %"] or 0),
            })
        except (TypeError, ValueError):
            continue
    new_flows = [f for f in new_flows if f["source"] and f["target"]]
    if new_flows != design.get("flows"):
        design["flows"] = new_flows
        st.session_state[design_state_key] = design
        st.rerun()

    # ── Per-source share validation banner ─────────────────────────────────
    by_src: dict[tuple, float] = {}
    for f in design.get("flows", []):
        key = (f["source"], f["source_layer"])
        by_src[key] = by_src.get(key, 0.0) + float(f["share"])
    bad = [(k, v) for k, v in by_src.items() if abs(v - 100.0) > 0.5]
    if bad:
        msg = " · ".join(f"{src} (laag {lay}): {tot:.1f}%" for (src, lay), tot in bad)
        st.markdown(
            f'<div class="warn-box">⚠️ Aandelen moeten per bron 100% zijn — {msg}</div>',
            unsafe_allow_html=True,
        )
    else:
        if design.get("flows"):
            st.markdown(
                '<div class="info-box">✅ Alle bron-aandelen sommeren naar 100%.</div>',
                unsafe_allow_html=True,
            )

# ════════════════════════════════════════════════════════════════════════════
# Productcatalogus voor deze diagnose (volledige breedte, direct onder Sankey)
# ════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown('<div class="section-header">Productcatalogus (deze diagnose)</div>',
            unsafe_allow_html=True)

_cat_diag_id = diag_id  # resolved at the top of the layout block

if not _cat_diag_id:
    st.info("Geen diag_id_euk bekend voor deze diagnose; catalogus niet weergegeven.")
else:
    cat = catalogue_df.copy()
    cat["prod_id"] = cat["prod_id"].astype(str).str.strip()
    cat = cat[cat["diag_id_euk"].astype(str).str.strip() == _cat_diag_id]
    cat = cat.drop_duplicates(subset=["prod_id", "diag_id_euk"])

    if cat.empty:
        st.info("Geen producten in catalogus voor deze diagnose.")
    else:
        cat = cat.sort_values(
            ["group_name", "medicine_name", "admin_method", "prod_nm", "prod_id"]
        ).reset_index(drop=True)

        def _fmt_eur(x):
            try:
                return f"€ {float(x):,.2f}"
            except (TypeError, ValueError):
                return ""

        def _fmt_freq(x):
            try:
                return f"{float(x):.0f}"
            except (TypeError, ValueError):
                return ""

        def _fmt_stuks(x):
            try:
                return f"{float(x):.1f}"
            except (TypeError, ValueError):
                return ""

        st.caption(
            f"{cat['prod_id'].nunique()} producten · "
            f"{cat['group_name'].nunique()} groepen · "
            f"{cat['medicine_name'].nunique()} medicijnen"
        )

        for grp_name, grp_df in cat.groupby("group_name", sort=True, dropna=False):
            grp_label = grp_name if grp_name and str(grp_name) != "nan" else "(geen groep)"
            with st.expander(
                f"📁 {grp_label}  —  {grp_df['prod_id'].nunique()} prod"
            ):
                for med_name, med_df in grp_df.groupby("medicine_name", sort=True, dropna=False):
                    med_label = med_name if med_name and str(med_name) != "nan" else "(geen medicijn)"
                    with st.expander(
                        f"💊 {med_label}  —  {med_df['prod_id'].nunique()} prod"
                    ):
                        for adm_name, adm_df in med_df.groupby("admin_method", sort=True, dropna=False):
                            adm_label = adm_name if adm_name and str(adm_name) != "nan" else "(geen toedieningswijze)"
                            with st.expander(
                                f"💉 {adm_label}  —  {len(adm_df)} prod_id"
                            ):
                                leaf = adm_df[[
                                    "prod_id", "prod_nm", "prod_omschr",
                                    "prijs_dag", "vergoeding_dag", "margin_dag",
                                    "freq_dosage", "stuks_per_toediening",
                                ]].rename(columns={
                                    "prod_id":              "Prod_ID",
                                    "prod_nm":              "Merknaam",
                                    "prod_omschr":          "Omschrijving",
                                    "prijs_dag":            "Prijs/dag",
                                    "vergoeding_dag":       "Vergoed/dag",
                                    "margin_dag":           "Marge/dag",
                                    "freq_dosage":          "Freq dosering",
                                    "stuks_per_toediening": "Stuks/toediening",
                                })
                                styled = leaf.style.format({
                                    "Prijs/dag":        _fmt_eur,
                                    "Vergoed/dag":      _fmt_eur,
                                    "Marge/dag":        _fmt_eur,
                                    "Freq dosering":    _fmt_freq,
                                    "Stuks/toediening": _fmt_stuks,
                                })
                                st.dataframe(styled, use_container_width=True, hide_index=True)

# ════════════════════════════════════════════════════════════════════════════
# Save (onderaan de pagina)
# ════════════════════════════════════════════════════════════════════════════
st.markdown("---")
save_col_a, save_col_b = st.columns([1, 2])
with save_col_a:
    save_clicked = st.button("💾 Ontwerp opslaan", type="primary",
                              use_container_width=True, key="save_design_btn")
if save_clicked:
    errs = validate_design(design)
    if errs:
        st.error("Niet opgeslagen — eerst de volgende punten oplossen:\n\n- " + "\n- ".join(errs))
    else:
        path = save_design(design)
        # Promote the just-saved design to baseline so deltas reset to 0 and
        # the diff table immediately reflects "no changes vs. saved".
        st.session_state[baseline_state_key] = copy.deepcopy(design)
        st.success(f"✅ Opgeslagen: `{os.path.basename(path)}`")

# ════════════════════════════════════════════════════════════════════════════
# Floating AI chat assistant (bottom-right)
# ════════════════════════════════════════════════════════════════════════════

if "chat_open" not in st.session_state:
    st.session_state["chat_open"] = False
if "chat_messages" not in st.session_state:
    st.session_state["chat_messages"] = []

if not st.session_state["chat_open"]:
    fab = st.container()
    fab.float("bottom: 24px; right: 24px; z-index: 9999; width: 72px;")
    with fab:
        if st.button("💬", key="chat_fab_open", help="AI assistent openen"):
            st.session_state["chat_open"] = True
            st.rerun()
else:
    panel = st.container()
    panel.float(
        "bottom: 24px; right: 24px; z-index: 9999; width: 400px; max-height: 80vh;"
    )
    with panel:
        st.markdown('<div class="chat-panel-wrap">', unsafe_allow_html=True)
        h_left, h_right = st.columns([5, 1])
        with h_left:
            st.markdown(
                '<div class="chat-header">'
                '<span class="title">💬 AI ontwerphulp</span>'
                '</div>',
                unsafe_allow_html=True,
            )
        with h_right:
            if st.button("✕", key="chat_fab_close", help="Sluiten"):
                st.session_state["chat_open"] = False
                st.rerun()

        if not is_configured():
            st.warning(
                "LLM niet geconfigureerd — vul `.streamlit/secrets.toml` "
                "(zie `secrets.toml.example`)."
            )
        else:
            msg_box = st.container(height=380)
            with msg_box:
                if not st.session_state["chat_messages"]:
                    st.caption(
                        "Beschrijf wat je wilt aanpassen aan de flows. "
                        "Voorbeeld: *“Verlaag het aandeel van infliximab "
                        "met 20% en verdeel dat over de andere medicijnen.”*"
                    )
                for msg in st.session_state["chat_messages"]:
                    role = msg.get("role")
                    text = (msg.get("text") or "").strip()
                    if role not in ("user", "assistant") or not text:
                        continue
                    with st.chat_message(role):
                        st.markdown(text)

            user_input = st.chat_input("Typ je vraag…", key="chat_fab_input")
            if user_input:
                api_messages = [
                    {"role": m["role"], "content": m["text"]}
                    for m in st.session_state["chat_messages"]
                    if m.get("role") in ("user", "assistant") and (m.get("text") or "").strip()
                ]
                api_messages.append({"role": "user", "content": user_input})

                with msg_box:
                    with st.chat_message("user"):
                        st.markdown(user_input)
                    with st.spinner("Aan het denken…"):
                        result = chat_turn(
                            api_messages, design, selected_diag, catalogue_df,
                        )

                st.session_state["chat_messages"].append(
                    {"role": "user", "text": user_input}
                )
                if result.get("error"):
                    st.session_state["chat_messages"].append(
                        {"role": "assistant", "text": f"⚠️ {result['error']}"}
                    )
                else:
                    st.session_state["chat_messages"].append(
                        {"role": "assistant", "text": result["assistant_text"]}
                    )
                    if result.get("design_changed"):
                        st.session_state[design_state_key] = result["new_design"]

                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
