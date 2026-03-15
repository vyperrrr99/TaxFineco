"""
loader.py
---------
Carica e valida il file Excel esportato da Fineco.
Supporta sia il caricamento da path locale (IDE) che da bytes (Streamlit uploader).
"""

import io
from pathlib import Path
from typing import Union

import pandas as pd
import yaml


def load_config(config_path: str = "config.yaml") -> dict:
    """Carica il file di configurazione YAML."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"File di configurazione non trovato: {config_path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_excel(
    source: Union[str, Path, bytes, io.BytesIO],
    config: dict,
) -> pd.DataFrame:
    """
    Carica il file Excel Fineco e restituisce un DataFrame pulito e ordinato.

    Args:
        source: path al file oppure bytes/BytesIO (da Streamlit uploader).
        config: dizionario di configurazione caricato da config.yaml.

    Returns:
        DataFrame con colonne tipizzate, ordinato cronologicamente.

    Raises:
        ValueError: se mancano colonne obbligatorie.
        Exception: se il file non è leggibile.
    """
    # --- Lettura grezza ---
    if isinstance(source, (str, Path)):
        raw_df = pd.read_excel(source)
    elif isinstance(source, bytes):
        raw_df = pd.read_excel(io.BytesIO(source))
    elif isinstance(source, io.BytesIO):
        raw_df = pd.read_excel(source)
    else:
        raise TypeError(f"Tipo sorgente non supportato: {type(source)}")

    # --- Validazione colonne ---
    colonne_attese = config.get("colonne_attese", [])
    mancanti = [c for c in colonne_attese if c not in raw_df.columns]
    if mancanti:
        raise ValueError(
            f"Colonne mancanti nel file Excel: {', '.join(mancanti)}\n"
            f"Colonne trovate: {', '.join(raw_df.columns.tolist())}"
        )

    df = raw_df.copy()

    # --- Conversione tipi ---
    # Data: gestisce sia formato stringa "dd/mm/yyyy" che già datetime
    df["Data valuta"] = pd.to_datetime(
        df["Data valuta"], dayfirst=True, errors="coerce"
    )

    # Colonne numeriche: rimuove separatori migliaia e converte
    colonne_numeriche = ["Quantita", "Prezzo", "Cambio", "Controvalore", "QTY", "Val Unit €"]
    for col in colonne_numeriche:
        if col in df.columns:
            # Gestisce sia float che stringhe con virgola/punto come separatore
            if df[col].dtype == object:
                df[col] = (
                    df[col]
                    .astype(str)
                    .str.replace(r"[^\d.\-]", "", regex=True)
                )
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # --- Pulizia ---
    righe_prima = len(df)
    df.dropna(subset=["Data valuta"], inplace=True)
    righe_scartate = righe_prima - len(df)

    # Rimuovi righe senza ISIN (intestazioni duplicate, totali, ecc.)
    df = df[df["Isin"].notna() & (df["Isin"].astype(str).str.strip() != "")]

    # Pulisci whitespace nelle colonne stringa
    for col in ["Descrizione", "Titolo", "Isin", "Segno", "Divisa"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # Normalizza Segno: a volte Fineco usa minuscolo o spazi
    df["Segno"] = df["Segno"].str.upper()

    # --- Ordinamento cronologico (fondamentale per LIFO) ---
    df.sort_values(by="Data valuta", ascending=True, inplace=True)
    df.reset_index(drop=True, inplace=True)

    # --- Log sintetico ---
    n_isin = df["Isin"].nunique()
    date_min = df["Data valuta"].min().strftime("%d/%m/%Y") if not df.empty else "N/A"
    date_max = df["Data valuta"].max().strftime("%d/%m/%Y") if not df.empty else "N/A"

    print(f"[loader] Caricate {len(df)} righe ({righe_scartate} scartate per data non valida)")
    print(f"[loader] Periodo: {date_min} → {date_max} | ISIN unici: {n_isin}")

    return df


def classifica_operazioni(df: pd.DataFrame, config: dict) -> dict[str, pd.DataFrame]:
    """
    Suddivide il DataFrame in sotto-dataset per tipo di operazione.

    Returns:
        Dizionario con chiavi 'equity', 'cfd', 'altro'
    """
    operazioni_equity = [s.lower() for s in config.get("operazioni_equity", [])]
    operazioni_cfd = [s.lower() for s in config.get("operazioni_cfd", [])]

    desc_lower = df["Descrizione"].str.lower()

    mask_equity = desc_lower.str.contains("|".join(operazioni_equity), regex=True, na=False)
    mask_cfd = desc_lower.str.contains("|".join(operazioni_cfd), regex=True, na=False)

    df_equity = df[mask_equity].copy()
    df_cfd = df[mask_cfd & ~mask_equity].copy()
    df_altro = df[~mask_equity & ~mask_cfd].copy()

    print(f"[loader] Equity: {len(df_equity)} righe | CFD: {len(df_cfd)} righe | Altro: {len(df_altro)} righe")

    return {
        "equity": df_equity,
        "cfd": df_cfd,
        "altro": df_altro,
    }
