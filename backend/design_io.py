"""
Design save/load for the Ontwerp page.

A design captures the pharmacist's idealised patient flow at medicine_name
level, with an explicit prod_id mix per medicine_name (so that the
Implementatie page can compute a weighted prijs/vergoeding/marge per dag for
each medicine node).

JSON schema:
{
  "diagnosis":   "colitis ulcerosa: volwassen",
  "diag_id_euk": "1234",
  "created_at":  "2026-04-27 14:32:08",
  "flows": [
    {"source": "Source", "source_layer": 0, "target": "infliximab", "target_layer": 1, "share": 60.0},
    ...
  ],
  "medicine_products": {
    "infliximab": [{"prod_id": "16043065", "share": 100.0}],
    "adalimumab": [{"prod_id": "17239745", "share": 70.0}, {"prod_id": "17216001", "share": 30.0}]
  }
}
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from typing import Any

import pandas as pd

from backend.csv_builder import DATA_DIR

DESIGNS_DIR = os.path.join(DATA_DIR, "designs")


def slugify(text: str) -> str:
    if not text:
        return "diagnose"
    norm = unicodedata.normalize("NFKD", str(text))
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    norm = norm.lower()
    norm = re.sub(r"[^a-z0-9]+", "_", norm).strip("_")
    return norm or "diagnose"


def list_designs(diag_omschr_euk: str) -> list[dict]:
    """Return [{path, filename, created_at}, …] for this diagnosis, newest first."""
    if not os.path.isdir(DESIGNS_DIR):
        return []
    slug = slugify(diag_omschr_euk)
    out: list[dict] = []
    for fn in os.listdir(DESIGNS_DIR):
        if not fn.endswith(".json"):
            continue
        if not fn.startswith(slug + "__"):
            continue
        path = os.path.join(DESIGNS_DIR, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            created_at = payload.get("created_at") or ""
        except Exception:
            created_at = ""
        out.append({"path": path, "filename": fn, "created_at": created_at})
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


def save_design(design: dict) -> str:
    """Persist a design JSON and two companion CSVs. Returns the JSON path.

    Side-effect: alongside `<base>.json`, also writes
      - `<base>_flows.csv`     — columns: source, source_layer, target, target_layer, share
      - `<base>_products.csv`  — columns: medicine_name, prod_id, share
    """
    os.makedirs(DESIGNS_DIR, exist_ok=True)
    diag = str(design.get("diagnosis", "diagnose"))
    slug = slugify(diag)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    payload = dict(design)
    payload["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    base = f"{slug}__{ts}"
    json_path = os.path.join(DESIGNS_DIR, f"{base}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    # Companion CSVs (Excel-friendly).
    flows_rows = payload.get("flows", []) or []
    flows_df = pd.DataFrame(
        flows_rows,
        columns=["source", "source_layer", "target", "target_layer", "share"],
    )
    flows_df.to_csv(os.path.join(DESIGNS_DIR, f"{base}_flows.csv"), index=False)

    prod_rows: list[dict] = []
    for med, pids in (payload.get("medicine_products", {}) or {}).items():
        for p in pids:
            prod_rows.append({
                "medicine_name": med,
                "prod_id":       str(p.get("prod_id", "")),
                "share":         float(p.get("share", 0) or 0),
            })
    products_df = pd.DataFrame(prod_rows, columns=["medicine_name", "prod_id", "share"])
    products_df.to_csv(os.path.join(DESIGNS_DIR, f"{base}_products.csv"), index=False)

    return json_path


def load_design(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def latest_design_for(diag_omschr_euk: str) -> dict | None:
    """Return the most recent saved design dict for this diagnosis, or None."""
    items = list_designs(diag_omschr_euk)
    if not items:
        return None
    try:
        return load_design(items[0]["path"])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Adapters: design dict ↔ DataFrames the Sankey builder understands
# ---------------------------------------------------------------------------

def design_to_flows_df(design: dict) -> pd.DataFrame:
    """
    Translate a design's `flows` list into the column shape that
    `build_sankey_from_csv` expects.

    `share` is a per-source percentage (sum to 100 per source node), so flow
    widths must be propagated from Source = 100 patients through the graph:
    each downstream node's absolute patient count = sum of its incoming
    absolute flows, and each outgoing flow gets `source_pats * share / 100`.
    """
    rows = design.get("flows", []) or []
    cols = [
        "source_label", "source_layer", "target_label", "target_layer",
        "days_treated", "pat_count", "sum_aantal", "heritage",
        "stuks_dag", "gem_aantal_per_pat", "days_per_pat",
    ]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["source_label"]  = df["source"].astype(str)
    df["target_label"]  = df["target"].astype(str)
    df["source_layer"]  = df["source_layer"].astype(int)
    df["target_layer"]  = df["target_layer"].astype(int)
    df["share"]         = df["share"].astype(float)

    # Propagate absolute patient counts: every layer-0 source starts at 100.
    node_pats: dict[tuple[str, int], float] = {}
    for src, lay in df.loc[df["source_layer"] == 0, ["source_label", "source_layer"]].drop_duplicates().itertuples(index=False):
        node_pats[(str(src), int(lay))] = 100.0

    df = df.sort_values(["source_layer", "target_layer"], kind="stable").reset_index(drop=True)
    pat_counts: list[float] = []
    for _, r in df.iterrows():
        src_key = (r["source_label"], int(r["source_layer"]))
        tgt_key = (r["target_label"], int(r["target_layer"]))
        src_pats = node_pats.get(src_key, 0.0)
        flow_pats = src_pats * float(r["share"]) / 100.0
        pat_counts.append(flow_pats)
        node_pats[tgt_key] = node_pats.get(tgt_key, 0.0) + flow_pats

    df["pat_count"]    = pat_counts
    df["days_treated"] = pat_counts
    df["sum_aantal"]   = 0.0
    df["heritage"]     = df["target_label"]
    df["stuks_dag"]    = 1.0
    df["gem_aantal_per_pat"] = 0.0
    df["days_per_pat"] = 0.0
    return df[cols]


# ---------------------------------------------------------------------------
# Pre-fill helpers (used when the Ontwerp page first opens for a diagnosis)
# ---------------------------------------------------------------------------

def _round_shares_to_100(values: list[float]) -> list[float]:
    """Round to 1 decimal and adjust the largest entry so the sum is exactly 100."""
    if not values:
        return values
    rounded = [round(v, 1) for v in values]
    diff = round(100.0 - sum(rounded), 1)
    if diff != 0:
        idx = max(range(len(rounded)), key=lambda i: rounded[i])
        rounded[idx] = round(rounded[idx] + diff, 1)
    return rounded


def prefill_from_data_sankey(
    data_flows: pd.DataFrame,
    pat_in_window: pd.DataFrame,
    diag_omschr_euk: str,
) -> dict:
    """
    Build an initial design from the current data Sankey at medicine_name level.

    `data_flows`     – output of aggregate_flows(load_sankey_csv(edited=True),
                       level="medicine_name") filtered to this diagnosis.
    `pat_in_window`  – patient records for this diagnosis in the active window
                       (used to derive the default prod_id mix per medicine).
    """
    flows: list[dict] = []
    diag_id_euk = ""
    if not data_flows.empty:
        ids = data_flows.get("diag_id_euk", pd.Series(dtype=str))
        ids = ids.dropna().astype(str).replace("", pd.NA).dropna().unique() if len(ids) else []
        if len(ids) > 0:
            diag_id_euk = str(ids[0])

        for (src, src_lay), grp in data_flows.groupby(
            ["source_label", "source_layer"], sort=False
        ):
            total = float(grp["days_treated"].sum())
            if total <= 0:
                continue
            shares = _round_shares_to_100(
                [float(d) / total * 100.0 for d in grp["days_treated"]]
            )
            for (_, row), share in zip(grp.iterrows(), shares):
                flows.append({
                    "source":       str(src),
                    "source_layer": int(src_lay),
                    "target":       str(row["target_label"]),
                    "target_layer": int(row["target_layer"]),
                    "share":        float(share),
                })

    medicine_products: dict[str, list[dict]] = {}
    target_meds = sorted({f["target"] for f in flows})
    if not pat_in_window.empty and "Med_nm" in pat_in_window.columns:
        for med in target_meds:
            sub = pat_in_window[pat_in_window["Med_nm"].astype(str) == med]
            if sub.empty or "Prod_ID" not in sub.columns:
                continue
            tot = (
                sub.groupby("Prod_ID")["Aantal"]
                .sum()
                .reset_index()
                .rename(columns={"Prod_ID": "prod_id", "Aantal": "aantal"})
            )
            tot["prod_id"] = tot["prod_id"].apply(
                lambda v: str(int(float(str(v)))) if str(v).strip() not in ("", "nan") else ""
            )
            tot = tot[tot["prod_id"] != ""]
            if tot.empty:
                continue
            total_aantal = float(tot["aantal"].sum())
            if total_aantal <= 0:
                continue
            shares = _round_shares_to_100(
                [float(a) / total_aantal * 100.0 for a in tot["aantal"]]
            )
            medicine_products[med] = [
                {"prod_id": pid, "share": s}
                for pid, s in zip(tot["prod_id"].tolist(), shares)
            ]

    return {
        "diagnosis":         diag_omschr_euk,
        "diag_id_euk":       diag_id_euk,
        "flows":             flows,
        "medicine_products": medicine_products,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_design(design: dict) -> list[str]:
    """Return a list of human-readable validation errors. Empty list = valid."""
    errors: list[str] = []
    flows = design.get("flows", []) or []
    if not flows:
        errors.append("Geen flows gedefinieerd.")
        return errors

    # Per-source share sums
    by_src: dict[tuple, float] = {}
    for f in flows:
        key = (str(f.get("source", "")), int(f.get("source_layer", 0)))
        by_src[key] = by_src.get(key, 0.0) + float(f.get("share", 0) or 0)
    for (src, lay), total in by_src.items():
        if abs(total - 100.0) > 0.5:
            errors.append(
                f"Aandelen vanuit '{src}' (laag {lay}) sommeren naar {total:.1f}% (moet 100% zijn)."
            )

    # medicine_products: every target medicine must have ≥1 prod_id, weights sum to 100
    targets = sorted({str(f.get("target", "")) for f in flows})
    mp = design.get("medicine_products", {}) or {}
    for med in targets:
        pids = mp.get(med, [])
        if not pids:
            errors.append(f"Geen Prod_ID gekozen voor medicijn '{med}'.")
            continue
        total = sum(float(p.get("share", 0) or 0) for p in pids)
        if abs(total - 100.0) > 0.5:
            errors.append(
                f"Prod_ID-aandelen voor '{med}' sommeren naar {total:.1f}% (moet 100% zijn)."
            )
    return errors


# ---------------------------------------------------------------------------
# Weighted per-day rates per medicine_name
# ---------------------------------------------------------------------------

def design_medicine_rates(
    design: dict,
    catalogue_df: pd.DataFrame,
) -> dict[str, dict[str, float]]:
    """
    For every medicine_name in design['medicine_products'], compute the
    weighted prijs_dag / vergoeding_dag / margin_dag using the prod_id
    weights from the design and per-prod_id rates from the catalogue
    (already filtered or filterable by diagnosis upstream).

    Returns: {med_name: {"prijs_dag": x, "vergoeding_dag": y, "margin_dag": z}}
    """
    out: dict[str, dict[str, float]] = {}
    if catalogue_df.empty:
        return out

    cat = catalogue_df.copy()
    cat["prod_id"] = cat["prod_id"].astype(str).str.strip()
    diag_id = str(design.get("diag_id_euk", "") or "").strip()
    if diag_id and "diag_id_euk" in cat.columns:
        cat_diag = cat[cat["diag_id_euk"].astype(str).str.strip() == diag_id]
        if not cat_diag.empty:
            cat = cat_diag
    cat_by_id = cat.drop_duplicates("prod_id").set_index("prod_id")

    def _safe(v) -> float:
        try:
            x = float(v)
            return 0.0 if x != x else x
        except Exception:
            return 0.0

    for med, pids in (design.get("medicine_products", {}) or {}).items():
        total_w = sum(float(p.get("share", 0) or 0) for p in pids)
        if total_w <= 0:
            continue
        prijs = verg = marge = 0.0
        for p in pids:
            pid = str(p.get("prod_id", "")).strip()
            w   = float(p.get("share", 0) or 0) / total_w
            if pid not in cat_by_id.index:
                continue
            row = cat_by_id.loc[pid]
            prijs += w * _safe(row.get("prijs_dag",      0))
            verg  += w * _safe(row.get("vergoeding_dag", 0))
            marge += w * _safe(row.get("margin_dag",     0))
        out[med] = {"prijs_dag": prijs, "vergoeding_dag": verg, "margin_dag": marge}
    return out


# ---------------------------------------------------------------------------
# Whole-design totals (KPI strip on the Ontwerp page)
# ---------------------------------------------------------------------------

def compute_design_totals(
    design: dict,
    catalogue_df: pd.DataFrame,
    n_patients: int = 100,
) -> dict[str, float]:
    """
    Return totals per dag for the entire design at `n_patients` patients:
      {"vergoeding": ..., "uitgaven": ..., "marge": ...}

    Uses `design_medicine_rates` for the prod_id-weighted per-medicine daily
    rates, then weights by each flow's share (% of patients reaching that
    medicine from a layer-0 source).
    """
    rates = design_medicine_rates(design, catalogue_df)
    totals = {"vergoeding": 0.0, "uitgaven": 0.0, "marge": 0.0}
    for f in design.get("flows", []) or []:
        # Only count flows starting from a layer-0 source — those describe the
        # share of all `n_patients`. Downstream layers describe transitions
        # within already-counted patients (would double-count their daily cost).
        if int(f.get("source_layer", 0)) != 0:
            continue
        med = str(f.get("target", ""))
        r = rates.get(med)
        if r is None:
            continue
        n_pat = float(n_patients) * (float(f.get("share", 0) or 0) / 100.0)
        totals["uitgaven"]   += n_pat * r["prijs_dag"]
        totals["vergoeding"] += n_pat * r["vergoeding_dag"]
        totals["marge"]      += n_pat * r["margin_dag"]
    return totals


# ---------------------------------------------------------------------------
# Structured diff between two designs (for the "Wijzigingen" expander)
# ---------------------------------------------------------------------------

_DIFF_EPS = 0.05  # percentage points — tighter and rounding noise pollutes


def _flow_share_map(design: dict) -> dict[tuple[str, int, str, int], float]:
    out: dict[tuple[str, int, str, int], float] = {}
    for f in design.get("flows", []) or []:
        key = (
            str(f.get("source", "")),
            int(f.get("source_layer", 0)),
            str(f.get("target", "")),
            int(f.get("target_layer", 1)),
        )
        out[key] = float(f.get("share", 0) or 0)
    return out


def _product_share_map(design: dict, med: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in (design.get("medicine_products", {}) or {}).get(med, []) or []:
        out[str(p.get("prod_id", ""))] = float(p.get("share", 0) or 0)
    return out


def diff_designs(baseline: dict | None, current: dict) -> dict:
    """
    Compare two design dicts at medicine-flow level and per-medicine
    prod_id-mix level. Returns a structured diff (see plan for shape).
    """
    empty = {
        "flow_changes":      [],
        "added_medicines":   [],
        "removed_medicines": [],
        "product_changes":   {},
        "any_change":        False,
    }
    if baseline is None:
        return empty

    base_flows = _flow_share_map(baseline)
    curr_flows = _flow_share_map(current)

    flow_changes: list[dict] = []
    added_meds: set[str] = set()
    removed_meds: set[str] = set()

    all_keys = set(base_flows) | set(curr_flows)
    for key in all_keys:
        old = base_flows.get(key, 0.0)
        new = curr_flows.get(key, 0.0)
        delta = new - old
        if abs(delta) <= _DIFF_EPS:
            continue
        med = key[2]
        flow_changes.append({
            "medicine":  med,
            "source":    key[0],
            "old_share": old,
            "new_share": new,
            "delta":     delta,
        })
        if key not in base_flows:
            added_meds.add(med)
        elif key not in curr_flows:
            removed_meds.add(med)

    flow_changes.sort(key=lambda r: -abs(r["delta"]))

    # Prod_id mix changes per medicine (any medicine appearing in either side)
    prod_changes: dict[str, list[dict]] = {}
    base_meds = set((baseline.get("medicine_products") or {}).keys())
    curr_meds = set((current.get("medicine_products")  or {}).keys())
    for med in base_meds | curr_meds:
        old_map = _product_share_map(baseline, med)
        new_map = _product_share_map(current,  med)
        rows: list[dict] = []
        for pid in set(old_map) | set(new_map):
            old = old_map.get(pid, 0.0)
            new = new_map.get(pid, 0.0)
            delta = new - old
            if abs(delta) <= _DIFF_EPS:
                continue
            rows.append({
                "prod_id":   pid,
                "old_share": old,
                "new_share": new,
                "delta":     delta,
            })
        if rows:
            rows.sort(key=lambda r: -abs(r["delta"]))
            prod_changes[med] = rows

    any_change = bool(flow_changes) or bool(prod_changes)
    return {
        "flow_changes":      flow_changes,
        "added_medicines":   sorted(added_meds),
        "removed_medicines": sorted(removed_meds),
        "product_changes":   prod_changes,
        "any_change":        any_change,
    }
