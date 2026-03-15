"""
engine_conto.py
---------------
Elabora la movimentazione del conto Fineco per estrarre i PnL di CFD/Derivati.

Il file della movimentazione del conto contiene voci specifiche che rappresentano
guadagni o perdite realizzati su strumenti derivati (futures, CFD):

  - Margine di variazione derivati  → mark-to-market giornaliero (profitto/perdita)
  - Oneri CFD                       → costi di finanziamento/rollover
  - Proventi Societari CFD Long     → dividendi su posizioni long
  - Oneri Societari CFD Short       → dividendi su posizioni short

Per calcolare il PnL:
  PnL riga = Entrate - Uscite  (entrambi positivi nel file Fineco)

Raggruppamento per Strumento:
  Il nome dello strumento viene estratto dalla colonna "Descrizione Completa"
  rimuovendo prefissi operazione, prefissi prodotto (SUPER, USA, MINI),
  codici contratto (CD...) e codici mese-anno (JAN26, FEB25...).

Esempi:
  "Margine di variazione derivati SUPER PETROLIO JAN26 CD535..."  → "PETROLIO"
  "Div.su 5,000 USA NASDAQ"                                       → "NASDAQ"
  "Margine di variazione derivati GAS NATURALE MAY25"             → "GAS NATURALE"
"""

import re

import pandas as pd


# ------------------------------------------------------------------
# Pattern per estrazione nome strumento
# ------------------------------------------------------------------

# Prefissi operazione da rimuovere (ordine: dal più lungo al più corto)
_PREFISSI_OP = [
    r"margine di variazione derivati\s+",
    r"proventi societari cfd long\s+",
    r"oneri societari cfd short\s+",
    r"oneri su derivati\s+",
    r"oneri cfd\s+",
    r"div\.su\s+[\d,.]+\s+",   # "Div.su 5,000 " oppure "Div.su 1.000 "
]

# Modificatori prodotto da rimuovere
_PREFISSI_PRODOTTO = ["SUPER ", "USA ", "MINI "]

# Mese abbreviato + 2 cifre anno (es. JAN26, FEB25, DEC24)
_PAT_MONTHYEAR = re.compile(r"\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}\b")

# Codici contratto Fineco (es. CD53352102283138)
_PAT_CONTRACTCODE = re.compile(r"\bCD[A-Z0-9]{6,}\b")


def _extract_instrument(desc_completa: str) -> str:
    """
    Estrae il nome dello strumento da Descrizione Completa.

    Steps:
      1. Rimuovi prefisso operazione
      2. Rimuovi codici contratto
      3. Rimuovi mese-anno
      4. Rimuovi prefissi prodotto (SUPER, USA, MINI)
    """
    s = str(desc_completa).strip()

    # 1. Prefisso operazione
    for pat in _PREFISSI_OP:
        s = re.sub(pat, "", s, flags=re.IGNORECASE).strip()

    # 2. Codici contratto
    s = _PAT_CONTRACTCODE.sub("", s).strip()

    # 3. Mese-anno
    s = _PAT_MONTHYEAR.sub("", s).strip()

    # 4. Prefissi prodotto (solo se lo strumento inizia con quel token)
    for pref in _PREFISSI_PRODOTTO:
        if s.upper().startswith(pref):
            s = s[len(pref):].strip()

    return s.strip() or "ALTRO"


def _classify_type(desc_completa: str) -> str:
    """
    Classifica il tipo di movimento:
      "Margine variazione"  → mark-to-market (margine di variazione derivati)
      "Oneri/Proventi"      → oneri, proventi, dividendi su derivati
    """
    d = str(desc_completa).lower()
    if "margine di variazione" in d:
        return "Margine variazione"
    return "Oneri/Proventi"


# ------------------------------------------------------------------
# Funzioni principali
# ------------------------------------------------------------------

def processa_conto(df_conto: pd.DataFrame, descrizioni_cfd: list) -> pd.DataFrame:
    """
    Filtra le movimentazioni CFD/derivati e aggrega per Anno / Strumento / Tipo.

    Args:
        df_conto:         DataFrame della movimentazione conto (da load_excel_conto).
                          Colonne attese: Data valuta, Descrizione,
                          Descrizione Completa, Entrate, Uscite.
        descrizioni_cfd:  Lista di keyword da cercare nella colonna Descrizione
                          (da config.yaml → descrizioni_cfd_conto).

    Returns:
        DataFrame con colonne: Anno, Strumento, Tipo, PnL (€)
    """
    if df_conto.empty:
        return pd.DataFrame(columns=["Anno", "Strumento", "Tipo", "PnL (€)"])

    df = df_conto.copy()

    # --- Filtro per descrizioni CFD ---
    if "Descrizione" in df.columns and descrizioni_cfd:
        desc_lower = df["Descrizione"].astype(str).str.lower().str.strip()
        keywords = [k.lower() for k in descrizioni_cfd]
        pattern = "|".join(re.escape(k) for k in keywords)
        mask = desc_lower.str.contains(pattern, regex=True, na=False)
        df = df[mask].copy()

    if df.empty:
        return pd.DataFrame(columns=["Anno", "Strumento", "Tipo", "PnL (€)"])

    # --- Normalizza Entrate / Uscite ---
    for col in ["Entrate", "Uscite"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0

    df["PnL_riga"] = df["Entrate"] - df["Uscite"]

    # --- Anno ---
    df["Anno"] = df["Data valuta"].dt.year

    # --- Strumento e Tipo da Descrizione Completa ---
    col_desc = (
        "Descrizione Completa"
        if "Descrizione Completa" in df.columns
        else "Descrizione"
    )
    df["Strumento"] = df[col_desc].apply(_extract_instrument)
    df["Tipo"]      = df[col_desc].apply(_classify_type)

    # --- Aggregazione ---
    agg = (
        df.groupby(["Anno", "Strumento", "Tipo"], as_index=False)["PnL_riga"]
        .sum()
        .rename(columns={"PnL_riga": "PnL (€)"})
    )
    agg["PnL (€)"] = agg["PnL (€)"].round(2)

    return agg.sort_values(["Anno", "Strumento", "Tipo"]).reset_index(drop=True)


def riepilogo_per_anno_strumento(df_processed: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot: (Anno, Strumento) × Tipo con colonne:
      Margine variazione (€) | Oneri/Proventi (€) | Totale (€)
    """
    if df_processed.empty:
        return pd.DataFrame(
            columns=["Anno", "Strumento",
                     "Margine variazione (€)", "Oneri/Proventi (€)", "Totale (€)"]
        )

    pivot = df_processed.pivot_table(
        index=["Anno", "Strumento"],
        columns="Tipo",
        values="PnL (€)",
        aggfunc="sum",
        fill_value=0.0,
    ).reset_index()

    # Assicura entrambe le colonne tipo anche se assenti nel dataset
    for col in ["Margine variazione", "Oneri/Proventi"]:
        if col not in pivot.columns:
            pivot[col] = 0.0

    pivot = pivot.rename(columns={
        "Margine variazione": "Margine variazione (€)",
        "Oneri/Proventi":     "Oneri/Proventi (€)",
    })
    pivot["Totale (€)"] = (
        pivot["Margine variazione (€)"] + pivot["Oneri/Proventi (€)"]
    ).round(2)

    return pivot.sort_values(["Anno", "Strumento"]).reset_index(drop=True)


def totale_pnl_per_anno(df_processed: pd.DataFrame) -> dict:
    """
    Restituisce un dizionario {anno: pnl_totale_eur} per tutti gli anni.
    Usato per alimentare calcola_quadro_rt.
    """
    if df_processed.empty:
        return {}
    return (
        df_processed.groupby("Anno")["PnL (€)"]
        .sum()
        .round(2)
        .to_dict()
    )
