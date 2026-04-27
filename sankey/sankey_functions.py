"""
sankey_functions.py
-------------------
Adapted from 3_application/modules/sankey_functions.py.
All UploadedFiles static-class references have been removed.
Functions now accept df and explicit parameters directly.
"""

import pandas as pd
import plotly.graph_objects as go
import plotly.colors as pc
import numpy as np
from enum import Enum


class Cols(str, Enum):
    PAT_ID       = "Pat_id"
    DIAG         = "Diag_omschr_EUK"
    DATUM        = "Datum"
    AANTAL       = "Aantal"
    ZORGPAD_NR   = "Zorgpad_nr"
    ORIGIN       = "origin"
    DESTINATION  = "destination"
    NUMBER       = "number"
    HERITAGE     = "heritage"
    ORIGIN_NM    = "origin_nm"
    DEST_NM      = "destination_nm"
    AANTAL_PAT   = "Aantal_Pat"
    LAYER_PREFIX = "Layer_"
    SOURCE_NM    = "Source"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_sankey_diagram(
    df: pd.DataFrame,
    start_dat,
    end_dat,
    diag_omschr_euk: str,
    voorschrijver_nm: str,
    no_points_nodes: int,
    no_points_links: int,
    analyse_col: str,
    notitie_column: str,
) -> go.Figure:
    """
    Build and return a Plotly Sankey figure from patient-journey data.

    Parameters
    ----------
    df               : patient journey DataFrame (Pat_id, Datum, Diag_omschr_EUK, ...)
    start_dat        : start date (string or datetime)
    end_dat          : end date   (string or datetime)
    diag_omschr_euk  : diagnosis to filter on
    voorschrijver_nm : prescriber to filter on ('Alle voorschrijvers' = no filter)
    no_points_nodes  : top-N categories to show in node hover
    no_points_links  : top-N categories to show in link hover
    analyse_col      : column to use as Sankey node label (e.g. 'Med_nm', 'Prod_nm')
    notitie_column   : column whose values are shown as breakdown in hover notes
    """
    labels, x, y, df_filtered, df_grouped, df_result = _generate_nodes(
        df, analyse_col, start_dat, end_dat, diag_omschr_euk, voorschrijver_nm
    )
    links, unique_pat_diag = _generate_connections(
        df_filtered, labels, analyse_col, df_grouped=df_grouped, df_result=df_result
    )
    node_notes = _generate_node_notes(
        df_filtered, unique_pat_diag, labels, no_points_nodes, notitie_column
    )
    link_notes, df_links = _generate_link_notes(
        df_filtered, unique_pat_diag, labels, links, analyse_col, no_points_links, notitie_column
    )

    sources  = [s for s, t, v, anc in links]
    targets  = [t for s, t, v, anc in links]
    values   = [v for s, t, v, anc in links]
    ancestry = [anc for s, t, v, anc in links]

    n = len(labels)
    incoming_tot = [0.0] * n
    outgoing_tot = [0.0] * n
    for s, t, v, anc in links:
        outgoing_tot[s] += v
        incoming_tot[t] += v
    stopped_at_node = [max(incoming_tot[i] - outgoing_tot[i], 0.0) for i in range(n)]

    node_hover = [
        f"<b>{lbl}</b><br>"
        f"{node_notes.get(lbl, 'No note')}<br>"
        f"Incoming: {incoming_tot[i]}<br>"
        f"Outgoing: {outgoing_tot[i]}<br>"
        f"Stopped at node: {stopped_at_node[i]}"
        for i, lbl in enumerate(labels)
    ]

    def base_key(s):
        if not isinstance(s, str):
            return str(s)
        return s.split(" ", 1)[0].strip().lower()

    palette = pc.qualitative.Plotly
    unique_bases = []
    for lbl in labels:
        bk = base_key(lbl)
        if bk not in ("source", "nvt") and bk not in unique_bases:
            unique_bases.append(bk)

    base_to_hex = {bk: palette[i % len(palette)] for i, bk in enumerate(unique_bases)}
    NVG = "rgba(200,200,200,0.5)"
    base_to_hex["nvt"] = NVG

    def to_rgba(hex_or_rgba, alpha=0.7):
        if isinstance(hex_or_rgba, str) and hex_or_rgba.startswith("#"):
            h = hex_or_rgba.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r},{g},{b},{alpha})"
        return hex_or_rgba

    node_colors = []
    for lbl in labels:
        bk = base_key(lbl)
        if bk == "source":
            node_colors.append("rgba(210,210,210,0.6)")
        else:
            node_colors.append(to_rgba(base_to_hex.get(bk, "#999999"), 0.7))

    link_colors = []
    for anc in ancestry:
        bk = base_key(anc)
        if bk == "nvt":
            link_colors.append(NVG)
        else:
            link_colors.append(to_rgba(base_to_hex.get(bk, "#999999"), 0.7))

    link_notes = [str(note).replace("\n", "<br>") for note in link_notes]
    assert len(link_notes) == len(links), "link_notes must align 1:1 with links"
    link_custom = [[anc, note] for anc, note in zip(ancestry, link_notes)]

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            label=labels,
            color=node_colors,
            line=dict(width=0.5, color="black"),
            x=x,
            y=y,
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
                "<b>%{source.label} → %{target.label}</b><br>"
                "Value: %{value}<br>"
                "Heritage: %{customdata[0]}<br>"
                "Note: %{customdata[1]}<extra></extra>"
            ),
        ),
    ))

    x_rounded = np.round(x, 2)
    unique_layers = np.unique(x_rounded)
    nodes_per_layer = [np.sum(x_rounded == layer) for layer in unique_layers]
    max_nodes = max(nodes_per_layer) if nodes_per_layer else 1

    width_diagram = 1500.0
    height_diagram = float(max(400, 900 - 12.5 * max_nodes))

    fig.update_layout(
        title_text=f"Medicine flow diagram – {diag_omschr_euk}",
        font=dict(size=12),
        width=int(width_diagram),
        height=int(height_diagram),
        autosize=False,
        margin=dict(l=120, r=360, t=70, b=200),
    )
    fig.update_traces(domain=dict(y=[0.02, 0.95]), node=dict(thickness=12, pad=12))

    return fig


# ---------------------------------------------------------------------------
# Internal helpers (prefixed with _)
# ---------------------------------------------------------------------------

def _format_label(med, step):
    return f"{med} -{int(step)}e"


def _build_df_grouped(df_filtered: pd.DataFrame, analyse_col: str) -> pd.DataFrame:
    df_grouped = (
        df_filtered
        .groupby([Cols.PAT_ID, analyse_col], as_index=False)
        .agg(Datum=(Cols.DATUM, "min"), Aantal=(Cols.AANTAL, "sum"))
        .sort_values([Cols.PAT_ID, Cols.DATUM], kind="stable")
    )
    df_grouped[Cols.ZORGPAD_NR] = (
        df_grouped
        .groupby([Cols.PAT_ID], sort=False)
        .cumcount() + 1
    )
    return df_grouped


def _build_labels(df_grouped: pd.DataFrame, analyse_col: str):
    df_result = (
        df_grouped
        .groupby([analyse_col, Cols.ZORGPAD_NR])[Cols.PAT_ID]
        .nunique()
        .reset_index(name=Cols.AANTAL_PAT)
        .sort_values([Cols.ZORGPAD_NR, Cols.AANTAL_PAT], ascending=[True, False], kind="stable")
    )
    labels = list(dict.fromkeys(
        _format_label(r[analyse_col], r[Cols.ZORGPAD_NR]) for _, r in df_result.iterrows()
    ))
    labels = ["Source"] + labels
    label_to_idx = {lab: i for i, lab in enumerate(labels)}
    return labels, label_to_idx, df_result


def _build_unique_pat_diag(df_grouped: pd.DataFrame, analyse_col: str) -> pd.DataFrame:
    wide = (
        df_grouped
        .pivot_table(index=[Cols.PAT_ID], columns=Cols.ZORGPAD_NR, values=analyse_col, aggfunc="first")
        .sort_index(axis=1)
    )
    wide_labeled = wide.copy()
    for k in wide.columns:
        wide_labeled[k] = wide[k].where(wide[k].isna(), wide[k].astype(str) + f" -{int(k)}e")
    wide_labeled.columns = [f"Layer_{int(c)}" for c in wide_labeled.columns]
    return wide_labeled.reset_index()


def _build_transitions(unique_pat_diag: pd.DataFrame, labels: list, label_to_idx: dict) -> pd.DataFrame:
    unique_pat_diag = unique_pat_diag.loc[:, ~unique_pat_diag.columns.duplicated()].copy()
    layer_cols = [c for c in unique_pat_diag.columns if c.startswith(Cols.LAYER_PREFIX)]
    if not layer_cols:
        return pd.DataFrame(columns=[Cols.ORIGIN, Cols.DESTINATION, Cols.NUMBER, Cols.HERITAGE, Cols.ORIGIN_NM, Cols.DEST_NM])

    heritage_col = layer_cols[0]
    trans_frames = []
    for i in range(len(layer_cols) - 1):
        a, b = layer_cols[i], layer_cols[i + 1]
        df_pair = pd.DataFrame({
            Cols.PAT_ID: unique_pat_diag[Cols.PAT_ID],
            Cols.ORIGIN_NM: unique_pat_diag[a],
            Cols.DEST_NM: unique_pat_diag[b],
            Cols.HERITAGE: unique_pat_diag[heritage_col],
        })
        df_pair = df_pair.dropna(subset=[Cols.ORIGIN_NM, Cols.DEST_NM])
        if df_pair.empty:
            continue
        df_pair = (
            df_pair
            .groupby([Cols.ORIGIN_NM, Cols.DEST_NM, Cols.HERITAGE], as_index=False)[Cols.PAT_ID]
            .nunique()
            .rename(columns={Cols.PAT_ID: Cols.NUMBER})
        )
        trans_frames.append(df_pair)

    if not trans_frames:
        return pd.DataFrame(columns=[Cols.ORIGIN_NM, Cols.DEST_NM, Cols.HERITAGE, Cols.NUMBER])

    trans = pd.concat(trans_frames, ignore_index=True)
    trans[Cols.ORIGIN] = trans[Cols.ORIGIN_NM].map(label_to_idx)
    trans[Cols.DESTINATION] = trans[Cols.DEST_NM].map(label_to_idx)
    trans = trans.dropna(subset=[Cols.ORIGIN, Cols.DESTINATION]).copy()
    trans[Cols.ORIGIN] = trans[Cols.ORIGIN].astype("int64")
    trans[Cols.DESTINATION] = trans[Cols.DESTINATION].astype("int64")
    return trans[[Cols.ORIGIN, Cols.DESTINATION, Cols.NUMBER, Cols.HERITAGE, Cols.ORIGIN_NM, Cols.DEST_NM]]


def _add_source_links(df_result: pd.DataFrame, labels: list, label_to_idx: dict, analyse_col: str) -> pd.DataFrame:
    first = df_result.loc[df_result[Cols.ZORGPAD_NR] == 1, :].copy()
    if first.empty:
        return pd.DataFrame(columns=[Cols.ORIGIN, Cols.DESTINATION, Cols.NUMBER, Cols.HERITAGE, Cols.ORIGIN_NM, Cols.DEST_NM])
    first[Cols.ORIGIN] = 0
    first[Cols.DEST_NM] = first.apply(lambda r: _format_label(r[analyse_col], r[Cols.ZORGPAD_NR]), axis=1)
    first[Cols.DESTINATION] = first[Cols.DEST_NM].map(label_to_idx)
    first = first.dropna(subset=[Cols.DESTINATION]).copy()
    first[Cols.DESTINATION] = first[Cols.DESTINATION].astype("int64")
    first[Cols.NUMBER] = first[Cols.AANTAL_PAT]
    first[Cols.HERITAGE] = "nvt"
    first[Cols.ORIGIN_NM] = "Source"
    return first[[Cols.ORIGIN, Cols.DESTINATION, Cols.NUMBER, Cols.HERITAGE, Cols.ORIGIN_NM, Cols.DEST_NM]]


def _pat_notitie_column_pairs(df_filtered: pd.DataFrame, notitie_column: str) -> pd.DataFrame:
    return df_filtered[[Cols.PAT_ID, notitie_column]].dropna().drop_duplicates()


def _apply_diag_voorschrijver_time_filter(
    df: pd.DataFrame,
    analyse_col: str,
    start_dat,
    end_dat,
    diag_omschr_euk: str,
    voorschrijver_nm: str,
) -> pd.DataFrame:
    pat_ids_with_diagnosis = df[df[Cols.DIAG] == diag_omschr_euk][Cols.PAT_ID].unique()

    df["Start_Datum_pat"] = df.groupby([Cols.PAT_ID])[Cols.DATUM].transform("min")

    pat_ids_with_time = df[
        (df["Start_Datum_pat"] >= start_dat) & (df["Start_Datum_pat"] <= end_dat)
    ][Cols.PAT_ID].unique()

    if voorschrijver_nm != "Alle voorschrijvers":
        pat_ids_with_voorschrijver = df[df["Voorschrijver_nm"] == voorschrijver_nm][Cols.PAT_ID].unique()
    else:
        pat_ids_with_voorschrijver = df[Cols.PAT_ID].unique()

    pat_ids_final = list(
        set(pat_ids_with_diagnosis) & set(pat_ids_with_time) & set(pat_ids_with_voorschrijver)
    )
    return df[df[Cols.PAT_ID].isin(pat_ids_final)].copy()


def _sankey_positions(layer_counts, x_margin=0.04, y_margin=0.03, first_gap_ratio=0.5):
    assert all(n >= 0 for n in layer_counts)
    total_nodes = sum(layer_counts)
    if total_nodes == 0:
        return [], []

    L = len(layer_counts)
    inner_w = max(1e-9, 1.0 - 2.0 * float(x_margin))
    inner_h = max(1e-9, 1.0 - 2.0 * float(y_margin))

    if L == 1:
        x_layers = [0.5]
    elif L == 2:
        x_layers = [float(x_margin), float(1.0 - x_margin)]
    else:
        first_gap_ratio = max(0.0, float(first_gap_ratio))
        total_units = first_gap_ratio + (L - 2)
        step = inner_w / total_units
        gaps = [first_gap_ratio * step] + [step] * (L - 2)
        x_layers = [float(x_margin)]
        for g in gaps:
            x_layers.append(x_layers[-1] + g)
        x_layers.append(float(1.0 - x_margin))
        x_layers = x_layers[:L]

    x, y = [], []
    for li, count in enumerate(layer_counts):
        xl = x_layers[li]
        if count <= 0:
            continue
        for k in range(count):
            frac = (k + 1.0) / (count + 1.0)
            yk = float(y_margin) + frac * inner_h
            x.append(float(xl))
            y.append(float(yk))

    return x, y


def _generate_nodes(df, analyse_col, start_dat, end_dat, diag_omschr_euk, voorschrijver_nm):
    df_filtered = _apply_diag_voorschrijver_time_filter(
        df, analyse_col, start_dat, end_dat, diag_omschr_euk, voorschrijver_nm
    )
    df_grouped = _build_df_grouped(df_filtered, analyse_col)
    labels, label_to_idx, df_result = _build_labels(df_grouped, analyse_col)

    if df_result.empty:
        counts_list = [1]
    else:
        max_k = int(df_result[Cols.ZORGPAD_NR].max())
        counts_per_layer = (
            df_result.groupby(Cols.ZORGPAD_NR)[analyse_col]
            .nunique()
            .reindex(range(1, max_k + 1), fill_value=0)
            .tolist()
        )
        counts_list = [1] + counts_per_layer

    x, y = _sankey_positions(counts_list, x_margin=0.04, y_margin=0.06)
    return labels, x, y, df_filtered, df_grouped, df_result


def _generate_connections(df_filtered, labels, analyse_col, df_grouped=None, df_result=None):
    if df_grouped is None:
        df_grouped = _build_df_grouped(df_filtered, analyse_col)
    if df_result is None:
        _, _, df_result = _build_labels(df_grouped, analyse_col)

    label_to_idx = {lab: i for i, lab in enumerate(labels)}
    unique_pat_diag = _build_unique_pat_diag(df_grouped, analyse_col)
    trans = _build_transitions(unique_pat_diag, labels, label_to_idx)
    src = _add_source_links(df_result, labels, label_to_idx, analyse_col)

    links_df = pd.concat([trans, src], ignore_index=True)
    filtered_links = [
        (int(r.origin), int(r.destination), int(r.number), r.heritage)
        for _, r in links_df.iterrows()
    ]
    return filtered_links, unique_pat_diag


def _generate_node_notes(df_filtered, unique_pat_diag, labels, no_points, notitie_column):
    layer_cols = [c for c in unique_pat_diag.columns if c.startswith(Cols.LAYER_PREFIX)]
    long_nodes = (
        unique_pat_diag
        .melt(id_vars=[Cols.PAT_ID], value_vars=layer_cols, var_name="Layer", value_name="label")
        .dropna(subset=["label"])
    )
    pv = _pat_notitie_column_pairs(df_filtered, notitie_column)
    long_nodes = long_nodes.merge(pv, on=Cols.PAT_ID, how="left")

    pat_per_label = long_nodes.groupby("label")[Cols.PAT_ID].nunique()
    per_v = (
        long_nodes.groupby(["label", notitie_column])[Cols.PAT_ID]
        .nunique()
        .reset_index(name="n_pat")
        .sort_values(["label", "n_pat"], ascending=[True, False], kind="stable")
    )

    node_notes = {}
    for lab in labels:
        if lab == Cols.SOURCE_NM:
            continue
        total = int(pat_per_label.get(lab, 0))
        if total == 0:
            node_notes[lab] = "no note"
            continue
        top = per_v.loc[per_v["label"] == lab].head(no_points)
        if top.empty:
            node_notes[lab] = "no note"
            continue
        parts = []
        for i, (_, r) in enumerate(top.iterrows(), 1):
            name = r[notitie_column]
            n_pat = int(r["n_pat"])
            pct = (n_pat / total * 100) if total else 0
            parts.append(f"{i}. {name}, with {n_pat} patients ({pct:.0f}% of total)<br>")
        node_notes[lab] = (
            f"Patient count = {total}, top {no_points} sources are:<br>"
            + "".join(parts).rstrip("<br>")
        )
    return node_notes


def _generate_link_notes(df_filtered, unique_pat_diag, labels, links, analyse_col, no_points, notitie_column):
    df_links = pd.DataFrame(links, columns=[Cols.ORIGIN, Cols.DESTINATION, Cols.NUMBER, Cols.HERITAGE])
    mapper = pd.Series(labels, index=range(len(labels)))
    df_links[Cols.ORIGIN_NM] = df_links[Cols.ORIGIN].map(mapper)
    df_links[Cols.DEST_NM] = df_links[Cols.DESTINATION].map(mapper)

    unique_pat_diag = unique_pat_diag.loc[:, ~unique_pat_diag.columns.duplicated()].copy()
    layer_cols = [c for c in unique_pat_diag.columns if c.startswith(Cols.LAYER_PREFIX)]

    if not layer_cols:
        trans_pat = pd.DataFrame(columns=[Cols.PAT_ID, Cols.DIAG, Cols.HERITAGE, Cols.ORIGIN_NM, Cols.DEST_NM])
    else:
        heritage_col = layer_cols[0]
        trans_pat_frames = []
        for i in range(len(layer_cols) - 1):
            a, b = layer_cols[i], layer_cols[i + 1]
            tmp = pd.DataFrame({
                Cols.PAT_ID: unique_pat_diag[Cols.PAT_ID],
                Cols.HERITAGE: unique_pat_diag[heritage_col],
                Cols.ORIGIN_NM: unique_pat_diag[a],
                Cols.DEST_NM: unique_pat_diag[b],
            })
            tmp = tmp.dropna(subset=[Cols.ORIGIN_NM, Cols.DEST_NM])
            if not tmp.empty:
                trans_pat_frames.append(tmp)
        trans_pat = (
            pd.concat(trans_pat_frames, ignore_index=True)
            if trans_pat_frames
            else pd.DataFrame(columns=[Cols.PAT_ID, Cols.HERITAGE, Cols.ORIGIN_NM, Cols.DEST_NM])
        )

    pv = _pat_notitie_column_pairs(df_filtered, notitie_column)
    trans_pv = trans_pat.merge(pv, on=Cols.PAT_ID, how="left").dropna(subset=[notitie_column])

    per_edge_v = (
        trans_pv.groupby([Cols.ORIGIN_NM, Cols.DEST_NM, Cols.HERITAGE, notitie_column])[Cols.PAT_ID]
        .nunique()
        .reset_index(name="n_pat")
    )

    categories = per_edge_v[notitie_column].dropna().unique().tolist()
    for v in categories:
        df_links[v] = 0

    if not per_edge_v.empty:
        key = [Cols.ORIGIN_NM, Cols.DEST_NM, Cols.HERITAGE]
        df_links = df_links.merge(
            per_edge_v.pivot_table(index=key, columns=notitie_column, values="n_pat", aggfunc="sum")
            .fillna(0)
            .reset_index(),
            on=key,
            how="left",
        )
        fill_cols = [c for c in categories if c in df_links.columns]
        df_links[fill_cols] = df_links[fill_cols].fillna(0).astype("int64")

    output_strings = []
    cat_cols = [
        c for c in df_links.columns
        if c not in {Cols.ORIGIN, Cols.DESTINATION, Cols.NUMBER, Cols.HERITAGE, Cols.ORIGIN_NM, Cols.DEST_NM}
    ]
    for _, row in df_links.iterrows():
        total = int(row[Cols.NUMBER])
        text = f"Total number of patients {total}, most frequent sources<br>"
        if total > 0 and cat_cols:
            s = row[cat_cols].astype(float)
            top = s.nlargest(min(no_points, int((s > 0).sum())))
            for i, (name, val) in enumerate(top.items(), 1):
                pct = (val / total * 100) if total else 0
                text += f"{i}. {name}, with {int(val)} patients ({pct:.0f}% of total)<br>"
        output_strings.append(text)

    return output_strings, df_links
