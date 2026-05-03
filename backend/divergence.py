"""
Design vs. data divergence analysis (used by the Implementatie page).

Given a saved design and the current data Sankey at medicine_name level,
compute per-flow share deltas and translate them into financial impact:

  Δ days  = (actual_share − design_share) × total_days_out_of_source
  Δ marge = Δ days × (data_med_rate.margin_dag − design_med_rate.margin_dag)

Both Δ uitgaven and Δ vergoeding follow the same shape. The data-side
medicine rate is weighted by the actual prod_id mix observed in the patient
records inside the active filter window; the design-side rate uses the
prod_id weights from the design JSON.
"""
from __future__ import annotations

import pandas as pd

from backend.design_io import design_medicine_rates


def _norm_pid(val) -> str:
    try:
        return str(int(float(str(val))))
    except (ValueError, TypeError):
        return ""


def _data_medicine_rates_from_records(
    pat_in_window: pd.DataFrame,
    catalogue_df: pd.DataFrame,
    diag_id_euk: str,
) -> dict[str, dict[str, float]]:
    """
    For each medicine_name observed in pat_in_window, derive the
    Aantal-weighted prijs_dag / vergoeding_dag / margin_dag using
    per-prod_id rates from the catalogue.
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
    pat["pid_norm"] = pat["Prod_ID"].apply(_norm_pid)
    pat = pat[pat["pid_norm"] != ""]
    if pat.empty or "Med_nm" not in pat.columns:
        return out

    def _safe(v) -> float:
        try:
            x = float(v)
            return 0.0 if x != x else x
        except Exception:
            return 0.0

    grouped = (
        pat.groupby(["Med_nm", "pid_norm"])["Aantal"].sum().reset_index()
    )
    for med, sub in grouped.groupby("Med_nm"):
        total_w = float(sub["Aantal"].sum())
        if total_w <= 0:
            continue
        prijs = verg = marge = 0.0
        for _, r in sub.iterrows():
            pid = str(r["pid_norm"])
            w   = float(r["Aantal"]) / total_w
            if pid not in cat_by_id.index:
                continue
            row = cat_by_id.loc[pid]
            prijs += w * _safe(row.get("prijs_dag",      0))
            verg  += w * _safe(row.get("vergoeding_dag", 0))
            marge += w * _safe(row.get("margin_dag",     0))
        out[str(med)] = {
            "prijs_dag":      prijs,
            "vergoeding_dag": verg,
            "margin_dag":     marge,
        }
    return out


def compute_divergence(
    design: dict,
    data_flows: pd.DataFrame,
    pat_in_window: pd.DataFrame,
    catalogue_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Per (source_medicine, target_medicine) flow, return:
      design_share, actual_share, delta_share, total_days, delta_days,
      delta_uitgaven, delta_vergoeding, delta_marge.

    Positive delta_share means the actual data over-uses that target relative
    to the design. Sign of delta_marge is "actual − design": negative means
    money left on the table by the actual prescribing.
    """
    cols = [
        "source", "target",
        "design_share", "actual_share", "delta_share",
        "total_days", "delta_days",
        "delta_uitgaven", "delta_vergoeding", "delta_marge",
        "data_marge_dag", "design_marge_dag",
    ]
    if data_flows.empty:
        return pd.DataFrame(columns=cols)

    # Aggregate actual flows per (source, target) — already at medicine_name
    df = data_flows.copy()
    df["source"] = df["source_label"].astype(str)
    df["target"] = df["target_label"].astype(str)
    actual = (
        df.groupby(["source", "target"])["days_treated"].sum().reset_index()
    )
    src_totals = actual.groupby("source")["days_treated"].sum().to_dict()

    # Index design flows for lookup
    design_share_lookup: dict[tuple[str, str], float] = {}
    for f in design.get("flows", []) or []:
        key = (str(f["source"]), str(f["target"]))
        design_share_lookup[key] = float(f.get("share", 0) or 0)

    diag_id = str(design.get("diag_id_euk", "") or "").strip()
    design_rates = design_medicine_rates(design, catalogue_df)
    data_rates   = _data_medicine_rates_from_records(pat_in_window, catalogue_df, diag_id)

    rows: list[dict] = []
    pairs = set(actual[["source", "target"]].itertuples(index=False, name=None))
    pairs.update(design_share_lookup.keys())

    for (src, tgt) in pairs:
        total_days = float(src_totals.get(src, 0.0))
        actual_days = float(
            actual.loc[(actual["source"] == src) & (actual["target"] == tgt), "days_treated"].sum()
        )
        actual_share = (actual_days / total_days * 100.0) if total_days > 0 else 0.0
        design_share = float(design_share_lookup.get((src, tgt), 0.0))
        delta_share  = actual_share - design_share
        delta_days   = (delta_share / 100.0) * total_days

        d_rate  = data_rates.get(tgt,   {})
        ds_rate = design_rates.get(tgt, {})
        d_pr  = float(d_rate.get("prijs_dag",      0.0))
        d_vg  = float(d_rate.get("vergoeding_dag", 0.0))
        d_mg  = float(d_rate.get("margin_dag",     0.0))
        ds_pr = float(ds_rate.get("prijs_dag",      0.0))
        ds_vg = float(ds_rate.get("vergoeding_dag", 0.0))
        ds_mg = float(ds_rate.get("margin_dag",     0.0))

        rows.append({
            "source":           src,
            "target":           tgt,
            "design_share":     design_share,
            "actual_share":     actual_share,
            "delta_share":      delta_share,
            "total_days":       total_days,
            "delta_days":       delta_days,
            "delta_uitgaven":   delta_days * (d_pr - ds_pr),
            "delta_vergoeding": delta_days * (d_vg - ds_vg),
            "delta_marge":      delta_days * (d_mg - ds_mg),
            "data_marge_dag":   d_mg,
            "design_marge_dag": ds_mg,
        })

    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values("delta_marge", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)


def voorschrijver_breakdown(
    design: dict,
    pat_in_window: pd.DataFrame,
    catalogue_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Per-voorschrijver financial divergence vs the design's first-layer
    (Source → medicine_name) distribution.

    The Sankey has a flow structure (multiple layers) but attributing an
    individual prescriber to a specific later-step transition is noisy when
    a patient moves through many doctors. To stay defensible we compare each
    voorschrijver's actual medicine usage (in days, derived from Aantal /
    stuks_dag) against the share they "should" have prescribed each
    medicine according to the design's first layer.

    Returns one row per voorschrijver with delta_uitgaven / delta_vergoeding /
    delta_marge / abs_marge, sorted by |Δ marge| desc.
    """
    cols = ["voorschrijver", "delta_uitgaven", "delta_vergoeding",
            "delta_marge", "abs_marge", "totaal_dagen"]
    if pat_in_window.empty or "Voorschrijver_nm" not in pat_in_window.columns:
        return pd.DataFrame(columns=cols)

    # Design's first-layer distribution = expected share of each target medicine
    expected_share: dict[str, float] = {}
    for f in design.get("flows", []) or []:
        if int(f.get("source_layer", 0)) == 0:
            expected_share[str(f["target"])] = float(f.get("share", 0) or 0) / 100.0
    if not expected_share:
        return pd.DataFrame(columns=cols)

    diag_id = str(design.get("diag_id_euk", "") or "").strip()
    cat = catalogue_df.copy()
    cat["prod_id"] = cat["prod_id"].astype(str).str.strip()
    if diag_id and "diag_id_euk" in cat.columns:
        cat_diag = cat[cat["diag_id_euk"].astype(str).str.strip() == diag_id]
        if not cat_diag.empty:
            cat = cat_diag
    cat_by_id = cat.drop_duplicates("prod_id").set_index("prod_id")

    design_rates = design_medicine_rates(design, catalogue_df)

    pat = pat_in_window.copy()
    pat["pid_norm"] = pat["Prod_ID"].apply(_norm_pid)
    pat = pat[pat["pid_norm"] != ""]
    if pat.empty:
        return pd.DataFrame(columns=cols)

    # Translate Aantal → days using each prod_id's catalogue stuks_dag
    def _stuks_dag(pid: str) -> float:
        if pid not in cat_by_id.index:
            return 0.0
        row = cat_by_id.loc[pid]
        try:
            v = float(row.get("stuks_dag", 0))
        except Exception:
            return 0.0
        return v if v > 0 else 0.0

    pat["stuks_dag"] = pat["pid_norm"].map(_stuks_dag)
    pat = pat[pat["stuks_dag"] > 0]
    if pat.empty:
        return pd.DataFrame(columns=cols)
    pat["days"] = pat["Aantal"].astype(float) / pat["stuks_dag"]

    rows: list[dict] = []
    for vrs, sub in pat.groupby("Voorschrijver_nm"):
        total_days = float(sub["days"].sum())
        if total_days <= 0:
            continue
        # Actual share per medicine_name
        actual_med = sub.groupby("Med_nm")["days"].sum().to_dict()
        d_uit = d_verg = d_marge = 0.0
        for med, exp_share in expected_share.items():
            actual_days = float(actual_med.get(med, 0.0))
            expected_days = exp_share * total_days
            delta_days = actual_days - expected_days
            ds_rate = design_rates.get(med, {})
            # Use design rate as both data and design proxy when the medicine
            # appears: divergence here is "wrong medicine entirely", so the
            # actual cost is what was actually billed (computed below from
            # prod_ids the prescriber used) — for simplicity at the
            # per-voorschrijver level we compare the missing-or-extra days
            # against the design medicine's marge_dag (i.e. opportunity cost).
            d_uit   += delta_days * float(ds_rate.get("prijs_dag",      0.0)) * -1
            d_verg  += delta_days * float(ds_rate.get("vergoeding_dag", 0.0)) * -1
            d_marge += delta_days * float(ds_rate.get("margin_dag",     0.0)) * -1
        rows.append({
            "voorschrijver":    str(vrs),
            "delta_uitgaven":   d_uit,
            "delta_vergoeding": d_verg,
            "delta_marge":      d_marge,
            "abs_marge":        abs(d_marge),
            "totaal_dagen":     total_days,
        })

    out = pd.DataFrame(rows, columns=cols)
    if out.empty:
        return out
    return out.sort_values("abs_marge", ascending=False).reset_index(drop=True)
