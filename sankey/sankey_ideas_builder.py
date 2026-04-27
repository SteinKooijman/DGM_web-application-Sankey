"""
sankey_ideas_builder.py
-----------------------
Builds a Plotly Sankey figure from an explicit flows DataFrame
(source_nm, target_nm, value, heritage, link_id) so that flow values
can be adjusted interactively without re-running the full patient-journey pipeline.

Also extracts the flows DataFrame from the standard sankey_functions pipeline
so the two systems stay in sync.
"""

from __future__ import annotations

import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go

from sankey.sankey_functions import (
    _apply_diag_voorschrijver_time_filter,
    _build_df_grouped,
    _build_labels,
    _build_transitions,
    _add_source_links,
    _build_unique_pat_diag,
    _generate_node_notes,
    _generate_link_notes,
    _sankey_positions,
    Cols,
)


# ---------------------------------------------------------------------------
# Extract aggregated flows from patient journey data
# ---------------------------------------------------------------------------

def build_flows_from_patient_data(
    df: pd.DataFrame,
    start_dat,
    end_dat,
    diag_omschr_euk: str,
    voorschrijver_nm: str,
    analyse_col: str,
) -> tuple[pd.DataFrame, list[str], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Run the standard sankey pipeline and return:
      flows_df   : DataFrame(source_nm, target_nm, value, heritage, link_id)
      labels     : list of node labels
      df_filtered: patient-filtered DataFrame
      df_grouped : patient × medicine grouped DataFrame
      df_result  : label summary (analyse_col, Zorgpad_nr, Aantal_Pat)
    """
    df_filtered = _apply_diag_voorschrijver_time_filter(
        df, analyse_col, start_dat, end_dat, diag_omschr_euk, voorschrijver_nm
    )
    df_grouped = _build_df_grouped(df_filtered, analyse_col)
    labels, label_to_idx, df_result = _build_labels(df_grouped, analyse_col)
    unique_pat_diag = _build_unique_pat_diag(df_grouped, analyse_col)
    trans = _build_transitions(unique_pat_diag, labels, label_to_idx)
    src = _add_source_links(df_result, labels, label_to_idx, analyse_col)

    links_df = pd.concat([trans, src], ignore_index=True)
    if links_df.empty:
        flows_df = pd.DataFrame(columns=["source_nm", "target_nm", "value", "heritage", "link_id"])
    else:
        flows_df = pd.DataFrame({
            "source_nm": links_df[Cols.ORIGIN_NM].values,
            "target_nm": links_df[Cols.DEST_NM].values,
            "value": links_df[Cols.NUMBER].astype(float).values,
            "heritage": links_df[Cols.HERITAGE].values,
        })
        flows_df["link_id"] = flows_df["source_nm"] + "||" + flows_df["target_nm"]

    return flows_df, labels, df_filtered, df_grouped, df_result


# ---------------------------------------------------------------------------
# Build Plotly figure from flows DataFrame
# ---------------------------------------------------------------------------

def build_sankey_figure(
    flows_df: pd.DataFrame,
    labels: list[str],
    df_filtered: pd.DataFrame,
    df_result: pd.DataFrame,
    df_grouped: pd.DataFrame,
    analyse_col: str,
    diag_omschr_euk: str,
    no_points_nodes: int,
    no_points_links: int,
    notitie_column: str | None,
    selected_node: str | None = None,
    selected_link: tuple[str, str] | None = None,
) -> go.Figure:
    """
    Build a Plotly Sankey from an (optionally adjusted) flows DataFrame.
    selected_node and selected_link drive visual highlighting.
    """
    if flows_df.empty or not labels:
        fig = go.Figure()
        fig.update_layout(title_text="Geen data beschikbaar", height=300)
        return fig

    label_to_idx = {lab: i for i, lab in enumerate(labels)}

    sources = []
    targets = []
    values = []
    ancestry = []
    link_ids = []

    for _, row in flows_df.iterrows():
        s = label_to_idx.get(row["source_nm"])
        t = label_to_idx.get(row["target_nm"])
        if s is None or t is None:
            continue
        sources.append(s)
        targets.append(t)
        values.append(max(0.001, float(row["value"])))
        ancestry.append(str(row.get("heritage", "nvt")))
        link_ids.append(str(row.get("link_id", f"{row['source_nm']}||{row['target_nm']}")))

    # ── node colors ────────────────────────────────────────────────────────
    def base_key(s: str) -> str:
        return s.split(" ", 1)[0].strip().lower()

    palette = pc.qualitative.Plotly
    unique_bases = []
    for lbl in labels:
        bk = base_key(lbl)
        if bk not in ("source", "nvt") and bk not in unique_bases:
            unique_bases.append(bk)
    base_to_hex = {bk: palette[i % len(palette)] for i, bk in enumerate(unique_bases)}

    def to_rgba(hex_or_rgba: str, alpha: float = 0.7) -> str:
        if isinstance(hex_or_rgba, str) and hex_or_rgba.startswith("#"):
            h = hex_or_rgba.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r},{g},{b},{alpha})"
        return hex_or_rgba

    NVG = "rgba(200,200,200,0.5)"
    HIGHLIGHT_NODE = "rgba(255,165,0,0.9)"
    HIGHLIGHT_LINK = "rgba(255,140,0,0.85)"

    node_colors = []
    for lbl in labels:
        bk = base_key(lbl)
        if lbl == selected_node:
            node_colors.append(HIGHLIGHT_NODE)
        elif bk == "source":
            node_colors.append("rgba(210,210,210,0.6)")
        else:
            node_colors.append(to_rgba(base_to_hex.get(bk, "#999999"), 0.7))

    # ── link colors ─────────────────────────────────────────────────────────
    link_colors = []
    for lid, anc in zip(link_ids, ancestry):
        is_sel_link = (
            selected_link is not None
            and lid == f"{selected_link[0]}||{selected_link[1]}"
        )
        if is_sel_link:
            link_colors.append(HIGHLIGHT_LINK)
        elif anc.lower() == "nvt":
            link_colors.append(NVG)
        else:
            link_colors.append(to_rgba(base_to_hex.get(base_key(anc), "#999999"), 0.45))

    # ── node hover ──────────────────────────────────────────────────────────
    n = len(labels)
    incoming_tot = [0.0] * n
    outgoing_tot = [0.0] * n
    for s, t, v in zip(sources, targets, values):
        outgoing_tot[s] += v
        incoming_tot[t] += v
    stopped = [max(incoming_tot[i] - outgoing_tot[i], 0.0) for i in range(n)]

    if notitie_column and notitie_column in df_filtered.columns:
        unique_pat_diag = _build_unique_pat_diag(df_grouped, analyse_col)
        node_notes = _generate_node_notes(df_filtered, unique_pat_diag, labels, no_points_nodes, notitie_column)
    else:
        node_notes = {}

    node_hover = [
        (
            f"<b>{lbl}</b><br>"
            f"{node_notes.get(lbl, '')}<br>"
            f"Incoming: {incoming_tot[i]:.0f}<br>"
            f"Outgoing: {outgoing_tot[i]:.0f}<br>"
            f"Stopped: {stopped[i]:.0f}<br>"
            "<i>Klik om alternatieven te zien</i>"
        )
        for i, lbl in enumerate(labels)
    ]

    # ── link customdata = [link_id, hover_note] ─────────────────────────────
    link_hover_notes = [f"Flow: {lid.replace('||', ' → ')}" for lid in link_ids]

    link_custom = [[lid, note] for lid, note in zip(link_ids, link_hover_notes)]

    # ── node positions ────────────────────────────────────────────────────
    if not df_result.empty:
        max_k = int(df_result[Cols.ZORGPAD_NR].max())
        counts_per_layer = (
            df_result.groupby(Cols.ZORGPAD_NR)[analyse_col]
            .nunique()
            .reindex(range(1, max_k + 1), fill_value=0)
            .tolist()
        )
        counts_list = [1] + counts_per_layer
    else:
        counts_list = [1]

    import numpy as np
    x_pos, y_pos = _sankey_positions(counts_list, x_margin=0.04, y_margin=0.06)

    # Pad or trim to match label count
    while len(x_pos) < n:
        x_pos.append(0.5)
        y_pos.append(0.5)
    x_pos, y_pos = x_pos[:n], y_pos[:n]

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
                "<b>%{customdata[0]}</b><br>"
                "Patiënten: %{value:.0f}<br>"
                "<i>Klik om flow aan te passen</i><extra></extra>"
            ),
        ),
    ))

    x_rounded = [round(xv, 2) for xv in x_pos]
    unique_layers = sorted(set(x_rounded))
    nodes_per_layer = [x_rounded.count(xl) for xl in unique_layers]
    max_nodes = max(nodes_per_layer) if nodes_per_layer else 1
    height_diagram = float(max(450, 900 - 12.5 * max_nodes))

    fig.update_layout(
        title_text=f"Patiëntenstroom – {diag_omschr_euk}",
        font=dict(size=12, family="Inter, sans-serif"),
        width=None,
        height=int(height_diagram),
        autosize=True,
        margin=dict(l=60, r=80, t=60, b=80),
        paper_bgcolor="#F6F8FA",
    )
    fig.update_traces(node=dict(thickness=14, pad=14))

    return fig
