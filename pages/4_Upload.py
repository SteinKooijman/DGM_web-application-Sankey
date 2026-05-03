"""
Upload page – Sankey-Ideas
--------------------------
Upload patient data and the product catalogue once.
The main Sankey page reads these cached files and always computes
the diagram live based on the chosen filters.
"""

from __future__ import annotations

import glob as _glob
import io
import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.csv_builder import (
    CATALOGUE_CSV,
    DATA_DIR,
    PATIENT_CACHE_CSV,
    V4_CATALOGUE_PATH,
    generate_catalogue_csv,
    import_catalogue_from_v4,
    invalidate_sankey_caches,
    load_catalogue_csv,
    load_patient_cache,
    save_patient_cache,
)
from backend.sidebar import inject_shared_css

# ---------------------------------------------------------------------------
# Page config + shared styling
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Gegevens uploaden | Sankey-Ideas",
    page_icon="📂",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_shared_css()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INPUT_DIR = r"C:\2_KSA\1_DGM\9_WebApp_v3\99_latest_versions_input"

PAT_COLS_NEEDED = [
    "Pat_id", "Datum", "Diag_ID_EUK", "Diag_omschr_EUK",
    "Prod_ID", "Prod_nm", "Med_nm", "Group_nm", "Aantal", "Voorschrijver_nm",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Patiëntenbestand inlezen …")
def _parse_pat_bytes(file_bytes: bytes, filename: str) -> pd.DataFrame:
    df = (
        pd.read_csv(io.BytesIO(file_bytes))
        if filename.lower().endswith(".csv")
        else pd.read_excel(io.BytesIO(file_bytes))
    )
    # Case-insensitive column normalisation
    col_lower = {c.lower(): c for c in df.columns}
    renames = {
        col_lower[n.lower()]: n
        for n in PAT_COLS_NEEDED
        if n not in df.columns and n.lower() in col_lower
    }
    if renames:
        df = df.rename(columns=renames)
    if "Datum" in df.columns:
        df["Datum"] = pd.to_datetime(df["Datum"], errors="coerce")
    if "Diag_omschr_EUK" in df.columns:
        df["Diag_omschr_EUK"] = df["Diag_omschr_EUK"].astype(str)
    keep = [c for c in PAT_COLS_NEEDED if c in df.columns]
    return df[keep]


@st.cache_data(show_spinner=False)
def _try_load_t1() -> pd.DataFrame:
    paths = sorted(_glob.glob(os.path.join(INPUT_DIR, "T1_*.xlsx")))
    return pd.read_excel(paths[-1]) if paths else pd.DataFrame()


@st.cache_data(show_spinner=False)
def _try_load_t2() -> pd.DataFrame:
    paths = sorted(_glob.glob(os.path.join(INPUT_DIR, "T2_*.xlsx")))
    return pd.read_excel(paths[-1]) if paths else pd.DataFrame()


# ---------------------------------------------------------------------------
# Sidebar nav hint
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## ⚗️ Sankey-Ideas")
    st.markdown("---")
    st.page_link("app.py",                   label="📊 Samenvatting",  icon="📊")
    st.page_link("pages/2_Ontwerp.py",       label="✏️ Ontwerp",       icon="✏️")
    st.page_link("pages/3_Implementatie.py", label="🔍 Implementatie", icon="🔍")
    st.page_link("pages/4_Upload.py",        label="📂 Upload",        icon="📂")

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.title("📂 Gegevens uploaden")
st.markdown(
    '<div class="info-box">Upload hier het patiëntenbestand en de productcatalogus. '
    'Na het opslaan kunt u direct naar het Sankey-dashboard gaan en alle filters '
    '(datum, voorschrijver) interactief aanpassen.</div>',
    unsafe_allow_html=True,
)
st.markdown("<br>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Current status
# ---------------------------------------------------------------------------

col_s1, col_s2 = st.columns(2)

with col_s1:
    cache_df = load_patient_cache()
    if cache_df.empty:
        st.warning("⚠️ Geen patiëntendata geladen.")
    else:
        n_diag = cache_df["Diag_omschr_EUK"].nunique() if "Diag_omschr_EUK" in cache_df.columns else "?"
        n_pat  = cache_df["Pat_id"].nunique() if "Pat_id" in cache_df.columns else "?"
        d_min  = cache_df["Datum"].min().date() if "Datum" in cache_df.columns else "?"
        d_max  = cache_df["Datum"].max().date() if "Datum" in cache_df.columns else "?"
        st.success(
            f"✅ **Patiëntendata aanwezig**  \n"
            f"{len(cache_df):,} rijen · {n_pat} patiënten · {n_diag} diagnoses  \n"
            f"Periode: {d_min} → {d_max}"
        )

with col_s2:
    cat_df = load_catalogue_csv()
    if cat_df.empty:
        st.warning("⚠️ Geen productcatalogus geladen.")
    else:
        st.success(
            f"✅ **Productcatalogus aanwezig**  \n"
            f"{len(cat_df):,} producten"
        )

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 1 – Patient data
# ---------------------------------------------------------------------------

st.header("1. Patiëntendata")
st.markdown(
    "Upload het patiëntenbestand (v02 of later). "
    f"Vereiste kolommen: `{'`, `'.join(PAT_COLS_NEEDED)}`"
)

pat_upload = st.file_uploader(
    "Patiëntenbestand (.xlsx, .xls of .csv)",
    type=["xlsx", "xls", "csv"],
    key="pat_uploader",
)

if pat_upload is not None:
    pat_bytes = pat_upload.read()
    pat_df = _parse_pat_bytes(pat_bytes, pat_upload.name)

    missing = [c for c in ["Pat_id", "Diag_omschr_EUK", "Datum", "Prod_ID"] if c not in pat_df.columns]
    if missing:
        st.error(f"Verplichte kolommen ontbreken: {', '.join(missing)}")
    else:
        n_rows = len(pat_df)
        n_diag = pat_df["Diag_omschr_EUK"].nunique()
        n_pat  = pat_df["Pat_id"].nunique()
        d_min  = pat_df["Datum"].min().date()
        d_max  = pat_df["Datum"].max().date()

        st.info(
            f"**Bestand:** {pat_upload.name}  \n"
            f"{n_rows:,} rijen · {n_pat} patiënten · {n_diag} diagnoses  \n"
            f"Periode: {d_min} → {d_max}"
        )
        diagnoses_found = sorted(pat_df["Diag_omschr_EUK"].dropna().unique())
        with st.expander(f"Diagnoses in dit bestand ({len(diagnoses_found)})", expanded=False):
            st.write(", ".join(diagnoses_found))

        if st.button("💾 Patiëntendata opslaan", type="primary", use_container_width=True):
            save_patient_cache(pat_df)
            invalidate_sankey_caches()
            st.success(f"✅ Opgeslagen: {n_rows:,} rijen voor {n_diag} diagnoses.")
            st.cache_data.clear()

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 2 – Product catalogue
# ---------------------------------------------------------------------------

st.header("2. Productcatalogus")

cat_tab1, cat_tab2 = st.tabs(["📄 Upload v4 Excel", "🔧 Genereer uit T1 + T2"])

with cat_tab1:
    st.markdown(
        "Upload de productcatalogus in v4-formaat "
        "(`product_diagnosis_catelogue_v4_new.xlsx` of vergelijkbaar)."
    )
    v4_upload = st.file_uploader(
        "Productcatalogus v4 (.xlsx)",
        type=["xlsx", "xls"],
        key="v4_uploader",
    )
    if v4_upload is not None:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_path = os.path.join(DATA_DIR, "_uploaded_v4.xlsx")
        with open(tmp_path, "wb") as f:
            f.write(v4_upload.read())

        if st.button("💾 Catalogus opslaan (v4)", type="primary", use_container_width=True, key="save_v4"):
            with st.spinner("Catalogus importeren …"):
                try:
                    cat = import_catalogue_from_v4(v4_path=tmp_path)
                    st.success(f"✅ Productcatalogus opgeslagen: {len(cat):,} producten.")
                    st.cache_data.clear()
                except Exception as exc:
                    st.error(f"Fout bij importeren: {exc}")

with cat_tab2:
    st.markdown(
        "Genereer de catalogus automatisch uit de T1 (medicijn-/dosisgegevens) "
        "en T2 (prijs-/vergoedingsgegevens) bestanden."
    )

    t1_upload = st.file_uploader("T1 bestand (.xlsx)", type=["xlsx", "xls"], key="t1_uploader")
    t2_upload = st.file_uploader("T2 bestand (.xlsx)", type=["xlsx", "xls"], key="t2_uploader")

    # Also try auto-loading from INPUT_DIR if files exist there
    auto_t1 = _try_load_t1()
    auto_t2 = _try_load_t2()
    if not auto_t1.empty and not auto_t2.empty:
        st.info(f"T1 en T2 gevonden in `{INPUT_DIR}` — kunnen direct worden gebruikt.")

    if st.button("🔧 Genereer catalogus", type="primary", use_container_width=True, key="gen_cat_btn"):
        with st.spinner("Catalogus genereren …"):
            if t1_upload and t2_upload:
                t1 = pd.read_excel(io.BytesIO(t1_upload.read()))
                t2 = pd.read_excel(io.BytesIO(t2_upload.read()))
            elif not auto_t1.empty and not auto_t2.empty:
                t1, t2 = auto_t1, auto_t2
            else:
                st.error("Upload T1 en T2 bestanden, of zorg dat ze in INPUT_DIR staan.")
                t1 = t2 = pd.DataFrame()

            if not t1.empty and not t2.empty:
                try:
                    cat = generate_catalogue_csv(t1, t2)
                    st.success(f"✅ Productcatalogus gegenereerd: {len(cat):,} producten.")
                    st.cache_data.clear()
                except Exception as exc:
                    st.error(f"Fout bij genereren: {exc}")

st.markdown("---")

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

if not load_patient_cache().empty:
    st.page_link("app.py", label="📊 Ga naar Sankey-dashboard →", icon="📊")
