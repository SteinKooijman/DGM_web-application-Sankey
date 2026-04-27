"""
csv_builder.py
--------------
Generate and load the three foundation CSV files.

  data/sankey_original.csv  – one row per (source_prod_id, target_prod_id, layer pair).
                              Always at prod_id grain. Never mutated after generation.
  data/sankey_edited.csv    – identical structure, updated by user edits in the UI.
  data/product_catalogue.csv – one row per prod_id with per-dag financials.

Flow quantity: days_treated = sum(Aantal) * stuks_dag
  stuks_dag comes from product_catalogue.csv (defaults to 1.0 when missing).

Display aggregation (prod_id → prod_nm → medicine_name → group_name) is done at
render time in sankey_from_csv.py, not stored in the CSV.
"""

from __future__ import annotations

import os

import pandas as pd

from sankey.sankey_functions import _apply_diag_voorschrijver_time_filter

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_PROJECT_DIR, "data")

SANKEY_ORIGINAL_CSV  = os.path.join(DATA_DIR, "sankey_original.csv")
SANKEY_EDITED_CSV    = os.path.join(DATA_DIR, "sankey_edited.csv")
CATALOGUE_CSV        = os.path.join(DATA_DIR, "product_diagnosis_catalogue.csv")
PATIENT_CACHE_CSV    = os.path.join(DATA_DIR, "patient_data_cache.csv")

# Grain: one row per (source_prod_id, target_prod_id, source_layer, target_layer, heritage)
# source_prod_id = "" for the virtual Source node (layer 0)
SANKEY_COLS = [
    "source_prod_id",
    "source_prod_nm",
    "source_medicine_name",
    "source_group_name",
    "target_prod_id",
    "target_prod_nm",
    "target_medicine_name",
    "target_group_name",
    "source_layer",
    "target_layer",
    "sum_aantal",
    "stuks_dag",
    "gem_aantal_per_pat",   # sum_aantal / pat_count
    "diag_id_euk",          # matched from catalogue (diagnosis-specific lookup)
    "days_treated",         # sum_aantal / stuks_dag  (÷ not ×)
    "days_per_pat",         # days_treated / pat_count
    "pat_count",
    "heritage",
    "diag_omschr_euk",
]

CATALOGUE_COLS = [
    "prod_id", "diag_id_euk", "diag_omschr_euk",
    "group_name", "medicine_name", "prod_nm", "prod_omschr", "admin_method",
    "prijs_st", "vergoeding_st", "margin_st",
    "freq_dosage", "stuks_per_toediening", "stuks_dag",
    "prijs_dag", "vergoeding_dag", "margin_dag",
]

# Column used to drive aggregation levels at display time
LEVEL_COLS = {
    "prod_id":       ("source_prod_id",      "target_prod_id"),
    "prod_nm":       ("source_prod_nm",       "target_prod_nm"),
    "medicine_name": ("source_medicine_name", "target_medicine_name"),
    "group_name":    ("source_group_name",    "target_group_name"),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm_prod_id(val) -> str:
    try:
        return str(int(float(str(val))))
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# Sankey CSV generator
# ---------------------------------------------------------------------------

def generate_sankey_csv(
    pat_df: pd.DataFrame,
    diag_omschr_euk: str,
    start_date,
    end_date,
    voorschrijver_nm: str = "Alle voorschrijvers",
    append: bool = True,
) -> pd.DataFrame:
    """
    Generate (or append to) sankey_original.csv from patient journey data.

    Mirrors the original sankey_functions.py with analyse_col='Prod_ID':
      - Steps grouped by (Pat_id, Prod_ID) — one step per unique product per patient
      - Flow value = pat_count (unique patient headcount on each transition)
      - sum_aantal and days_treated also stored for future financial use

    Heritage = first-step medicine name (Med_nm of the first Prod_ID per patient).
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    # ── Filter to diag / date range / voorschrijver ──────────────────────────
    df = _apply_diag_voorschrijver_time_filter(
        pat_df, "Prod_ID",
        pd.Timestamp(start_date), pd.Timestamp(end_date),
        diag_omschr_euk, voorschrijver_nm,
    )
    if df.empty:
        return pd.DataFrame(columns=SANKEY_COLS)

    # ── Prod_id → hierarchy label lookup ────────────────────────────────────
    prod_info: dict[str, dict] = {}
    for _, row in df.drop_duplicates("Prod_ID").iterrows():
        pid = _norm_prod_id(row["Prod_ID"])
        if pid:
            prod_info[pid] = {
                "prod_nm":       str(row.get("Prod_nm",  "") or ""),
                "medicine_name": str(row.get("Med_nm",   "") or ""),
                "group_name":    str(row.get("Group_nm", "") or ""),
            }

    def _info(pid: str) -> dict:
        return prod_info.get(pid, {"prod_nm": "", "medicine_name": "", "group_name": ""})

    # ── Resolve diag_id_euk from the original (unfiltered) patient data ──────
    # _apply_diag_voorschrijver_time_filter returns ALL records for patients
    # who have this diagnosis (including their other diagnoses), so we must
    # look up Diag_ID_EUK from pat_df filtered to this diagnosis name only.
    diag_id_euk_val = ""
    if "Diag_ID_EUK" in pat_df.columns and "Diag_omschr_EUK" in pat_df.columns:
        diag_rows = pat_df[pat_df["Diag_omschr_EUK"].astype(str) == str(diag_omschr_euk)]
        ids = diag_rows["Diag_ID_EUK"].dropna().unique()
        if len(ids) > 0:
            try:
                diag_id_euk_val = str(int(float(str(ids[0]))))
            except (ValueError, TypeError):
                diag_id_euk_val = str(ids[0])

    # ── stuks_dag from product_diagnosis_catalogue.csv ────────────────────────
    # Filter to this diag_id_euk first; fall back to whole catalogue if unmatched.
    cat = load_catalogue_csv()
    std_by_pid: dict[str, tuple[float, str]] = {}   # prod_id → (stuks_dag, diag_id_euk)
    if not cat.empty:
        cat_diag = pd.DataFrame()
        if diag_id_euk_val and "diag_id_euk" in cat.columns:
            cat_diag = cat[cat["diag_id_euk"].astype(str).str.strip() == diag_id_euk_val]
        cat_use = cat_diag if not cat_diag.empty else cat
        for _, row in cat_use.iterrows():
            pid = str(row.get("prod_id", "")).strip()
            if pid:
                raw_std = row.get("stuks_dag", None)
                try:
                    std_val = float(raw_std)
                    if not (std_val > 0):   # catches NaN, 0, negative
                        std_val = 1.0
                except (TypeError, ValueError):
                    std_val = 1.0
                std_by_pid[pid] = (std_val, diag_id_euk_val)

    def _std(pid: str) -> tuple[float, str]:
        return std_by_pid.get(pid, (1.0, diag_id_euk_val))

    # ── Build treatment sequence per patient (grouped by Prod_ID) ────────────
    # Mirrors _build_df_grouped from sankey_functions.py with analyse_col='Prod_ID'
    df_step = (
        df.groupby(["Pat_id", "Prod_ID"], as_index=False)
        .agg(Datum=("Datum", "min"), Aantal=("Aantal", "sum"))
        .sort_values(["Pat_id", "Datum"], kind="stable")
    )
    df_step["Zorgpad_nr"] = df_step.groupby("Pat_id", sort=False).cumcount() + 1
    df_step["Prod_ID_norm"] = df_step["Prod_ID"].map(_norm_prod_id)

    wide_pid    = df_step.pivot_table(index="Pat_id", columns="Zorgpad_nr",
                                      values="Prod_ID_norm", aggfunc="first")
    wide_aantal = df_step.pivot_table(index="Pat_id", columns="Zorgpad_nr",
                                      values="Aantal", aggfunc="sum")
    steps = sorted(wide_pid.columns.tolist())
    if not steps:
        return pd.DataFrame(columns=SANKEY_COLS)

    # Heritage = first-step medicine name (colour-coding in the Sankey)
    heritage_map = (
        wide_pid[steps[0]]
        .map(lambda p: _info(str(p)).get("medicine_name", "nvt") if p else "nvt")
        .fillna("nvt")
        .rename("heritage")
    )

    rows: list[dict] = []

    # ── Virtual Source → first step ──────────────────────────────────────────
    first_step = steps[0]
    src_df = pd.DataFrame({
        "Pat_id":   wide_pid.index,
        "tgt_pid":  wide_pid[first_step],
        "aantal":   wide_aantal.get(first_step, pd.Series(0, index=wide_pid.index)),
        "heritage": heritage_map,
    }).dropna(subset=["tgt_pid"])
    src_df = src_df[src_df["tgt_pid"].astype(str).str.strip() != ""]

    for (tgt_pid, heritage), grp in src_df.groupby(["tgt_pid", "heritage"]):
        tgt_pid      = str(tgt_pid)
        std, diag_id = _std(tgt_pid)
        n_pat        = int(grp["Pat_id"].nunique())
        s_aantal     = float(grp["aantal"].sum())
        s_gem        = round(s_aantal / n_pat, 4) if n_pat > 0 else 0.0
        s_days       = round(s_aantal / std,   4) if std  > 0 else 0.0
        s_days_pat   = round(s_days   / n_pat, 4) if n_pat > 0 else 0.0
        ti           = _info(tgt_pid)
        rows.append({
            "source_prod_id":       "",
            "source_prod_nm":       "",
            "source_medicine_name": "",
            "source_group_name":    "",
            "target_prod_id":       tgt_pid,
            "target_prod_nm":       ti["prod_nm"],
            "target_medicine_name": ti["medicine_name"],
            "target_group_name":    ti["group_name"],
            "source_layer":         0,
            "target_layer":         1,
            "sum_aantal":           round(s_aantal, 4),
            "stuks_dag":            round(std, 6),
            "gem_aantal_per_pat":   s_gem,
            "diag_id_euk":          diag_id,
            "days_treated":         s_days,
            "days_per_pat":         s_days_pat,
            "pat_count":            n_pat,
            "heritage":             str(heritage),
            "diag_omschr_euk":      diag_omschr_euk,
        })

    # ── Step i → step i+1 ────────────────────────────────────────────────────
    for i in range(len(steps) - 1):
        step_a, step_b = steps[i], steps[i + 1]
        pair = pd.DataFrame({
            "Pat_id":   wide_pid.index,
            "src_pid":  wide_pid[step_a],
            "tgt_pid":  wide_pid[step_b],
            "aantal":   wide_aantal.get(step_a, pd.Series(0, index=wide_pid.index)),
            "heritage": heritage_map,
        }).dropna(subset=["src_pid", "tgt_pid"])
        pair = pair[
            (pair["src_pid"].astype(str).str.strip() != "")
            & (pair["tgt_pid"].astype(str).str.strip() != "")
        ]

        for (src_pid, tgt_pid, heritage), grp in pair.groupby(["src_pid", "tgt_pid", "heritage"]):
            src_pid      = str(src_pid)
            tgt_pid      = str(tgt_pid)
            std, diag_id = _std(src_pid)   # aantal is from the source step
            n_pat        = int(grp["Pat_id"].nunique())
            s_aantal     = float(grp["aantal"].sum())
            s_gem        = round(s_aantal / n_pat, 4) if n_pat > 0 else 0.0
            s_days       = round(s_aantal / std,   4) if std  > 0 else 0.0
            s_days_pat   = round(s_days   / n_pat, 4) if n_pat > 0 else 0.0
            si           = _info(src_pid)
            ti           = _info(tgt_pid)
            rows.append({
                "source_prod_id":       src_pid,
                "source_prod_nm":       si["prod_nm"],
                "source_medicine_name": si["medicine_name"],
                "source_group_name":    si["group_name"],
                "target_prod_id":       tgt_pid,
                "target_prod_nm":       ti["prod_nm"],
                "target_medicine_name": ti["medicine_name"],
                "target_group_name":    ti["group_name"],
                "source_layer":         int(step_a),
                "target_layer":         int(step_b),
                "sum_aantal":           round(s_aantal, 4),
                "stuks_dag":            round(std, 6),
                "gem_aantal_per_pat":   s_gem,
                "diag_id_euk":          diag_id,
                "days_treated":         s_days,
                "days_per_pat":         s_days_pat,
                "pat_count":            n_pat,
                "heritage":             str(heritage),
                "diag_omschr_euk":      diag_omschr_euk,
            })

    new_df = pd.DataFrame(rows, columns=SANKEY_COLS)

    # ── Append / overwrite ───────────────────────────────────────────────────
    if append and os.path.exists(SANKEY_ORIGINAL_CSV):
        existing = pd.read_csv(SANKEY_ORIGINAL_CSV)
        existing = existing[existing["diag_omschr_euk"] != diag_omschr_euk]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined.to_csv(SANKEY_ORIGINAL_CSV, index=False)

    # Sync edited CSV (reset this diagnosis to original)
    if os.path.exists(SANKEY_EDITED_CSV):
        edited = pd.read_csv(SANKEY_EDITED_CSV)
        edited = edited[edited["diag_omschr_euk"] != diag_omschr_euk]
        pd.concat([edited, new_df], ignore_index=True).to_csv(SANKEY_EDITED_CSV, index=False)
    else:
        new_df.to_csv(SANKEY_EDITED_CSV, index=False)

    save_patient_cache(pat_df)

    return new_df


# ---------------------------------------------------------------------------
# Product catalogue CSV generator  — v4 Excel import (preferred)
# ---------------------------------------------------------------------------

V4_CATALOGUE_PATH = os.path.join(DATA_DIR, "product_diagnosis_catelogue_v4_new.xlsx")
T4_PATH = os.path.join(
    os.path.dirname(DATA_DIR),           # project root
    "..", "9_WebApp_v3", "99_latest_versions_input", "T4_EUK_diag.xlsx"
)


def import_catalogue_from_v4(
    v4_path: str | None = None,
    t4_path: str | None = None,
) -> pd.DataFrame:
    """
    Build product_diagnosis_catalogue.csv directly from the v4 Excel file.

    The v4 file already has (Prod_ID, Diag_ID_EUK) at grain with pre-computed
    Stuks/dag, Prijs/dag, Vergoeding/dag and Marge/dag values.
    T4 supplies the Diag_omschr_EUK text for each Diag_ID_EUK.
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    vpath = v4_path or V4_CATALOGUE_PATH
    if not os.path.exists(vpath):
        return pd.DataFrame(columns=CATALOGUE_COLS)

    v4 = pd.read_excel(vpath)

    # ── Deduplicate (Prod_ID, Diag_ID_EUK) ──────────────────────────────────
    # Rule: keep rows where Info_Dosage? == True first; among those (or among
    # all rows if none qualify), take the one with the most recent Date edited.
    _info_col = "Info_Dosage?"
    _date_col = "Date edited"
    v4["_info_true"] = (
        v4[_info_col].astype(str).str.strip().str.lower().isin(["true", "1", "yes"])
        if _info_col in v4.columns else True
    )
    v4["_date_edited"] = (
        pd.to_datetime(v4[_date_col], errors="coerce")
        if _date_col in v4.columns else pd.NaT
    )
    v4 = (
        v4.sort_values(["_info_true", "_date_edited"], ascending=[False, False])
        .drop_duplicates(subset=["Prod_ID", "Diag_ID_EUK"])
        .drop(columns=["_info_true", "_date_edited"])
        .reset_index(drop=True)
    )

    # Admin method: prefer _y (dosage-linked), fall back to _x
    if "Administration Method_y" in v4.columns:
        v4["_admin"] = v4["Administration Method_y"].fillna(
            v4.get("Administration Method_x", "")
        )
    elif "Administration Method_x" in v4.columns:
        v4["_admin"] = v4["Administration Method_x"]
    elif "Administration Method" in v4.columns:
        v4["_admin"] = v4["Administration Method"]
    else:
        v4["_admin"] = ""

    rename = {
        "Prod_ID":           "prod_id",
        "Diag_ID_EUK":       "diag_id_euk",
        "Group Name":        "group_name",
        "Medicine Name":     "medicine_name",
        "Prod_nm":           "prod_nm",
        "Prod_omschr":       "prod_omschr",
        "_admin":            "admin_method",
        "Prijs/st":          "prijs_st",
        "Vergoeding/st":     "vergoeding_st",
        "Marge/st":          "margin_st",
        "Dosage Frequency":  "freq_dosage",
        "Stuks/toediening":  "stuks_per_toediening",
        "Stuks/dag":         "stuks_dag",
        "Prijs/dag":         "prijs_dag",
        "Vergoeding/dag":    "vergoeding_dag",
        "Marge/dag":         "margin_dag",
    }
    keep = {k: v for k, v in rename.items() if k in v4.columns}
    out = v4[list(keep.keys())].rename(columns=keep).copy()

    # Normalise numeric IDs
    out["prod_id"] = out["prod_id"].apply(_norm_prod_id)
    out["diag_id_euk"] = (
        out["diag_id_euk"]
        .astype(str).str.strip()
        .apply(lambda x: str(int(float(x))) if x.replace(".", "").isdigit() else x)
    )

    # Add diag_omschr_euk from T4 lookup
    tpath = t4_path or T4_PATH
    if os.path.exists(tpath):
        t4 = pd.read_excel(tpath)[["Diag_ID_EUK", "Diag_omschr_EUK"]].dropna()
        t4["_id_key"] = t4["Diag_ID_EUK"].astype(str).str.strip().apply(
            lambda x: str(int(float(x))) if x.replace(".", "").isdigit() else x
        )
        id_to_name = t4.set_index("_id_key")["Diag_omschr_EUK"].to_dict()
        out["diag_omschr_euk"] = out["diag_id_euk"].map(id_to_name).fillna("")
    else:
        out["diag_omschr_euk"] = ""

    # Ensure all CATALOGUE_COLS present
    for c in CATALOGUE_COLS:
        if c not in out.columns:
            out[c] = ""

    out = out[CATALOGUE_COLS]

    # Round financials
    for c in ["prijs_st", "vergoeding_st", "margin_st",
              "prijs_dag", "vergoeding_dag", "margin_dag", "stuks_dag"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce").round(6)

    sort_cols = [c for c in ["group_name", "medicine_name", "prod_nm", "admin_method"]
                 if c in out.columns]
    out = (
        out.sort_values(sort_cols)
        .drop_duplicates(subset=["prod_id", "diag_id_euk"])
        .reset_index(drop=True)
    )
    out.to_csv(CATALOGUE_CSV, index=False)
    return out


# ---------------------------------------------------------------------------
# Product catalogue CSV generator  — T1+T2 fallback
# ---------------------------------------------------------------------------

def generate_catalogue_csv(t1: pd.DataFrame, t2: pd.DataFrame) -> pd.DataFrame:
    """
    Generate product_diagnosis_catalogue.csv from raw T1 + T2 files.
    Use import_catalogue_from_v4() instead when the v4 Excel is available.

    One row per (prod_id, diag_id_euk) — the same product can have different
    dosage frequencies and per-dag rates for different diagnoses.
    diag_omschr_euk is filled from T4 when available.
    """
    from backend.financial_utils import build_t1t2
    os.makedirs(DATA_DIR, exist_ok=True)

    cat = build_t1t2(t1, t2)
    if cat.empty:
        return pd.DataFrame(columns=CATALOGUE_COLS)

    rename = {
        "Prod_ID":               "prod_id",
        "Diag_ID_EUK":           "diag_id_euk",
        "Group Name":            "group_name",
        "Medicine Name":         "medicine_name",
        "Prod_nm":               "prod_nm",
        "Prod_omschr":           "prod_omschr",
        "Administration Method": "admin_method",
        "Prijs/st":              "prijs_st",
        "Vergoeding/st":         "vergoeding_st",
        "Dosage Frequency":      "freq_dosage",
        "Stuks/toediening":      "stuks_per_toediening",
        "stuks_dag":             "stuks_dag",
        "uitgaven_dag":          "prijs_dag",
        "vergoeding_dag":        "vergoeding_dag",
        "margin_dag":            "margin_dag",
    }
    keep = {k: v for k, v in rename.items() if k in cat.columns}
    out = cat[list(keep.keys())].rename(columns=keep).copy()

    v = out.get("vergoeding_st", pd.Series(0, index=out.index)).fillna(0)
    p = out.get("prijs_st",      pd.Series(0, index=out.index)).fillna(0)
    out["margin_st"] = (v - p).round(6)

    # diag_omschr_euk is left blank — user fills it in manually
    out["diag_omschr_euk"] = ""

    for c in CATALOGUE_COLS:
        if c not in out.columns:
            out[c] = ""

    out = out[CATALOGUE_COLS]
    for c in ["prijs_st", "vergoeding_st", "margin_st",
              "prijs_dag", "vergoeding_dag", "margin_dag", "stuks_dag"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce").round(6)

    sort_cols = [c for c in ["group_name", "medicine_name", "prod_nm", "admin_method"]
                 if c in out.columns]
    dedup_cols = [c for c in ["prod_id", "diag_id_euk"] if c in out.columns]
    out = (
        out.sort_values(sort_cols)
        .drop_duplicates(subset=dedup_cols if dedup_cols else ["prod_id"])
        .reset_index(drop=True)
    )
    out.to_csv(CATALOGUE_CSV, index=False)
    return out


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_sankey_csv(edited: bool = True) -> pd.DataFrame:
    path = SANKEY_EDITED_CSV if edited else SANKEY_ORIGINAL_CSV
    if not os.path.exists(path):
        return pd.DataFrame(columns=SANKEY_COLS)
    df = pd.read_csv(path, dtype={"source_prod_id": str, "target_prod_id": str})
    for c in SANKEY_COLS:
        if c not in df.columns:
            df[c] = ""
    # Normalize prod_id columns: strip float .0 artifacts (e.g. "16634195.0" → "16634195")
    for col in ("source_prod_id", "target_prod_id"):
        df[col] = df[col].apply(
            lambda s: _norm_prod_id(s) if str(s).strip() not in ("", "nan", "NaN") else ""
        )
    return df


def load_catalogue_csv() -> pd.DataFrame:
    if not os.path.exists(CATALOGUE_CSV):
        return pd.DataFrame(columns=CATALOGUE_COLS)
    df = pd.read_csv(CATALOGUE_CSV)
    for c in CATALOGUE_COLS:
        if c not in df.columns:
            df[c] = ""
    return df


def save_patient_cache(pat_df: pd.DataFrame) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    pat_df.to_csv(PATIENT_CACHE_CSV, index=False)


_PAT_COLS_NEEDED = [
    "Pat_id", "Datum", "Diag_ID_EUK", "Diag_omschr_EUK", "Prod_ID",
    "Prod_nm", "Med_nm", "Group_nm", "Aantal", "Voorschrijver_nm",
]


def load_patient_cache() -> pd.DataFrame:
    if not os.path.exists(PATIENT_CACHE_CSV):
        return pd.DataFrame()
    df = pd.read_csv(PATIENT_CACHE_CSV)
    # Case-insensitive column normalization to match what generate_sankey_csv expects
    col_lower = {c.lower(): c for c in df.columns}
    renames = {col_lower[n.lower()]: n for n in _PAT_COLS_NEEDED
               if n not in df.columns and n.lower() in col_lower}
    if renames:
        df = df.rename(columns=renames)
    if "Datum" in df.columns:
        df["Datum"] = pd.to_datetime(df["Datum"], errors="coerce")
    if "Pat_id" not in df.columns:
        return pd.DataFrame()
    return df


def load_prod_omschr_lookup() -> dict[str, str]:
    """Return {prod_id: Prod_omschr}. Falls back to prod_nm when omschr unavailable."""
    if os.path.exists(CATALOGUE_CSV):
        cat = pd.read_csv(CATALOGUE_CSV, dtype=str)
        if "prod_omschr" in cat.columns:
            result = {}
            for _, row in cat.drop_duplicates("prod_id").iterrows():
                pid = str(row["prod_id"]).strip()
                val = str(row.get("prod_omschr", "") or "").strip()
                if val and val != "nan":
                    result[pid] = val
            if result:
                return result

    for fname in ["_uploaded_v4.xlsx", "product_diagnosis_catelogue_v5.xlsx"]:
        path = os.path.join(DATA_DIR, fname)
        if not os.path.exists(path):
            continue
        try:
            xl = pd.read_excel(path)
            if "Prod_ID" in xl.columns and "Prod_omschr" in xl.columns:
                result = {}
                for _, row in xl.drop_duplicates("Prod_ID").iterrows():
                    pid = _norm_prod_id(row["Prod_ID"])
                    val = str(row.get("Prod_omschr", "") or "").strip()
                    if pid and val and val != "nan":
                        result[pid] = val
                if result:
                    return result
        except Exception:
            continue

    return {}


def _extract_prod_id(label: str) -> str:
    """Extract raw prod_id from 'Prod_omschr (prod_id)' combined label."""
    if "(" in label and label.rstrip().endswith(")"):
        return label.rsplit("(", 1)[1].rstrip(")").strip()
    return label


def get_available_diagnoses() -> list[str]:
    if not os.path.exists(SANKEY_ORIGINAL_CSV):
        return []
    df = pd.read_csv(SANKEY_ORIGINAL_CSV, usecols=["diag_omschr_euk"])
    return sorted(df["diag_omschr_euk"].dropna().unique().tolist(), key=str.casefold)


def reset_edited_for_diagnosis(diag_omschr_euk: str) -> bool:
    if not os.path.exists(SANKEY_ORIGINAL_CSV):
        return False
    orig = pd.read_csv(SANKEY_ORIGINAL_CSV)
    orig_diag = orig[orig["diag_omschr_euk"] == diag_omschr_euk]
    if os.path.exists(SANKEY_EDITED_CSV):
        edited = pd.read_csv(SANKEY_EDITED_CSV)
        edited = edited[edited["diag_omschr_euk"] != diag_omschr_euk]
        pd.concat([edited, orig_diag], ignore_index=True).to_csv(SANKEY_EDITED_CSV, index=False)
    else:
        orig_diag.to_csv(SANKEY_EDITED_CSV, index=False)
    return True


# ---------------------------------------------------------------------------
# Flow capacity + cascade (level-aware, works at any aggregation granularity)
# ---------------------------------------------------------------------------

def get_node_capacity(
    flows_df: pd.DataFrame,
    node_label: str,
    node_layer: int,
    level: str = "prod_nm",
) -> float:
    """
    Max days_treated available for a source node at the given aggregation level.

    'Source' (layer 0): total of all Source outgoing flows.
    Other nodes: sum of incoming days_treated; falls back to outgoing if leaf.
    """
    if node_label == "Source" and int(node_layer) == 0:
        return float(flows_df.loc[
            flows_df["source_label"] == "Source", "days_treated"
        ].sum())

    incoming = flows_df.loc[
        (flows_df["target_label"] == node_label)
        & (flows_df["source_layer"].astype(int) == int(node_layer) - 1),
        "days_treated",
    ].sum()
    if incoming > 0:
        return float(incoming)

    return float(flows_df.loc[
        flows_df["source_label"] == node_label, "days_treated"
    ].sum())


def cascade_and_save_flow(
    diag_omschr_euk: str,
    source_label: str,
    source_layer: int,
    target_label: str,
    target_layer: int,
    level: str,
    new_days_treated: float,
) -> pd.DataFrame:
    """
    Update all prod_id-level flows matching the aggregated (source_label, target_label)
    at the given level, proportionally scale them to reach new_days_treated,
    cascade to the target's outgoing flows, save, and return the diag-filtered df.
    """
    df = load_sankey_csv(edited=True)
    diag_mask = df["diag_omschr_euk"] == diag_omschr_euk

    src_col, tgt_col = LEVEL_COLS.get(level, ("source_prod_nm", "target_prod_nm"))

    # At prod_id level, labels are now "Prod_omschr (prod_id)" — extract the raw id
    if level == "prod_id":
        source_key = _extract_prod_id(source_label)
        target_key = _extract_prod_id(target_label)
    else:
        source_key = source_label
        target_key = target_label

    # For Source virtual node, always match on source_prod_id == ""
    if source_label == "Source":
        src_match = df["source_prod_id"].astype(str).str.strip().isin(["", "nan"])
    else:
        src_match = df[src_col].astype(str) == source_key

    flow_mask = (
        diag_mask
        & src_match
        & (df["source_layer"].astype(int) == int(source_layer))
        & (df[tgt_col].astype(str) == target_key)
        & (df["target_layer"].astype(int) == int(target_layer))
    )
    if not flow_mask.any():
        return df[diag_mask]

    old_total = float(df.loc[flow_mask, "days_treated"].sum())
    new_total  = max(0.0, float(new_days_treated))

    if old_total > 0:
        scale = new_total / old_total
        df.loc[flow_mask, "days_treated"] = (df.loc[flow_mask, "days_treated"] * scale).round(4)
        stds = df.loc[flow_mask, "stuks_dag"].where(df.loc[flow_mask, "stuks_dag"] > 0, 1.0)
        df.loc[flow_mask, "sum_aantal"] = (df.loc[flow_mask, "days_treated"] / stds).round(4)
    else:
        df.loc[flow_mask, "days_treated"] = round(new_total / flow_mask.sum(), 4)

    # Cascade: scale target's outgoing flows proportionally
    delta = new_total - old_total
    out_mask = (
        diag_mask
        & (df[src_col].astype(str) == target_key)
        & (df["source_layer"].astype(int) == int(target_layer))
    )
    if out_mask.any() and delta != 0:
        old_out = float(df.loc[out_mask, "days_treated"].sum())
        new_out = max(0.0, old_out + delta)
        if old_out > 0:
            out_scale = new_out / old_out
            df.loc[out_mask, "days_treated"] = (
                df.loc[out_mask, "days_treated"] * out_scale
            ).clip(lower=0).round(4)
            out_stds = df.loc[out_mask, "stuks_dag"].where(df.loc[out_mask, "stuks_dag"] > 0, 1.0)
            df.loc[out_mask, "sum_aantal"] = (df.loc[out_mask, "days_treated"] / out_stds).round(4)

    df.to_csv(SANKEY_EDITED_CSV, index=False)
    return df[df["diag_omschr_euk"] == diag_omschr_euk]
