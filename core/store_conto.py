"""
store_conto.py
--------------
Gestione dello storico della movimentazione del conto Fineco (CFD/Derivati).

Struttura analoga a store.py ma per il file della movimentazione del conto
corrente, che contiene margini di variazione derivati, oneri CFD, proventi CFD.
"""

from pathlib import Path

import pandas as pd


STORICO_CONTO_PATH = Path("data/storico_conto.csv")

# Chiave di deduplicazione: data + descrizione completa + importi
CHIAVE_DEDUP_CONTO = [
    "Data valuta",
    "Descrizione Completa",
    "Entrate",
    "Uscite",
]


def _normalizza_chiavi_conto(df: pd.DataFrame) -> pd.DataFrame:
    """Normalizza le colonne chiave per garantire un confronto consistente."""
    df = df.copy()
    for col in ["Entrate", "Uscite"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).round(4)
    if "Descrizione Completa" in df.columns:
        df["Descrizione Completa"] = df["Descrizione Completa"].fillna("").astype(str).str.strip()
        df.loc[df["Descrizione Completa"].str.lower() == "nan", "Descrizione Completa"] = ""
    return df


def load_storico_conto() -> pd.DataFrame:
    """Carica lo storico conto da CSV. Restituisce DataFrame vuoto se non esiste."""
    if not STORICO_CONTO_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(STORICO_CONTO_PATH, parse_dates=["Data valuta"])
    return df


def save_storico_conto(df: pd.DataFrame) -> None:
    """Salva lo storico conto su CSV."""
    STORICO_CONTO_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(STORICO_CONTO_PATH, index=False)


def merge_storico_conto(storico: pd.DataFrame, nuovo: pd.DataFrame) -> dict:
    """
    Merge del DataFrame storico conto con nuove righe.
    Deduplica sulla chiave CHIAVE_DEDUP_CONTO.

    Returns:
        {
          "df":          DataFrame risultante (storico aggiornato),
          "n_nuove":     numero di righe effettivamente aggiunte,
          "n_duplicate": numero di righe scartate come duplicate,
        }
    """
    chiave = [c for c in CHIAVE_DEDUP_CONTO if c in nuovo.columns]

    nuovo_norm = _normalizza_chiavi_conto(nuovo.copy())

    if storico.empty:
        n_prima = len(nuovo_norm)
        df_dedup = nuovo_norm.drop_duplicates(subset=chiave, keep="first")
        n_dup = n_prima - len(df_dedup)
        df_sorted = df_dedup.sort_values("Data valuta").reset_index(drop=True)
        return {"df": df_sorted, "n_nuove": len(df_sorted), "n_duplicate": n_dup}

    storico_norm = _normalizza_chiavi_conto(storico.copy())
    combined = pd.concat([storico_norm, nuovo_norm], ignore_index=True)
    n_prima = len(combined)
    combined_dedup = combined.drop_duplicates(subset=chiave, keep="first")
    n_dup = n_prima - len(combined_dedup)
    n_nuove = len(combined_dedup) - len(storico_norm)

    df_sorted = combined_dedup.sort_values("Data valuta").reset_index(drop=True)
    return {"df": df_sorted, "n_nuove": max(0, n_nuove), "n_duplicate": n_dup}
