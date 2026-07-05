"""
classificazione.py
------------------
Gestisce la persistenza e le euristiche per classificare un Titolo come
"Trading" o "Investing".

Struttura del file JSON salvato:
  {
    "NOME TITOLO": "Trading",
    "ALTRO TITOLO": "Investing"
  }
"""

import json
import re
from pathlib import Path

import pandas as pd

CLASSIFICAZIONE_PATH = Path("data/titoli_classificazione.json")

def load_classificazioni() -> dict:
    """
    Carica il file JSON delle classificazioni.

    Returns:
        {"Titolo": "Trading" | "Investing", ...}
    """
    if not CLASSIFICAZIONE_PATH.exists():
        return {}
    try:
        with open(CLASSIFICAZIONE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError):
        return {}


def save_classificazioni(mappa: dict) -> None:
    """Salva le classificazioni confermate su disco."""
    CLASSIFICAZIONE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CLASSIFICAZIONE_PATH, "w", encoding="utf-8") as f:
        json.dump(
            mappa,
            f,
            indent=2,
            ensure_ascii=False,
        )


def suggerisci_classificazione(titolo: str, df_equity: pd.DataFrame) -> str:
    """
    Euristica per proporre "Trading" o "Investing" per un titolo sconosciuto.
    
    1. Certificati a leva (es. GOLD L 7X) -> Trading
    2. Operazioni chiuse rapidamente (max_data - min_data <= 28 gg) -> Trading
    3. Altrimenti -> Investing
    """
    # 1. Regex per Lev Certificates (L/S seguito da numero e X, es. "L 7X", "S 5X")
    if re.search(r'\b[LS]\s*\d+X\b', titolo, re.IGNORECASE):
        return "Trading"
    
    # 2. Controllo rapido tempistiche di trades
    if not df_equity.empty:
        df_titolo = df_equity[df_equity["Titolo"] == titolo]
        if not df_titolo.empty:
            min_date = df_titolo["Data valuta"].min()
            max_date = df_titolo["Data valuta"].max()
            if pd.notna(min_date) and pd.notna(max_date):
                diff_days = (max_date - min_date).days
                # Se è stato comprato e venduto tutto entro 28 giorni, è un trade rapido
                if diff_days <= 28:
                    return "Trading"
                    
    # 3. Default per roba tenuta a lungo (o solo comprata finora ma tenuta più di 28gg)
    return "Investing"

