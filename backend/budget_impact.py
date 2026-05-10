"""
Budget impact analysis for treatment-pathway changes (year-1 starter cohort).

Per-drug attribution methodology, aligned with the prompt's
"addressable_volume = total_quantity × starter_percentage" rule:

  For each medicine d that appears in the actual data, the year-1 starter
  cost under a pathway X is

      cost_X[d] = T_d × rate_X[d]

  where
    T_d        — drug d's actual layer-0 (Source → d) days_treated last year
    rate_X[d]  — d's prijs_dag / vergoeding_dag / margin_dag in pathway X,
                 weighted by that pathway's prod_id mix (design's explicit
                 mix or the actual data's observed Aantal-weighted mix)

  Total year-1 cost(X) = Σ_d T_d × rate_X[d]
  Δ between two pathways = Σ_d T_d × (rate_B[d] − rate_A[d])

  Drugs absent from the design's medicine_products keep their actual rate
  (pass-through) — the design didn't make a choice for them, so they contribute
  0 to the delta. Drugs absent from actuals but present in the design have
  T_d = 0 and contribute nothing (no observed baseline volume).

This deliberately uses each drug's *own* observed baseline volume as the
addressable amount instead of redistributing the cohort total across the
design's claimed shares — so a pure prod_id swap within an unchanged drug
share applies the rate delta only to that drug's actual usage, not to the
full cohort.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from backend.design_io import design_medicine_rates


def _norm_pid(val: Any) -> str:
    try:
        return str(int(float(str(val))))
    except (ValueError, TypeError):
        return ""


def _safe(v: Any) -> float:
    try:
        x = float(v)
        return 0.0 if x != x else x
    except Exception:
        return 0.0


def data_medicine_rates(
    pat_in_window: pd.DataFrame,
    catalogue_df: pd.DataFrame,
    diag_id_euk: str,
) -> dict[str, dict[str, float]]:
    """
    Aantal-weighted prijs_dag / vergoeding_dag / margin_dag per medicine_name
    derived from the actual prod_id mix observed in `pat_in_window`.
    """
    out: dict[str, dict[str, float]] = {}
    if pat_in_window.empty or catalogue_df.empty:
        return out

    cat = catalogue_df.copy()
    cat["prod_id"] = cat["prod_id"].astype(str).str.strip()
    if diag_id_euk and "diag_id_euk" in cat.columns:
        cat_diag = cat[cat["diag_id_euk"].astype(str).str.strip() == str(diag_id_euk)]
        if not cat_diag.empty:
            cat = cat_diag
    cat_by_id = cat.drop_duplicates("prod_id").set_index("prod_id")

    pat = pat_in_window.copy()
    if "Prod_ID" not in pat.columns or "Med_nm" not in pat.columns:
        return out
    pat["pid_norm"] = pat["Prod_ID"].apply(_norm_pid)
    pat = pat[pat["pid_norm"] != ""]
    if pat.empty:
        return out

    grouped = pat.groupby(["Med_nm", "pid_norm"])["Aantal"].sum().reset_index()
    for med, sub in grouped.groupby("Med_nm"):
        total_w = float(sub["Aantal"].sum())
        if total_w <= 0:
            continue
        prijs = verg = marge = 0.0
        for _, r in sub.iterrows():
            pid = str(r["pid_norm"])
            w = float(r["Aantal"]) / total_w
            if pid not in cat_by_id.index:
                continue
            row = cat_by_id.loc[pid]
            row = row.iloc[0] if isinstance(row, pd.DataFrame) else row
            prijs += w * _safe(row.get("prijs_dag", 0))
            verg  += w * _safe(row.get("vergoeding_dag", 0))
            marge += w * _safe(row.get("margin_dag", 0))
        out[str(med)] = {
            "prijs_dag":      prijs,
            "vergoeding_dag": verg,
            "margin_dag":     marge,
        }
    return out


def drug_starter_days(data_flows: pd.DataFrame) -> dict[str, float]:
    """
    Per-medicine actual baseline starter days from the data Sankey:
      T_d = Σ days_treated where source_layer == 0 and target_label == d.

    Layer-0 flows are Source → drug, so this is the year's first-line
    treatment volume per drug for the selected diagnosis cohort.
    """
    if data_flows.empty or "source_layer" not in data_flows.columns:
        return {}
    l0 = data_flows[data_flows["source_layer"].astype(int) == 0]
    if l0.empty:
        return {}
    return {
        str(k): float(v)
        for k, v in l0.groupby("target_label")["days_treated"].sum().items()
    }


def compute_budget_impact(
    design: dict,
    data_flows_agg: pd.DataFrame,
    pat_in_window: pd.DataFrame,
    catalogue_df: pd.DataFrame,
) -> dict[str, float]:
    """
    Per-drug year-1 financial projection (see module docstring).

    Returns numbers in €:
      actual_uitgaven / vergoeding / marge   – Σ_d T_d × actual_rate[d]
      design_uitgaven / vergoeding / marge   – Σ_d T_d × design_rate[d]
      delta_uitgaven / vergoeding / marge    – design − actual
      net_savings                            – −delta_uitgaven
      baseline_starter_days                  – Σ_d T_d, carried for context
    """
    diag_id = str(design.get("diag_id_euk", "") or "").strip()

    T_by_drug = drug_starter_days(data_flows_agg)
    baseline_days = float(sum(T_by_drug.values()))

    actual_rates = data_medicine_rates(pat_in_window, catalogue_df, diag_id)
    design_rates = design_medicine_rates(design, catalogue_df)

    actual = {"uitgaven": 0.0, "vergoeding": 0.0, "marge": 0.0}
    design_proj = {"uitgaven": 0.0, "vergoeding": 0.0, "marge": 0.0}

    for d, T_d in T_by_drug.items():
        if T_d <= 0:
            continue
        a = actual_rates.get(d) or {}
        # Pass-through rule: drugs the design does NOT address (no entry in
        # medicine_products → no design rate) keep their actual rate on both
        # sides, so they contribute 0 to the delta. Only drugs the design
        # explicitly chooses a prod-mix for can change the projection.
        b = design_rates.get(d) or a

        actual["uitgaven"]        += T_d * _safe(a.get("prijs_dag", 0.0))
        actual["vergoeding"]      += T_d * _safe(a.get("vergoeding_dag", 0.0))
        actual["marge"]           += T_d * _safe(a.get("margin_dag", 0.0))
        design_proj["uitgaven"]   += T_d * _safe(b.get("prijs_dag", 0.0))
        design_proj["vergoeding"] += T_d * _safe(b.get("vergoeding_dag", 0.0))
        design_proj["marge"]      += T_d * _safe(b.get("margin_dag", 0.0))

    return {
        "actual_uitgaven":       actual["uitgaven"],
        "actual_vergoeding":     actual["vergoeding"],
        "actual_marge":          actual["marge"],
        "design_uitgaven":       design_proj["uitgaven"],
        "design_vergoeding":     design_proj["vergoeding"],
        "design_marge":          design_proj["marge"],
        "delta_uitgaven":        design_proj["uitgaven"]   - actual["uitgaven"],
        "delta_vergoeding":      design_proj["vergoeding"] - actual["vergoeding"],
        "delta_marge":           design_proj["marge"]      - actual["marge"],
        "net_savings":           actual["uitgaven"]        - design_proj["uitgaven"],
        "baseline_starter_days": baseline_days,
    }
