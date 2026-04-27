"""
Shared feedback utilities for loading and querying feedback.csv.
Adapted for the Sankey-Ideas webapp — FEEDBACK_CSV points to this project's local file.
"""

import os

import pandas as pd
import streamlit as st

FEEDBACK_COLS = [
    "timestamp", "prod_id_huidig", "diag_id_euk", "diag_omschr_euk",
    "group", "medicine_name", "prod_nm",
    "admin_method_huidig", "admin_method_alternatief",
    "medicine_name_max", "group_name_max", "prod_nm_max", "prod_id_max",
    "doen", "verdere_studie", "niet_doen",
    "niveau", "notities", "Reden_beslissing", "auto_classified",
]

# Local feedback.csv lives alongside app.py in this project root
_project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDBACK_CSV = os.path.join(_project_dir, "feedback.csv")


def _is_one(val) -> bool:
    try:
        return float(val) == 1.0
    except (ValueError, TypeError):
        return False


def norm_id(val) -> str:
    try:
        return str(int(float(str(val).strip())))
    except (ValueError, TypeError):
        return str(val).strip()


def _is_prod_id_max_empty(val) -> bool:
    return (
        pd.isna(val)
        or str(val).strip() == ""
        or str(val).strip() == "–"
        or str(val).strip().lower() == "nan"
    )


def _latest_row(prod_id, feedback_df: pd.DataFrame, diag_id=None, prod_id_max=None):
    if feedback_df.empty or "prod_id_huidig" not in feedback_df.columns:
        return None
    norm_pid = norm_id(prod_id)
    row = feedback_df[feedback_df["prod_id_huidig"].apply(norm_id) == norm_pid]
    if row.empty:
        return None
    if diag_id is not None and "diag_id_euk" in feedback_df.columns:
        diag_filtered = row[row["diag_id_euk"].astype(str) == str(diag_id)]
        if diag_filtered.empty:
            return None
        row = diag_filtered
    if "prod_id_max" in row.columns:
        query_empty = _is_prod_id_max_empty(prod_id_max)
        if query_empty:
            row = row[row["prod_id_max"].apply(lambda v: _is_prod_id_max_empty(v))]
        else:
            norm_max = norm_id(prod_id_max)
            max_filtered = row[
                row["prod_id_max"].apply(
                    lambda v: norm_id(v) if pd.notna(v) and not _is_prod_id_max_empty(v) else ""
                )
                == norm_max
            ]
            if max_filtered.empty:
                return None
            row = max_filtered
    if row.empty:
        return None
    if "timestamp" in row.columns:
        row = row.sort_values("timestamp", ascending=False)
    return row.iloc[0]


def load_feedback(csv_path: str | None = None) -> pd.DataFrame:
    path = csv_path or FEEDBACK_CSV
    if os.path.exists(path):
        try:
            fb = pd.read_csv(path)
            for col in FEEDBACK_COLS:
                if col not in fb.columns:
                    fb[col] = ""
            return fb
        except Exception:
            pass
    return pd.DataFrame(columns=FEEDBACK_COLS)


def save_idea(row_dict: dict) -> None:
    """Append one idea row to the local feedback.csv, creating the file if needed."""
    fb = load_feedback()
    new_row = pd.DataFrame([row_dict])
    for col in FEEDBACK_COLS:
        if col not in new_row.columns:
            new_row[col] = ""
    fb = pd.concat([fb, new_row[FEEDBACK_COLS]], ignore_index=True)
    fb.to_csv(FEEDBACK_CSV, index=False)
    load_feedback_cached.clear()


def get_beslissing(prod_id, feedback_df: pd.DataFrame, diag_id=None, prod_id_max=None) -> str:
    r = _latest_row(prod_id, feedback_df, diag_id=diag_id, prod_id_max=prod_id_max)
    if r is None:
        return "Nog te bepalen"
    if _is_one(r.get("doen")):
        return "Doen"
    if _is_one(r.get("verdere_studie")):
        return "Verdere Studie"
    if _is_one(r.get("niet_doen")):
        return "Niet Doen"
    return "Nog te bepalen"


@st.cache_data(ttl=60)
def load_feedback_cached() -> pd.DataFrame:
    return load_feedback()
