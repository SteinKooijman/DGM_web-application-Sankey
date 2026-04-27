from enum import Enum


class Columns(Enum):
    """All column names from all input tables - unique column names only"""

    # Shared columns
    MEDICINE_NAME = 'Medicine Name'
    DIAGNOSIS = 'Diagnosis'
    PROD_OMSCHR = 'Prod_omschr'
    ADMINISTRATION_METHOD = 'Administration Method'
    PROD_ID = 'Prod_ID'
    DIAG_ID_EUK = 'Diag_ID_EUK'
    DIAG_ID_CLIENT = 'Diag_ID_client'
    DIAG_OMSCHR_EUK = 'Diag_omschr_EUK'

    # T1_med_diag_dosage.xlsx specific columns
    GROUP_NAME = 'Group Name'
    DOSAGE_TEXT = 'Dosage_text'
    ATCCODE = 'ATCcode'
    DOSAGE_HEIGHT = 'Dosage Height'
    DOSAGE_FREQUENCY = 'Dosage Frequency'
    DOSAGE_TYPE = 'Dosage Type'
    INFO_DOSAGE = 'Info_Dosage?'
    DATE_EDITED = 'Date edited'
    INTravitreaal = 'Intravitreaal?'
    DOSAGE_INFORMATION = 'Info_Dosage?'

    # T1H1_diag_linked.xlsx specific columns
    DIAG_ID_EUK_TOEGEWEZEN = 'Diag_ID_EUK_toegewezen?'

    # T2 and T2_combined shared columns
    PROD_NM = 'Prod_nm'
    PRIJS_ST = 'Prijs/st'
    VERGOEDING_ST = 'Vergoeding/st'
    MG = 'mg'
    AANTAL_PER_VERPAKKING = 'Aantal_per_verpakking'

    # T3_prod_diag specific columns
    Q1M = 'Q1M'
    Q3M = 'Q3M'
    Q6M = 'Q6M'
    Q12M = 'Q12M'
    Q12M_PROD_NM_ADMIN = 'Q12M_prod_nm_admin'
    Q12M_PROD_NM = 'Q12M_prod_nm'
    Q12M_MED_NM = 'Q12M_med_nm'
    Q12M_DIAG = 'Q12M_diag'

    # T3H1_diag specific columns
    UITGAVEN_TOT = 'Uitgaven_tot'
    VERGOEDING_TOT = 'Vergoeding_tot'
    DIAG_ID_OMSCHR = 'Diag_ID_omschr'

    # T4_EUK_diag.xlsx specific columns
    SPEC_EUK = 'Spec_EUK'
    MOD0_EUK = 'Mod0_EUK'
    MOD1_EUK = 'Mod1_EUK'
    OPMERKINGEN = 'Opmerkingen'

    # T6_Richtlijnen_relaties.xlsx specific columns
    RCHTLN_GROEP_ID = 'Rchtln_groep_ID'
    RCHTLN_GROEP_OMSCHR = 'Rchtln_groep_omschr'
    RICHTLIJN_OPMERKINGEN = 'Richtlijn_Opmerkingen'

    PERCENTAGE_PATIENTS_NOT_USED_NEW_PRODUCT = 'Perc_volume_not_used_new_product'

    # New and calculated columns
    STUK_PER_TOEDIENING = 'Stuks/toediening'
    MARGIN_ST = 'Margin/st'
    STUK_PER_DAG = 'Stuks/dag'
    PRIJS_PER_DAG = 'Prijs/dag'
    MARGIN_PER_DAG = 'Margin/dag'
    MED_ADMIN_ID = 'Med_admin_ID'
    MED_ADMIN_DIAG_ID = 'Med_admin_diag_ID'
    CONNECTION_T1 = 'Connection_T1'
    TOTALE_UITGAVES = 'Totaal uitgaves'
    CONNECTION_T2 = 'Connection_T2'
    CONNECTION_T1_REASON = 'Connection_T1_reason'

    # Analysis.py specific columns
    TYPE_OF_IDEA_EUK = 'Type_of_Idea_EUK'
    PROD_ID_MAX_MARGIN = 'Prod_ID_max_margin'
    PROD_NM_MAX_MARGIN = 'Prod_nm_max_margin'
    ADMINISTRATION_METHOD_MAX_MARGIN = 'Administration Method_max_margin'
    MEDICINE_NAME_MAX_MARGIN = 'Medicine Name_max_margin'
    GROUP_NAME_MAX_MARGIN = 'Group Name_max_margin'
    INTER_GROUP_ID = 'Inter_Group_ID'
    INTER_GROUP_ID_MAX_MARGIN = 'Inter_Group_ID_max_margin'
    MARGIN_PER_DAY_MAX = 'Margin/day_max'
    PROD_OMSCHR_MAX_MARGIN = 'Prod_omschr_max_margin'
    PRIJS_ST_MAX_MARGIN = 'Prijs/st_max_margin'
    VERGOEDING_ST_MAX_MARGIN = 'Vergoeding/st_max_margin'
    MARGIN_ST_MAX_MARGIN = 'Margin/st_max_margin'
    STUK_PER_TOEDIENING_MAX_MARGIN = 'Stuks/toediening_max_margin'
    STUK_PER_DAG_MAX_MARGIN = 'Stuks/dag_max_margin'
    DOSAGE_TEXT_MAX_MARGIN = 'Dosage_text_max_margin'
    NUMBER_OF_DAYS_TREATED = 'Number_of_days_treated'
    MARGIN_NOW = 'Margin_now'
    MARGIN_MAX = 'Margin_max'
    MARGIN_INCREASE = 'Margin_increase'
    FREQ_DOSAGE_DAYS = 'Freq_dosage (days)'
    FREQ_DOSAGE_MAX_MARGIN_DAYS = 'Freq_dosage_max_margin (days)'
    TOTALE_UITGAVEN_NOW = 'Totale_uitgaven_now'
    TOTALE_UITGAVEN_MAX_MARGIN = 'Totale_uitgaven_max_margin'
    INTER_GROUP_OMSCHR = 'Inter_Group_omschr'
    PROD_ID_MOST_FREQ = 'Prod_ID_most_freq'
    PROD_ID_MIN_MARGIN = 'Prod_ID_min_margin'
    MARGIN_DAY_AVG = 'Margin/day_avg'
    Q1M_MAX_MARGIN = 'Q1M_max_margin'
    Q3M_MAX_MARGIN = 'Q3M_max_margin'
    Q6M_MAX_MARGIN = 'Q6M_max_margin'
    Q12M_MAX_MARGIN = 'Q12M_max_margin'

    # raw_pat_data_nwz.csv specific columns
    PAT_ID = 'Pat_ID'
    AANTAL = 'Aantal'
    DATUM = 'datum'


# Default analysis parameters
STAND_KG = 75
STAND_M2 = 1.7
CalculationParam_months = 'Q3M'
TOPX = 10

dict_type_of_idea_euk = {
    'z_no_improvement_found': 0,
    'a. Verpakking': 1,
    'b. Biosimilar': 1,
    'c. Administration method': 1,
    'd. Biosimilar and administration method': 1,
    'e. Group medicine': Columns.Q12M_MED_NM.value,
    'f. Other group medicine': Columns.Q12M_MED_NM.value,
    'g. Group medicine and administration method': Columns.Q12M_MED_NM.value,
    'h. Other group medicine and administration method': Columns.Q12M_MED_NM.value,
    'zz_onbekende_verandering': 0
}
