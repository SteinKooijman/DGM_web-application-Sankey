"""
sankey_from_csv.py
------------------
Two public functions:

  aggregate_flows(flows_df, level) → flows_df with source_label / target_label columns
  build_sankey_from_csv(flows_df, ...) → go.Figure

The CSV is always at prod_id grain. `aggregate_flows` collapses those rows to the
chosen display level (prod_id | prod_nm | medicine_name | group_name) by summing
days_treated and pat_count.  `build_sankey_from_csv` then renders from the labels
that aggregate_flows produces.
"""

from __future__ import annotations

import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go

from backend.csv_builder import LEVEL_COLS, load_prod_omschr_lookup
from sankey.sankey_functions import _sankey_positions


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_flows(flows_df: pd.DataFrame, level: str) -> pd.DataFrame:
    """
    Set source_label / target_label on a flows DataFrame that is already at the
    requested grain (one CSV per grain — see backend/csv_builder.py).

    Parameters
    ----------
    flows_df : rows from sankey_edited_<grain>.csv / sankey_original_<grain>.csv
               for one diagnosis. Already at the requested grain.
    level    : "prod_id" | "prod_nm" | "medicine_name" | "group_name"

    For prod_id grain, the label is "Prod_omschr (prod_id)" so users see a
    descriptive name in the diagram. For coarser grains the label IS the value
    in the corresponding canonical column.

    Heritage is taken as the most-common value within each merged group (groups
    only collapse heritage variants of the same flow — flows themselves are
    already at the right grain).
    """
    df = flows_df.copy()

    # Virtual Source node: source_layer == 0 and source label columns are blank.
    is_source = df["source_layer"].astype(int) == 0

    if level == "prod_id":
        omschr_map = load_prod_omschr_lookup()

        def _pid_label(pid: str, nm: str) -> str:
            pid = str(pid).strip()
            desc = omschr_map.get(pid, "") or str(nm).strip()
            return f"{desc} ({pid})" if desc else pid

        df["source_label"] = df.apply(
            lambda r: _pid_label(str(r["source_prod_id"]), str(r.get("source_prod_nm", ""))),
            axis=1,
        )
        df.loc[is_source, "source_label"] = "Source"
        df["target_label"] = df.apply(
            lambda r: _pid_label(str(r["target_prod_id"]), str(r.get("target_prod_nm", ""))),
            axis=1,
        )
    else:
        src_col, tgt_col = LEVEL_COLS.get(level, ("source_prod_nm", "target_prod_nm"))
        df["source_label"] = df[src_col].astype(str).str.strip()
        df.loc[is_source, "source_label"] = "Source"
        df["target_label"] = df[tgt_col].astype(str).str.strip()

    agg = (
        df.groupby(
            ["source_label", "source_layer", "target_label", "target_layer"],
            dropna=False,
            sort=False,
        )
        .agg(
            days_treated=("days_treated", "sum"),
            pat_count=("pat_count",    "sum"),
            sum_aantal=("sum_aantal",   "sum"),
            heritage=("heritage", lambda s: s.mode().iloc[0] if not s.empty else "nvt"),
        )
        .reset_index()
    )

    # Recompute per-patient ratios and effective stuks_dag after the heritage
    # roll-up. (No self-loops are possible here: flows are stored at grain.)
    pc  = agg["pat_count"].replace(0, float("nan"))
    dt  = agg["days_treated"].replace(0, float("nan"))
    agg["gem_aantal_per_pat"] = (agg["sum_aantal"] / pc).round(2).fillna(0)
    agg["days_per_pat"]       = (agg["days_treated"] / pc).round(2).fillna(0)
    agg["stuks_dag"]          = (agg["sum_aantal"] / dt).round(6).fillna(1.0)
    return agg


# ---------------------------------------------------------------------------
# Figure builder
# ---------------------------------------------------------------------------

def build_sankey_from_csv(
    flows_df: pd.DataFrame,
    level: str = "prod_nm",
    selected_node: str | None = None,
    selected_link: tuple | None = None,
    diag_omschr_euk: str = "",
) -> go.Figure:
    """
    Build an interactive Plotly Sankey from an aggregated flows DataFrame.

    Parameters
    ----------
    flows_df       : output of aggregate_flows() — must have source_label / target_label
    level          : aggregation level used (encoded into link_id for click detection)
    selected_node  : label of the highlighted node (orange)
    selected_link  : (source_label, source_layer, target_label, target_layer) tuple
    diag_omschr_euk: used only for the figure title
    """
    if flows_df.empty or "source_label" not in flows_df.columns:
        fig = go.Figure()
        fig.update_layout(title_text="Geen flows beschikbaar", height=300)
        return fig

    # ── Build ordered node list: (label, layer) tuples ──────────────────────
    seen: set = set()
    nodes: list[tuple[str, int]] = []
    for _, row in flows_df.iterrows():
        for lbl_col, lay_col in [("source_label", "source_layer"),
                                  ("target_label", "target_layer")]:
            k = (str(row[lbl_col]), int(row[lay_col]))
            if k not in seen:
                seen.add(k)
                nodes.append(k)

    nodes.sort(key=lambda n: (n[1], n[0]))
    node_to_idx = {n: i for i, n in enumerate(nodes)}
    labels = [n[0] for n in nodes]
    n_nodes = len(nodes)

    # ── Node positions ───────────────────────────────────────────────────────
    max_layer = max(n[1] for n in nodes)
    counts_per_layer = [sum(1 for n in nodes if n[1] == li)
                        for li in range(max_layer + 1)]
    x_pos, y_pos = _sankey_positions(counts_per_layer, x_margin=0.04, y_margin=0.10)
    while len(x_pos) < n_nodes:
        x_pos.append(0.5)
        y_pos.append(0.5)
    x_pos, y_pos = x_pos[:n_nodes], y_pos[:n_nodes]

    # ── Color palette ────────────────────────────────────────────────────────
    palette = pc.qualitative.Plotly

    unique_bases: list[str] = []
    for lbl, _ in nodes:
        bk = lbl.split(" ", 1)[0].strip().lower()
        if bk not in ("source", "nvt", "") and bk not in unique_bases:
            unique_bases.append(bk)
    base_to_hex: dict[str, str] = {
        bk: palette[i % len(palette)] for i, bk in enumerate(unique_bases)
    }

    def to_rgba(hex_col: str, alpha: float = 0.7) -> str:
        h = hex_col.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"

    HIGHLIGHT_NODE = "rgba(255,165,0,0.9)"

    node_colors = []
    for lbl, _layer in nodes:
        bk = lbl.split(" ", 1)[0].strip().lower()
        if selected_node and lbl == selected_node:
            node_colors.append(HIGHLIGHT_NODE)
        elif bk == "source":
            node_colors.append("rgba(210,210,210,0.6)")
        else:
            node_colors.append(to_rgba(base_to_hex.get(bk, "#999999"), 0.7))

    # ── Build links ──────────────────────────────────────────────────────────
    # Flow thickness is now `pat_count` so the diagram conserves: every patient
    # is counted exactly once at every transition. `days_treated` is kept as a
    # secondary metric on the link tooltip — but note it represents the days the
    # SOURCE product was used, not the target's. The right-side bar chart shows
    # ground-truth-correct total behandeldagen per product.
    sources:    list[int]   = []
    targets:    list[int]   = []
    values:     list[float] = []   # = pat_count (flow width)
    days_list:  list[float] = []   # = days_treated for tooltip
    pat_counts: list[float] = []   # mirrors `values`, kept for hover sums
    ancestries: list[str]   = []
    link_ids:   list[str]   = []

    for _, row in flows_df.iterrows():
        src_key = (str(row["source_label"]), int(row["source_layer"]))
        tgt_key = (str(row["target_label"]), int(row["target_layer"]))
        s = node_to_idx.get(src_key)
        t = node_to_idx.get(tgt_key)
        if s is None or t is None:
            continue
        sources.append(s)
        targets.append(t)
        pat_n = float(row.get("pat_count", 0) or 0)
        values.append(max(0.001, pat_n if pat_n > 0 else 0.001))
        days_list.append(float(row.get("days_treated", 0) or 0))
        pat_counts.append(pat_n)
        ancestries.append(str(row.get("heritage", "nvt")))
        # Encode level in link_id so the click handler knows which column to match
        link_ids.append(
            f"{row['source_label']}||{int(row['source_layer'])}||"
            f"{row['target_label']}||{int(row['target_layer'])}||{level}"
        )

    NVG = "rgba(200,200,200,0.5)"
    HIGHLIGHT_LINK = "rgba(255,140,0,0.85)"

    sel_lid: str | None = None
    if selected_link is not None:
        src_lbl, src_lay, tgt_lbl, tgt_lay = selected_link
        sel_lid = f"{src_lbl}||{int(src_lay)}||{tgt_lbl}||{int(tgt_lay)}||{level}"

    link_colors = []
    for lid, anc in zip(link_ids, ancestries):
        if sel_lid and lid == sel_lid:
            link_colors.append(HIGHLIGHT_LINK)
        elif anc.lower() in ("nvt", ""):
            link_colors.append(NVG)
        else:
            bk = anc.split(" ", 1)[0].strip().lower()
            link_colors.append(to_rgba(base_to_hex.get(bk, "#999999"), 0.45))

    # ── Node hover ───────────────────────────────────────────────────────────
    # Patient counts conserve through the diagram (every patient counted once
    # at every transition), so the in/out sums here are meaningful.  Total
    # behandeldagen per product is shown separately in the side bar chart, since
    # flow widths can't represent that without double-counting (see app layout).
    node_hover = []
    for i, (lbl, layer) in enumerate(nodes):
        in_pats  = sum(p for s, t, p in zip(sources, targets, pat_counts) if t == i)
        out_pats = sum(p for s, t, p in zip(sources, targets, pat_counts) if s == i)
        node_hover.append(
            f"<b>{lbl}</b> (stap {layer})<br>"
            f"Patiënten in: {in_pats:,.0f} &nbsp;|&nbsp; Patiënten uit: {out_pats:,.0f}<br>"
            "<i>Zie grafiek rechts voor totaal behandeldagen.</i><br>"
            "<i>Klik om alternatieven te zien.</i>"
        )

    # Link customdata: [link_id, source_days_label] — keep lid at index 0 for the
    # click handler (parses '||'-encoded id), days at index 1 for the tooltip.
    link_custom = [
        [lid, f"{d:,.1f}"]
        for lid, d in zip(link_ids, days_list)
    ]

    # ── Figure ───────────────────────────────────────────────────────────────
    max_nodes_in_layer = max(counts_per_layer) if counts_per_layer else 1
    height = int(max(450, 900 - 12.5 * max_nodes_in_layer))
    title = f"Patiëntenstroom – {diag_omschr_euk}" if diag_omschr_euk else "Patiëntenstroom"

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            label=labels,
            color=node_colors,
            line=dict(width=0.5, color="black"),
            x=x_pos,
            y=y_pos,
            customdata=node_hover,
            hovertemplate="%{customdata}<extra></extra>",
        ),
        link=dict(
            source=sources,
            target=targets,
            value=values,
            color=link_colors,
            customdata=link_custom,
            hovertemplate=(
                "Patiënten: %{value:,.0f}<br>"
                "Behandeldagen (op bron-product): %{customdata[1]}<br>"
                "<i>Klik om flow aan te passen</i><extra></extra>"
            ),
        ),
    ))
    fig.update_traces(node=dict(thickness=14, pad=14), domain=dict(y=[0.05, 0.95]))
    fig.update_layout(
        title_text=title,
        font=dict(size=12, family="Inter, sans-serif"),
        height=height,
        autosize=True,
        margin=dict(l=60, r=80, t=40, b=60),
        paper_bgcolor="#F6F8FA",
    )
    return fig
