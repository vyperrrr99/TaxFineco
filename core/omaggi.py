"""
omaggi.py
---------
Gestisce la persistenza delle conferme utente per le assegnazioni gratuite
(vendite con quantità mancante in portafoglio).

Struttura del file JSON:
[
  {
    "isin": "IT0005674368",
    "titolo": "LEONARDO S 5X",
    "data_vendita": "2026-04-10",
    "quantita_mancante": 500.0
  }
]
"""

import json
from pathlib import Path


OMAGGI_PATH = Path("data/omaggi_confermati.json")


def load_omaggi() -> list[dict]:
    """
    Carica il file JSON degli omaggi confermati.
    Restituisce una lista di dizionari.
    """
    if not OMAGGI_PATH.exists():
        return []
    try:
        with open(OMAGGI_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except (json.JSONDecodeError, OSError):
        return []


def save_omaggi(omaggi_list: list[dict]) -> None:
    """
    Salva la lista di omaggi confermati su disco.
    """
    OMAGGI_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OMAGGI_PATH, "w", encoding="utf-8") as f:
        json.dump(
            omaggi_list,
            f,
            indent=2,
            ensure_ascii=False,
        )


def check_omaggio_action(isin: str, data_vendita: str, quantita_mancante: float, omaggi_list: list[dict], epsilon: float = 1e-4) -> str | None:
    """
    Verifica se uno specifico trade anomalo è già stato approvato dall'utente e restituisce l'azione:
    'costo_zero' oppure 'ignora'. Restituisce None se non è presente.
    La data_vendita deve essere passata in formato stringa YYYY-MM-DD.
    """
    for o in omaggi_list:
        if o.get("isin") == isin and o.get("data_vendita") == data_vendita:
            if abs(float(o.get("quantita_mancante", 0.0)) - quantita_mancante) < epsilon:
                return o.get("azione", "costo_zero")
    return None

def is_omaggio_confermato(isin: str, data_vendita: str, quantita_mancante: float, omaggi_list: list[dict], epsilon: float = 1e-4) -> bool:
    """
    Backward compatibility per quando l'azione era solo approvazione a costo zero.
    """
    return check_omaggio_action(isin, data_vendita, quantita_mancante, omaggi_list, epsilon) == "costo_zero"

def is_omaggio_ignorato(isin: str, data_vendita: str, quantita_mancante: float, omaggi_list: list[dict], epsilon: float = 1e-4) -> bool:
    """
    Verifica se l'utente ha scelto di ignorare questa vendita.
    """
    return check_omaggio_action(isin, data_vendita, quantita_mancante, omaggi_list, epsilon) == "ignora"
