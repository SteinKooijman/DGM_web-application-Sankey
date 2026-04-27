import pandas as pd
from pathlib import Path

# ── Input / Output paths ───────────────────────────────────────────────────────
INPUT_T2    = Path("data/T2_prod_price.xlsx")
INPUT_T1    = Path("data/product_diagnosis_catalogue_v02.xlsx")
INPUT_T4    = Path(r"C:\2_KSA\1_DGM\9_WebApp_v3\99_latest_versions_input\T4_EUK_diag.xlsx")
OUTPUT_FILE = Path("data/product_diagnosis_catelogue_v5.xlsx")

# ── Dosage Type multipliers for "Dosage Height/toediening" ────────────────────
# Modify these values to change the body-weight / body-surface assumptions.
DOSAGE_MULTIPLIERS = {
    "kg": 75,    # assumed body weight in kg
    "m2": 1.8,   # assumed body surface area in m²
}
DOSAGE_DEFAULT = 1   # multiplier when Dosage Type is blank

# ── Load ───────────────────────────────────────────────────────────────────────
t2 = pd.read_excel(INPUT_T2)
t1 = pd.read_excel(INPUT_T1)
t4 = pd.read_excel(INPUT_T4, usecols=["Diag_ID_EUK", "Diag_omschr_EUK"])

# ── Left join T2 ← T1 on Medicine Name (case-insensitive) ────────────────────
t2["_key"] = t2["Medicine Name"].str.strip().str.lower()
t1["_key"] = t1["Medicine Name"].str.strip().str.lower()

result = t2.merge(
    t1.drop(columns=["Medicine Name"]),
    on="_key",
    how="left",
).drop(columns=["_key"])

# ── Explode Diag_ID_EUK: "5;10" → two rows with 5 and 10 ────────────────────
result["Diag_ID_EUK"] = result["Diag_ID_EUK"].astype(str).str.split(";")
result = result.explode("Diag_ID_EUK")
result["Diag_ID_EUK"] = result["Diag_ID_EUK"].str.strip().replace("nan", pd.NA)
result = result.reset_index(drop=True)

# ── Join T4: add Diag_omschr_EUK ─────────────────────────────────────────────
# Diag_ID_EUK is string after explode; cast T4's key to string for matching.
t4["Diag_ID_EUK"] = t4["Diag_ID_EUK"].astype(str).str.strip()
result = result.merge(t4, on="Diag_ID_EUK", how="left")

# ── Calculated columns ────────────────────────────────────────────────────────
# 1. Marge/st
result["Marge/st"] = result["Vergoeding/st"] - result["Prijs/st"]

# 2. Dosage Height/toediening
multiplier = (
    result["Dosage Type"]
    .str.strip()
    .map(DOSAGE_MULTIPLIERS)
    .fillna(DOSAGE_DEFAULT)
)
result["Dosage Height/toediening"] = result["Dosage Height"] * multiplier

# 3. Stuks/toediening
result["Stuks/toediening"] = result["Dosage Height/toediening"] / result["mg"]

# 4. Stuks/dag
result["Stuks/dag"] = result["Stuks/toediening"] / result["Dosage Frequency"]

# 5–7. Cost/dag columns
result["Prijs/dag"]      = result["Prijs/st"]      * result["Stuks/dag"]
result["Vergoeding/dag"] = result["Vergoeding/st"]  * result["Stuks/dag"]
result["Marge/dag"]      = result["Marge/st"]       * result["Stuks/dag"]

# ── Final column order ────────────────────────────────────────────────────────
COLUMN_ORDER = [
    # Product identifiers
    "Prod_ID", "Prod_omschr", "Prod_nm", "Administration Method_x",
    "Medicine Name", "Medicine Name _ ori",
    # Pricing + margin per unit
    "Prijs/st", "Vergoeding/st", "Marge/st",
    # Per-insurer reimbursement
    "Vergoeding/st_ACHMEA", "Vergoeding/st_ASR", "Vergoeding/st_CZ",
    "Vergoeding/st_MENZIS", "Vergoeding/st_VGZ",
    # Packaging
    "mg", "Aantal_per_verpakking", "Uit_db_2025?", "Prijs/verpakking",
    "Zonder_iets",
    "Vergoeding/verpakking_ACHMEA", "ACHMEA=PRICE?",
    "Vergoeding/verpakking_ASR",    "ASR=PRICE?",
    "Vergoeding/verpakking_CZ",     "CZ=PRICE?",
    "Vergoeding/verpakking_MENZIS", "MENZIS=PRICE?",
    "Vergoeding/verpakking_VGZ",    "VGZ=PRICE?",
    # Market share
    "Aandeel_ACHMEA", "Aandeel_ASR", "Aandeel_CZ",
    "Aandeel_MENZIS", "Aandeel_VGZ",
    "VGZ_vergoeding_gelijk_biosimilar_of_gelijk_prijs_gezet",
    # Diagnosis catalogue
    "Group Name", "Diagnosis", "Dosage_text", "ATCcode",
    "Indicatie Tekst", "Diag_ID_EUK", "Diag_omschr_EUK", "Date edited",
    # Dosage detail + derived dosage columns
    "Dosage Frequency", "Administration Method_y",
    "Dosage Height", "Dosage Type",
    "Dosage Height/toediening", "Stuks/toediening", "Stuks/dag",
    "Prijs/dag", "Vergoeding/dag", "Marge/dag",
    "Info_Dosage?",
]

# Drop any column not present (guards against schema changes in source files)
final_cols = [c for c in COLUMN_ORDER if c in result.columns]
result = result[final_cols]

# ── Save ───────────────────────────────────────────────────────────────────────
result.to_excel(OUTPUT_FILE, index=False)
print(f"Saved : {OUTPUT_FILE}")
print(f"Shape : {result.shape[0]:,} rows × {result.shape[1]} columns")
