"""
financial_utils.py
------------------
Per-dag financial calculations and flow-based running totals.
Pure pandas/numpy — no Streamlit imports.
"""

from __future__ import annotations

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Per-dag metrics
# ---------------------------------------------------------------------------

def stuks_dag(stuks_per_toediening: float, freq_days: float) -> float:
    """Units dispensed per day."""
    if freq_days and freq_days > 0:
        return stuks_per_toediening / freq_days
    return 0.0


def uitgaven_dag(prijs_st: float, std: float) -> float:
    return prijs_st * std


def vergoeding_dag(verg_st: float, std: float) -> float:
    return verg_st * std


def margin_dag(verg_st: float, prijs_st: float, std: float) -> float:
    return (verg_st - prijs_st) * std


def aantal_alt(original_qty: float, std_cur: float, std_alt: float) -> float:
    """Dosage-corrected quantity when switching to an alternative product.
    alternative_qty = original_qty * (stuks/dag_alt / stuks/dag_cur)
    """
    if std_cur and std_cur > 0:
        return original_qty * std_alt / std_cur
    return 0.0


# ---------------------------------------------------------------------------
# Product catalogue helpers
# ---------------------------------------------------------------------------

def build_t1t2(t1: pd.DataFrame, t2: pd.DataFrame) -> pd.DataFrame:
    """
    Join T1 (diagnosis + dosage context) and T2 (price/vergoeding) via case-insensitive
    'Medicine Name'. T1 does not carry Prod_ID; T2 does not carry Diag_ID_EUK — the join
    bridges them.

    Returns a DataFrame with columns (subset of what's available):
      Prod_ID, Prod_nm, Medicine Name, Group Name, Diag_ID_EUK,
      Administration Method, Dosage Frequency, Prijs/st, Vergoeding/st
    Adds computed: Stuks/toediening (Dosage Height × multiplier / mg), stuks_dag,
                   uitgaven_dag, vergoeding_dag, margin_dag.
    """
    t1_cols = [c for c in [
        "Medicine Name", "Group Name", "Diag_ID_EUK",
        "Administration Method", "Dosage Frequency",
        "Dosage Height", "Dosage Type",
        "Stuks/toediening", "Freq_dosage (days)",  # present in older T1 versions
        "Info_Dosage?", "Date edited",             # used for T1 dedup
    ] if c in t1.columns]
    t2_cols = [c for c in [
        "Prod_ID", "Prod_nm", "Prod_omschr", "Medicine Name",
        "Administration Method", "Prijs/st", "Vergoeding/st", "mg",
    ] if c in t2.columns]

    t1_clean = t1[t1_cols].copy()

    # Drop T1 rows with zero/missing dosing — they create NaN per-dag values and
    # can mask the real dosing row during downstream dedup.
    if "Dosage Height" in t1_clean.columns and "Dosage Frequency" in t1_clean.columns:
        _dose_h = pd.to_numeric(t1_clean["Dosage Height"], errors="coerce")
        _dose_f = pd.to_numeric(t1_clean["Dosage Frequency"], errors="coerce")
        t1_clean = t1_clean[(_dose_h.fillna(0) > 0) & (_dose_f.fillna(0) > 0)].copy()

    # Dedup T1: prefer rows where Info_Dosage? is True, then most recent Date edited.
    # Unique key is (Medicine Name, Administration Method, Diag_ID_EUK) — one
    # canonical dosing row per medicine + admin route + diagnosis.
    _info_true = (
        t1_clean["Info_Dosage?"].astype(str).str.strip().str.lower()
        .isin(["true", "1", "yes"])
        if "Info_Dosage?" in t1_clean.columns
        else pd.Series(True, index=t1_clean.index)
    )
    _date_edited = (
        pd.to_datetime(t1_clean["Date edited"], errors="coerce")
        if "Date edited" in t1_clean.columns
        else pd.Series(pd.NaT, index=t1_clean.index)
    )
    t1_clean = t1_clean.assign(_info_true=_info_true, _date_edited=_date_edited)
    dedup_subset = [
        c for c in ["Medicine Name", "Administration Method", "Diag_ID_EUK"]
        if c in t1_clean.columns
    ]
    if dedup_subset:
        t1_clean = (
            t1_clean.sort_values(["_info_true", "_date_edited"], ascending=[False, False])
            .drop_duplicates(subset=dedup_subset)
        )
    t1_clean = t1_clean.drop(columns=["_info_true", "_date_edited"])

    t2_clean = t2[t2_cols].drop_duplicates(subset=["Prod_ID"]).copy()

    # Composite join key: Medicine Name + Administration Method (case-insensitive).
    # Same product can be dosed differently per admin route, so both must match.
    def _key(df: pd.DataFrame) -> pd.Series:
        med = df["Medicine Name"].astype(str).str.lower().str.strip()
        adm = (
            df["Administration Method"].astype(str).str.lower().str.strip()
            if "Administration Method" in df.columns
            else pd.Series("", index=df.index)
        )
        return med + "|" + adm

    t1_clean["_key"] = _key(t1_clean)
    t2_clean["_key"] = _key(t2_clean)

    merged = pd.merge(t1_clean, t2_clean, on="_key", how="inner", suffixes=("_t1", "_t2"))
    merged = merged.drop(columns=["_key"])

    # Resolve duplicate columns: prefer T2 for shared cols (Administration Method, Medicine Name)
    for col in ["Administration Method", "Medicine Name"]:
        if f"{col}_t1" in merged.columns and f"{col}_t2" in merged.columns:
            merged[col] = merged[f"{col}_t2"]
            merged = merged.drop(columns=[f"{col}_t1", f"{col}_t2"])

    # Compute Stuks/toediening if missing: Dosage Height × multiplier / mg.
    # Multiplier converts dose-per-kg or per-m² to absolute mg per administration.
    DOSAGE_MULTIPLIERS = {"kg": 75, "m2": 1.8}
    DOSAGE_DEFAULT = 1
    if (
        "Stuks/toediening" not in merged.columns
        and "Dosage Height" in merged.columns
        and "mg" in merged.columns
    ):
        dose_h = pd.to_numeric(merged["Dosage Height"], errors="coerce")
        mg = pd.to_numeric(merged["mg"], errors="coerce")
        if "Dosage Type" in merged.columns:
            multiplier = (
                merged["Dosage Type"].astype(str).str.strip()
                .map(DOSAGE_MULTIPLIERS).fillna(DOSAGE_DEFAULT)
            )
        else:
            multiplier = DOSAGE_DEFAULT
        merged["Stuks/toediening"] = (dose_h * multiplier) / mg

    # Compute stuks/dag: use explicit cols if present, else default to 1.0 (per-unit comparison)
    freq_col = next((c for c in ["Freq_dosage (days)", "Dosage Frequency"] if c in merged.columns), None)
    stuks_col = "Stuks/toediening" if "Stuks/toediening" in merged.columns else None

    if freq_col and stuks_col:
        merged["stuks_dag"] = merged.apply(
            lambda r: stuks_dag(r[stuks_col], r[freq_col])
            if pd.notna(r[stuks_col]) and pd.notna(r[freq_col]) and r[freq_col] > 0
            else 1.0,
            axis=1,
        )
    else:
        merged["stuks_dag"] = 1.0  # per-unit comparison

    prijs_col = "Prijs/st"
    verg_col = "Vergoeding/st"
    merged["uitgaven_dag"] = merged["stuks_dag"] * merged.get(prijs_col, pd.Series(0, index=merged.index)).fillna(0)
    merged["vergoeding_dag"] = merged["stuks_dag"] * merged.get(verg_col, pd.Series(0, index=merged.index)).fillna(0)
    if prijs_col in merged.columns and verg_col in merged.columns:
        merged["margin_dag"] = merged["stuks_dag"] * (merged[verg_col].fillna(0) - merged[prijs_col].fillna(0))
    else:
        merged["margin_dag"] = 0.0

    return merged


def _diag_matches(cell_val, target_id: str) -> bool:
    """Check if target_id appears in a potentially compound Diag_ID_EUK cell like '1010;1011'."""
    cell = str(cell_val).strip()
    parts = [p.strip() for p in cell.replace(",", ";").split(";")]
    return target_id in parts


def get_alternatives(
    t1t2: pd.DataFrame,
    diag_id_euk,
    current_prod_id=None,
    current_prod_nm: str | None = None,
) -> pd.DataFrame:
    """
    Return all products for a given Diag_ID_EUK from the joined T1+T2 catalogue.
    Handles compound Diag_ID_EUK values like '1010;1011'.
    Excludes the current product by Prod_ID (if available) or Prod_nm.
    Sorted by Group Name → Medicine Name → Prod_nm → Administration Method.
    """
    target = str(int(float(str(diag_id_euk)))) if str(diag_id_euk).replace(".", "").isdigit() else str(diag_id_euk)
    mask = t1t2["Diag_ID_EUK"].apply(lambda v: _diag_matches(v, target))
    alts = t1t2[mask].copy()

    if current_prod_id is not None and "Prod_ID" in alts.columns:
        alts = alts[alts["Prod_ID"].astype(str) != str(current_prod_id)]
    elif current_prod_nm and "Prod_nm" in alts.columns:
        alts = alts[alts["Prod_nm"].astype(str).str.lower() != current_prod_nm.lower()]

    sort_cols = [c for c in ["Group Name", "Medicine Name", "Prod_nm", "Administration Method"] if c in alts.columns]
    if sort_cols:
        alts = alts.sort_values(sort_cols)
    return alts.drop_duplicates(subset=["Prod_ID"] if "Prod_ID" in alts.columns else ["Prod_nm"]).reset_index(drop=True)


def lookup_product(t1t2: pd.DataFrame, prod_nm_base: str, analyse_col: str) -> pd.Series | None:
    """
    Find a product row in t1t2 matching the base node label (after stripping the ' -Ne' suffix).
    analyse_col is the column used for node labelling (e.g. 'Med_nm', 'Prod_nm', 'Group_nm').
    Returns the first matching row or None.
    """
    col_map = {
        "Med_nm": "Medicine Name",
        "Prod_nm": "Prod_nm",
        "Group_nm": "Group Name",
        "Prod_omschr": "Prod_nm",
    }
    t2_col = col_map.get(analyse_col, analyse_col)
    if t2_col not in t1t2.columns:
        # Try case-insensitive partial match against any text column
        for col in ["Prod_nm", "Medicine Name"]:
            if col in t1t2.columns:
                match = t1t2[t1t2[col].astype(str).str.lower() == prod_nm_base.lower()]
                if not match.empty:
                    return match.iloc[0]
        return None
    match = t1t2[t1t2[t2_col].astype(str).str.lower() == prod_nm_base.lower()]
    if match.empty:
        # Partial match fallback
        match = t1t2[t1t2[t2_col].astype(str).str.lower().str.contains(prod_nm_base.lower(), na=False)]
    return match.iloc[0] if not match.empty else None


# ---------------------------------------------------------------------------
# Flow adjustment + cascading
# ---------------------------------------------------------------------------

def source_node_capacity(flows_df: pd.DataFrame, src_nm: str) -> float:
    """
    Maximum flow a source node can distribute:
    = total incoming flow to that node (or total outflow if it is 'Source').
    """
    incoming = flows_df.loc[flows_df["target_nm"] == src_nm, "value"].sum()
    if incoming > 0:
        return float(incoming)
    # 'Source' node has no incoming; use its total outgoing as capacity
    return float(flows_df.loc[flows_df["source_nm"] == src_nm, "value"].sum())


def cascade_flow_change(
    flows_df: pd.DataFrame,
    src_nm: str,
    tgt_nm: str,
    new_val: float,
) -> pd.DataFrame:
    """
    Update the value of link src→tgt and proportionally scale tgt's outgoing links.
    One level of cascading — keeps UX predictable without infinite recursion.
    """
    flows_df = flows_df.copy()
    mask = (flows_df["source_nm"] == src_nm) & (flows_df["target_nm"] == tgt_nm)
    if not mask.any():
        return flows_df

    old_val = float(flows_df.loc[mask, "value"].iloc[0])
    new_val = max(0.0, new_val)
    flows_df.loc[mask, "value"] = new_val
    delta = new_val - old_val

    if delta == 0:
        return flows_df

    # Scale tgt's outgoing flows proportionally
    tgt_out_mask = flows_df["source_nm"] == tgt_nm
    if tgt_out_mask.any():
        old_total = float(flows_df.loc[tgt_out_mask, "value"].sum())
        new_total = max(0.0, old_total + delta)
        scale = new_total / old_total if old_total > 0 else 1.0
        flows_df.loc[tgt_out_mask, "value"] = (flows_df.loc[tgt_out_mask, "value"] * scale).clip(lower=0)

    return flows_df


# ---------------------------------------------------------------------------
# KPI computation from CSV-based flows + catalogue (no patient data needed)
# ---------------------------------------------------------------------------

def compute_kpis_from_patients(
    pat_df: pd.DataFrame,
    catalogue_df: pd.DataFrame,
    diag_omschr_euk: str,
    filter_to_diag: bool = False,
) -> dict:
    """
    Compute aggregate vergoeding / uitgaven / marge directly from patient data.

    This is the correct approach: for each Prod_ID, sum all Aantal across the
    (already-filtered) patient DataFrame, divide by stuks_dag from the catalogue
    to get behandeldagen, then multiply by the daily financial rates.

    Using the Sankey flows for KPIs causes double-counting: each product's
    Aantal appears both in Source→product and product→next_product flows.

    pat_df         : patient records for the qualifying patient set (caller
                     is responsible for patient selection).
    filter_to_diag : when True, restrict pat_df rows to diag_omschr_euk before
                     summing Aantal.  When False (default), all records for the
                     qualifying patients are used — this mirrors the Sankey's
                     own treatment of patient journeys, which groups by
                     (Pat_id, Prod_ID) across all diagnoses and then looks up
                     rates from the diagnosis-specific catalogue.
    Returns {"vergoeding": float, "uitgaven": float, "marge": float}
    """
    totals = {"vergoeding": 0.0, "uitgaven": 0.0, "marge": 0.0}
    if pat_df.empty or catalogue_df.empty:
        return totals

    def _safe(v) -> float:
        try:
            x = float(v)
            return 0.0 if x != x else x
        except Exception:
            return 0.0

    def _norm(val) -> str:
        try:
            return str(int(float(str(val))))
        except (ValueError, TypeError):
            return ""

    # Optionally filter patient data to this diagnosis
    if filter_to_diag and "Diag_omschr_EUK" in pat_df.columns:
        pat_df = pat_df[pat_df["Diag_omschr_EUK"].astype(str) == diag_omschr_euk]
    if pat_df.empty:
        return totals

    # Sum Aantal per Prod_ID
    agg = pat_df.groupby("Prod_ID")["Aantal"].sum().reset_index()

    # Filter catalogue to this diagnosis; fall back to full catalogue if unmatched
    cat = catalogue_df.copy()
    cat["prod_id"] = cat["prod_id"].astype(str).str.strip()
    if "diag_omschr_euk" in cat.columns:
        cat_diag = cat[cat["diag_omschr_euk"].astype(str) == diag_omschr_euk]
        if not cat_diag.empty:
            cat = cat_diag
    cat_by_id = cat.set_index("prod_id")

    for _, row in agg.iterrows():
        pid    = _norm(row["Prod_ID"])
        aantal = float(row["Aantal"])
        if not pid or pid not in cat_by_id.index:
            continue
        entry   = cat_by_id.loc[pid]
        cat_row = entry.iloc[0] if isinstance(entry, pd.DataFrame) else entry
        std = _safe(cat_row.get("stuks_dag", 0))
        if std <= 0:
            continue
        days = aantal / std
        totals["vergoeding"] += days * _safe(cat_row.get("vergoeding_dag", 0))
        totals["uitgaven"]   += days * _safe(cat_row.get("prijs_dag",      0))
        totals["marge"]      += days * _safe(cat_row.get("margin_dag",     0))

    return totals


def compute_kpis_csv(
    flows_df: pd.DataFrame,
    catalogue_df: pd.DataFrame,
) -> dict:
    """
    Compute aggregate vergoeding / uitgaven / marge from the CSV-based flows
    and the product-diagnosis catalogue.

    If the catalogue has a 'diag_omschr_euk' column it is filtered to the
    diagnosis present in flows_df before joining, so diagnosis-specific dosage
    rates (stuks_dag, prijs_dag, …) are used automatically.

    Returns {"vergoeding": float, "uitgaven": float, "marge": float}
    """
    totals = {"vergoeding": 0.0, "uitgaven": 0.0, "marge": 0.0}
    if flows_df.empty or catalogue_df.empty:
        return totals

    def _norm(val) -> str:
        try:
            return str(int(float(str(val))))
        except (ValueError, TypeError):
            return ""

    # Attribute days_treated to the product whose stuks_dag was used to compute them:
    #
    #   Source → product X  (source_layer == 0):
    #     days_treated = target_Aantal / target_stuks_dag  → X's own treatment days
    #     → attribute to target_prod_id
    #
    #   Product A → Product B  (source_layer > 0):
    #     days_treated = source_Aantal / source_stuks_dag  → A's treatment days
    #     → attribute to source_prod_id
    #
    is_first = flows_df["source_prod_id"].astype(str).str.strip().isin(["", "nan", "NaN"])

    first = (
        flows_df[is_first]
        .groupby("target_prod_id", dropna=True)["days_treated"]
        .sum()
        .reset_index()
        .rename(columns={"target_prod_id": "prod_id"})
    )
    subsequent = (
        flows_df[~is_first]
        .groupby("source_prod_id", dropna=True)["days_treated"]
        .sum()
        .reset_index()
        .rename(columns={"source_prod_id": "prod_id"})
    )
    agg = (
        pd.concat([first, subsequent], ignore_index=True)
        .groupby("prod_id", dropna=True)["days_treated"]
        .sum()
        .reset_index()
    )

    cat = catalogue_df.copy()
    cat["prod_id"] = cat["prod_id"].astype(str).str.strip()

    # Filter to diagnosis when possible — uses diagnosis-specific dosage rates
    if "diag_omschr_euk" in cat.columns and "diag_omschr_euk" in flows_df.columns:
        diag_vals = flows_df["diag_omschr_euk"].dropna().unique()
        if len(diag_vals) > 0:
            diag_omschr = str(diag_vals[0])
            cat_diag = cat[cat["diag_omschr_euk"].astype(str) == diag_omschr]
            if not cat_diag.empty:
                cat = cat_diag

    cat_by_id = cat.set_index("prod_id")

    def _safe(v) -> float:
        return 0.0 if pd.isna(v) else float(v)

    for _, row in agg.iterrows():
        prod_id = _norm(row["prod_id"])
        days    = float(row["days_treated"])

        if not prod_id or prod_id not in cat_by_id.index:
            continue

        entry   = cat_by_id.loc[prod_id]
        cat_row = entry.iloc[0] if isinstance(entry, pd.DataFrame) else entry

        totals["vergoeding"] += days * _safe(cat_row.get("vergoeding_dag", 0))
        totals["uitgaven"]   += days * _safe(cat_row.get("prijs_dag",      0))
        totals["marge"]      += days * _safe(cat_row.get("margin_dag",     0))

    return totals


# ---------------------------------------------------------------------------
# Running totals from flows + patient journey data
# ---------------------------------------------------------------------------

def compute_running_totals(
    flows_df: pd.DataFrame,
    pat_df: pd.DataFrame,
    t1t2: pd.DataFrame,
    analyse_col: str,
    diag_omschr_euk: str,
) -> dict:
    """
    Compute aggregate vergoeding, uitgaven, and marge for the current Sankey state.

    Strategy:
    - For each non-Source node, patient_count = sum of incoming link values (adjusted).
    - Base Aantal per patient = total Aantal from pat_df for that product / original patient count.
    - scaled_qty = adjusted_patient_count * base_qty_per_patient
    - totals = sum over all nodes of scaled_qty * price metrics

    Returns: {"vergoeding": float, "uitgaven": float, "marge": float}
    """
    totals = {"vergoeding": 0.0, "uitgaven": 0.0, "marge": 0.0}

    # Build node → base product name lookup (strip ' -Ne' suffix)
    all_nodes = set(flows_df["target_nm"].unique()) - {"Source"}

    # Aggregate pat_df by product (analyse_col), filtered to this diagnosis
    diag_mask = pat_df["Diag_omschr_EUK"] == diag_omschr_euk if "Diag_omschr_EUK" in pat_df.columns else pd.Series([True] * len(pat_df))
    pat_diag = pat_df[diag_mask] if isinstance(diag_mask, pd.Series) else pat_df

    for node_nm in all_nodes:
        # Patient count from adjusted flows (sum of all incoming links)
        adj_pat_count = float(flows_df.loc[flows_df["target_nm"] == node_nm, "value"].sum())
        if adj_pat_count <= 0:
            continue

        # Extract base product name (remove ' -1e', ' -2e', etc.)
        base_nm = node_nm.rsplit(" -", 1)[0].strip() if " -" in node_nm else node_nm

        # Find product in T1+T2
        prod_row = lookup_product(t1t2, base_nm, analyse_col)
        if prod_row is None:
            continue

        std = float(prod_row.get("stuks_dag", 0) or 0)
        prijs = float(prod_row.get("Prijs/st", 0) or 0)
        verg = float(prod_row.get("Vergoeding/st", 0) or 0)

        # Total Aantal from original data for this product
        col_map = {"Med_nm": "Med_nm", "Prod_nm": "Prod_nm", "Group_nm": "Group_nm"}
        pat_col = col_map.get(analyse_col, analyse_col)
        if pat_col in pat_diag.columns and "Aantal" in pat_diag.columns:
            mask_prod = pat_diag[pat_col].astype(str).str.lower() == base_nm.lower()
            base_aantal = float(pat_diag.loc[mask_prod, "Aantal"].sum())
            orig_pat_count = float(pat_diag.loc[mask_prod, "Pat_id"].nunique()) if "Pat_id" in pat_diag.columns else adj_pat_count
            qty_per_patient = base_aantal / orig_pat_count if orig_pat_count > 0 else 0.0
            scaled_qty = adj_pat_count * qty_per_patient
        else:
            # Fallback: 365 days × stuks/dag × adjusted patient count
            scaled_qty = adj_pat_count * std * 365

        totals["vergoeding"] += scaled_qty * verg
        totals["uitgaven"] += scaled_qty * prijs
        totals["marge"] += scaled_qty * (verg - prijs)

    return totals
