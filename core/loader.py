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


def _detect_header_row(source: Union[str, Path, bytes, io.BytesIO]) -> int:
    """
    Rileva automaticamente la riga degli header nel file Excel Fineco.

    Cerca la prima riga (tra le prime 10) che contiene la stringa "Data valuta".
    Supporta:
      - Formato A: header a riga 0  (Movimentazione storica standard)
      - Formato B: header a riga 5  (nuovo export con meta-info Fineco in testa)

    Returns:
        Indice 0-based della riga da usare come header (da passare a skiprows).
        Default 0 se non trovato.
    """
    try:
        if isinstance(source, (str, Path)):
            raw = pd.read_excel(source, header=None, nrows=10)
        elif isinstance(source, bytes):
            raw = pd.read_excel(io.BytesIO(source), header=None, nrows=10)
        elif isinstance(source, io.BytesIO):
            pos = source.tell()
            raw = pd.read_excel(source, header=None, nrows=10)
            source.seek(pos)
        else:
            return 0
    except Exception:
        return 0

    for i, row in raw.iterrows():
        row_str = row.astype(str)
        if (
            row_str.str.contains("Operazione", case=False, na=False).any()
            or row_str.str.contains("Data valuta", case=False, na=False).any()
        ):
            return int(i)

    return 0


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
    # --- Rilevamento formato (Formato A: header riga 0, Formato B: header riga 5) ---
    header_row = _detect_header_row(source)

    # --- Lettura grezza ---
    if isinstance(source, (str, Path)):
        raw_df = pd.read_excel(source, skiprows=header_row)
    elif isinstance(source, bytes):
        raw_df = pd.read_excel(io.BytesIO(source), skiprows=header_row)
    elif isinstance(source, io.BytesIO):
        source.seek(0)
        raw_df = pd.read_excel(source, skiprows=header_row)
    else:
        raise TypeError(f"Tipo sorgente non supportato: {type(source)}")

    # --- Colonna data: usa "Operazione" (data trade) se presente,
    #     altrimenti "Data valuta" (data regolamento) come fallback.
    #     Il rename avviene prima della validazione così colonne_attese
    #     continua a richiedere "Data valuta" senza dover modificare config.
    if "Operazione" in raw_df.columns:
        # Il file nuovo ha già una colonna "Data valuta" (regolamento):
        # la rimuoviamo prima di rinominare "Operazione" (trade date) al suo posto.
        if "Data valuta" in raw_df.columns:
            raw_df = raw_df.drop(columns=["Data valuta"])
        raw_df = raw_df.rename(columns={"Operazione": "Data valuta"})

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

    # Colonne numeriche obbligatorie: rimuove separatori migliaia e converte
    colonne_numeriche = ["Quantita", "Prezzo", "Cambio", "Controvalore"]
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

    # Colonne commissioni/spese (presenti solo nel nuovo formato Fineco):
    # rilevate dinamicamente e convertite a numerico; NaN → 0
    colonne_commissioni = [
        c for c in df.columns
        if any(kw in c.lower() for kw in ["commissioni", "commissione", "spese"])
    ]
    for col in colonne_commissioni:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

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


def _detect_header_row_conto(source: Union[str, Path, bytes, io.BytesIO]) -> int:
    """
    Rileva la riga degli header nel file Excel della movimentazione conto Fineco.
    Cerca la prima riga (tra le prime 20) che contiene "Entrate" o "Uscite".
    Il file Fineco ha un preambolo di ~12 righe prima degli header effettivi.
    """
    try:
        if isinstance(source, (str, Path)):
            raw = pd.read_excel(source, header=None, nrows=20)
        elif isinstance(source, bytes):
            raw = pd.read_excel(io.BytesIO(source), header=None, nrows=20)
        elif isinstance(source, io.BytesIO):
            pos = source.tell()
            raw = pd.read_excel(source, header=None, nrows=20)
            source.seek(pos)
        else:
            return 0
    except Exception:
        return 0

    for i, row in raw.iterrows():
        row_str = row.astype(str)
        if (
            row_str.str.contains(r"\bEntrate\b", case=False, na=False, regex=True).any()
            or row_str.str.contains(r"\bUscite\b", case=False, na=False, regex=True).any()
        ):
            return int(i)

    return 0


def load_excel_conto(
    source: Union[str, Path, bytes, io.BytesIO],
    config: dict,
) -> pd.DataFrame:
    """
    Carica il file Excel della movimentazione del conto Fineco.

    Il file ha colonne diverse dal file movimentazione titoli:
    contiene Entrate, Uscite, Descrizione, Descrizione Completa.
    Usato per estrarre PnL di CFD/Derivati (margini, oneri, proventi).

    Args:
        source: path al file oppure bytes/BytesIO.
        config: dizionario di configurazione da config.yaml.

    Returns:
        DataFrame pulito con Data valuta, Descrizione, Descrizione Completa,
        Entrate, Uscite e tutte le altre colonne presenti.

    Raises:
        ValueError: se mancano colonne obbligatorie.
    """
    header_row = _detect_header_row_conto(source)

    # --- Lettura grezza ---
    if isinstance(source, (str, Path)):
        raw_df = pd.read_excel(source, skiprows=header_row)
    elif isinstance(source, bytes):
        raw_df = pd.read_excel(io.BytesIO(source), skiprows=header_row)
    elif isinstance(source, io.BytesIO):
        source.seek(0)
        raw_df = pd.read_excel(source, skiprows=header_row)
    else:
        raise TypeError(f"Tipo sorgente non supportato: {type(source)}")

    # --- Normalizza nomi colonne: underscore → spazio ---
    # Il file Fineco conto usa Data_Operazione, Data_Valuta, Descrizione_Completa
    raw_df.columns = [str(c).replace("_", " ").strip() for c in raw_df.columns]

    # --- Colonna data: usa "Data Operazione" (trade date) → "Data valuta" ---
    if "Data Operazione" in raw_df.columns:
        if "Data Valuta" in raw_df.columns:
            raw_df = raw_df.drop(columns=["Data Valuta"])
        raw_df = raw_df.rename(columns={"Data Operazione": "Data valuta"})
    elif "Data Valuta" in raw_df.columns:
        raw_df = raw_df.rename(columns={"Data Valuta": "Data valuta"})
    elif "Operazione" in raw_df.columns:
        if "Data valuta" in raw_df.columns:
            raw_df = raw_df.drop(columns=["Data valuta"])
        raw_df = raw_df.rename(columns={"Operazione": "Data valuta"})

    # --- Validazione colonne ---
    colonne_attese = config.get("colonne_conto_attese", [])
    mancanti = [c for c in colonne_attese if c not in raw_df.columns]
    if mancanti:
        raise ValueError(
            f"Colonne mancanti nel file conto: {', '.join(mancanti)}\n"
            f"Colonne trovate: {', '.join(raw_df.columns.tolist())}"
        )

    df = raw_df.copy()

    # --- Conversione tipi ---
    df["Data valuta"] = pd.to_datetime(df["Data valuta"], dayfirst=True, errors="coerce")

    for col in ["Entrate", "Uscite"]:
        if col in df.columns:
            if df[col].dtype == object:
                df[col] = (
                    df[col]
                    .astype(str)
                    .str.replace(r"[^\d.\-]", "", regex=True)
                )
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # --- Pulizia ---
    df.dropna(subset=["Data valuta"], inplace=True)

    for col in ["Descrizione", "Descrizione Completa"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # --- Ordinamento cronologico ---
    df.sort_values(by="Data valuta", ascending=True, inplace=True)
    df.reset_index(drop=True, inplace=True)

    print(f"[loader_conto] Caricate {len(df)} righe")
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
