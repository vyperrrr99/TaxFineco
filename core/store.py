"""
store.py
--------
Gestisce la persistenza del CSV storico delle movimentazioni Fineco.

Il CSV storico (data/movimentazione_storico.csv) accumula tutte le righe
caricate nel tempo. La deduplicazione garantisce che ogni operazione
sia registrata una sola volta, anche se lo stesso file viene caricato più volte.
"""

from pathlib import Path

import pandas as pd


STORICO_PATH = Path("data/movimentazione_storico.csv")

# Campi che identificano univocamente una riga di movimentazione.
# Due righe con tutti questi campi identici sono considerate duplicate.
CHIAVE_DEDUP = ["Data valuta", "Isin", "Descrizione", "Segno", "Quantita", "Controvalore"]


def load_storico() -> pd.DataFrame:
    """
    Carica il CSV storico.

    Returns:
        DataFrame con le movimentazioni salvate, oppure DataFrame vuoto
        se il file non esiste ancora.
    """
    if not STORICO_PATH.exists():
        return pd.DataFrame()

    df = pd.read_csv(
        STORICO_PATH,
        parse_dates=["Data valuta"],
    )
    return df


def save_storico(df: pd.DataFrame) -> None:
    """
    Salva il DataFrame sul CSV storico, ordinato cronologicamente.

    Crea la cartella data/ se non esiste.
    """
    STORICO_PATH.parent.mkdir(parents=True, exist_ok=True)
    df_sorted = df.sort_values("Data valuta").reset_index(drop=True)
    df_sorted.to_csv(STORICO_PATH, index=False)


def merge_storico(storico: pd.DataFrame, nuovo: pd.DataFrame) -> dict:
    """
    Unisce lo storico esistente con le nuove righe, eliminando i duplicati.

    La deduplicazione è basata su CHIAVE_DEDUP. In caso di riga identica,
    si conserva la prima occorrenza (quella già presente nello storico).

    Args:
        storico: DataFrame caricato da load_storico() (può essere vuoto).
        nuovo:   DataFrame caricato da load_excel() con le nuove movimentazioni.

    Returns:
        Dizionario con:
          - "df":          DataFrame unificato, ordinato cronologicamente
          - "n_nuove":     numero di righe effettivamente aggiunte
          - "n_duplicate": numero di righe scartate come duplicate
    """
    if storico.empty:
        df_nuovo_norm = _normalizza_chiavi(nuovo.copy())
        # Deduplicazione interna: il file sorgente può contenere righe identiche
        # (stessa data/ISIN/segno/qty/controvalore). Senza questo step,
        # al caricamento successivo quelle righe verrebbero rimosse come dup
        # causando n_nuove negativo e lo storico che "si restringe".
        n_prima = len(df_nuovo_norm)
        df_dedup = df_nuovo_norm.drop_duplicates(subset=CHIAVE_DEDUP, keep="first")
        n_duplicate = n_prima - len(df_dedup)
        df_sorted = df_dedup.sort_values("Data valuta").reset_index(drop=True)
        return {
            "df": df_sorted,
            "n_nuove": len(df_sorted),
            "n_duplicate": n_duplicate,
        }

    storico_norm = _normalizza_chiavi(storico.copy())
    nuovo_norm = _normalizza_chiavi(nuovo.copy())

    # Concatena: storico prima, nuovo dopo — così drop_duplicates(keep="first")
    # conserva le righe già nello storico in caso di conflitto.
    n_storico = len(storico_norm)
    combined = pd.concat([storico_norm, nuovo_norm], ignore_index=True)

    n_prima = len(combined)
    combined_dedup = combined.drop_duplicates(subset=CHIAVE_DEDUP, keep="first")
    n_duplicate = n_prima - len(combined_dedup)
    n_nuove = len(combined_dedup) - n_storico

    combined_dedup = combined_dedup.sort_values("Data valuta").reset_index(drop=True)

    return {
        "df": combined_dedup,
        "n_nuove": n_nuove,
        "n_duplicate": n_duplicate,
    }


def _normalizza_chiavi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalizza le colonne della chiave di deduplicazione per confronti stabili.

    - Controvalore: arrotondato a 2 decimali
    - Quantita:     arrotondato a 6 decimali (per frazioni di ETF)
    - Segno:        maiuscolo
    - Descrizione:  strip whitespace
    - Isin:         strip whitespace
    """
    if "Controvalore" in df.columns:
        df["Controvalore"] = pd.to_numeric(df["Controvalore"], errors="coerce").round(2)
    if "Quantita" in df.columns:
        df["Quantita"] = pd.to_numeric(df["Quantita"], errors="coerce").round(6)
    if "Segno" in df.columns:
        df["Segno"] = df["Segno"].astype(str).str.strip().str.upper()
    if "Descrizione" in df.columns:
        df["Descrizione"] = df["Descrizione"].astype(str).str.strip()
    if "Isin" in df.columns:
        df["Isin"] = df["Isin"].astype(str).str.strip()
    return df
