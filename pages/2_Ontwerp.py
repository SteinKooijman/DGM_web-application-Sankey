"""
Ontwerp — interactieve editor voor het 'ideale' patiëntenpad per diagnose.

De gebruiker werkt met 100 hypothetische patiënten en verdeelt percentages
per bron-knoop over de doel-medicijnen. Per medicijn moet ze tenminste één
Prod_ID kiezen (met optionele gewichten als ze meerdere selecteert) — dat
geeft de Implementatie-pagina concrete prijs/vergoeding/marge per dag.
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st
from streamlit_float import float_init

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.chat_llm import chat_turn, is_configured
from backend.csv_builder import load_catalogue_csv, load_patient_cache, load_sankey_csv
from backend.design_io import (
    design_to_flows_df,
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

design_state_key = "design_state"
loaded_diag_key  = "design_state_diag"

def _seed_default_design() -> dict:
    flows_df = load_sankey_csv(edited=True, grain="medicine_name")
    flows_df = flows_df[flows_df["diag_omschr_euk"] == selected_diag]
    agg = aggregate_flows(flows_df, level="medicine_name")
    pat_window = filter_records_in_window(
        cache_df, selected_diag, regen_start, regen_end, regen_vrs
    )
    return prefill_from_data_sankey(agg, pat_window, selected_diag)

if (
    st.session_state.get(design_state_key) is None
    or st.session_state.get(loaded_diag_key) != selected_diag
):
    st.session_state[design_state_key] = _seed_default_design()
    st.session_state[loaded_diag_key]  = selected_diag
    # Reset chat history when the user switches diagnosis — context changes.
    st.session_state["chat_messages"] = []

design: dict = st.session_state[design_state_key]

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
        st.session_state[design_state_key] = _seed_default_design()
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
                st.session_state[design_state_key] = load_design(chosen["path"])
                st.rerun()

# ---------------------------------------------------------------------------
# Layout: Sankey (links) + Prod_ID-picker per medicijn (rechts);
#         daaronder collapsible flow-editor en de productcatalogus.
# ---------------------------------------------------------------------------

# Resolve the diagnose-specific catalogus subset once — used by both the
# right-side picker and the Productcatalogus block below.
diag_id = str(design.get("diag_id_euk", "") or "").strip()
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
        "Vink per medicijn aan welke Prod_IDs gebruikt worden en geef "
        "het aandeel op (sommeert per medicijn naar 100%)."
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
        cur_lookup = {p["prod_id"]: float(p.get("share", 0)) for p in current}

        with st.expander(
            f"💊 {med}  ({len(current_pids)} prod_id geselecteerd)",
            expanded=False,
        ):
            rows = med_options[[
                "prod_id", "prod_nm", "prod_omschr",
                "prijs_dag", "vergoeding_dag", "margin_dag",
                "freq_dosage", "stuks_per_toediening",
            ]].copy()
            rows.insert(0, "Use", rows["prod_id"].astype(str).isin(current_pids))
            default_w = round(100.0 / max(len(current_pids), 1), 1)
            rows["Share %"] = rows["prod_id"].astype(str).map(
                lambda pid: cur_lookup.get(pid, default_w if pid in current_pids else 0.0)
            )
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
                        "Share %", min_value=0.0, max_value=100.0, step=1.0, format="%.1f"
                    ),
                },
                key=f"prodpick_{med}",
            )

            chosen_df = edited_pick[edited_pick["Use"] == True]  # noqa: E712
            chosen = chosen_df["Prod_ID"].astype(str).tolist()
            if not chosen:
                st.warning("Kies minstens één Prod_ID.")
                continue

            wtotal = float(chosen_df["Share %"].fillna(0).sum() or 0)
            if abs(wtotal - 100.0) > 0.5:
                st.markdown(
                    f'<div class="warn-box">Som = {wtotal:.1f}% — moet 100% zijn.</div>',
                    unsafe_allow_html=True,
                )

            new_mp_entry = [
                {"prod_id": str(r["Prod_ID"]), "share": float(r["Share %"] or 0)}
                for _, r in chosen_df.iterrows()
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

# Resolve diag_id: prefer the design's diag_id_euk, fall back to lookup by name.
_cat_diag_id = str(design.get("diag_id_euk", "") or "").strip()
if not _cat_diag_id and selected_diag and "diag_omschr_euk" in catalogue_df.columns:
    _match = catalogue_df.loc[
        catalogue_df["diag_omschr_euk"].astype(str).str.strip() == str(selected_diag).strip(),
        "diag_id_euk",
    ].dropna().astype(str).str.strip()
    if not _match.empty:
        _cat_diag_id = _match.iloc[0]

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
