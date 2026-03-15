"""
isin_alias.py
-------------
Gestisce il mapping tra ISIN diversi per lo stesso titolo a seguito di
operazioni societarie (cambio nome/codice, scissione, fusione, ecc.).

Problema tipico:
  Titolo acquistato come "EUTELSAT COMM."  ISIN FR0010221234
  Titolo venduto   come "EUTELSAT DIR 9DC25" ISIN FR0014012K95
  → Il motore equity non trova la posizione perché gli ISIN non coincidono.

Soluzione:
  1. Rilevamento automatico delle "vendite orfane" (ISIN di vendita non presente
     come acquisto) limitato alle operazioni da isin_alias_check_from_anno in poi.
  2. Suggerimento di corrispondenze per similarità del nome (fuzzy match).
  3. Conferma utente → salvataggio in data/isin_alias.json.
  4. Prima dell'elaborazione, viene applicato il remapping ISIN nel DataFrame.

Struttura del file JSON salvato:
  {
    "alias":   {"ISIN_VENDITA": "ISIN_ACQUISTO", ...},
    "ignored": ["ISIN_DA_NON_MAPPARE", ...]
  }
"""

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd


ALIAS_PATH = Path("data/isin_alias.json")

# Parole "rumore" comuni nei nomi di titoli — escluse dal confronto fuzzy
_NOISE_WORDS = {
    "SA", "SPA", "NV", "AG", "SE", "PLC", "LTD", "INC", "CORP", "GROUP",
    "HOLDING", "COMM", "DIR", "ORD", "SHS", "SHR", "NEW", "OLD", "RTS",
    "WTS", "ADR", "GDR", "ETF", "ETP", "FUND", "THE", "AND", "DEL", "DI",
    "EUR", "USD", "GBP",
}


# ============================================================
# Persistenza
# ============================================================

def load_alias_map() -> dict:
    """
    Carica il file JSON degli alias.

    Returns:
        {"alias": {sell_isin: buy_isin, ...}, "ignored": [isin, ...]}
        Se il file non esiste restituisce strutture vuote.
    """
    if not ALIAS_PATH.exists():
        return {"alias": {}, "ignored": []}
    try:
        with open(ALIAS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {
            "alias":   {str(k): str(v) for k, v in data.get("alias", {}).items()},
            "ignored": [str(x) for x in data.get("ignored", [])],
        }
    except (json.JSONDecodeError, OSError):
        return {"alias": {}, "ignored": []}


def save_alias_map(alias: dict, ignored: list) -> None:
    """Salva alias confermati e ISIN ignorati su disco."""
    ALIAS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ALIAS_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"alias": alias, "ignored": ignored},
            f,
            indent=2,
            ensure_ascii=False,
        )


# ============================================================
# Fuzzy matching
# ============================================================

def _normalizza_titolo(title: str) -> list[str]:
    """
    Tokenizza e normalizza un nome di titolo per il confronto fuzzy.
    Rimuove: punteggiatura, parole rumore, token che contengono cifre (codici tipo
    "9DC25", "FR001", "12K95") lasciando solo le parole significative (es. "EUTELSAT").
    """
    t = re.sub(r"[^A-Z0-9 ]", " ", title.upper())
    tokens = t.split()
    return [
        tok for tok in tokens
        if len(tok) >= 3
        and tok not in _NOISE_WORDS
        and not any(c.isdigit() for c in tok)   # scarta token con cifre: 9DC25, FR001…
    ]


def _similarity_score(title_a: str, title_b: str) -> float:
    """
    Punteggio di similarità tra due nomi di titolo (0.0 .. 1.0).

    Formula: 70% Jaccard sui token significativi + 30% SequenceMatcher sul testo.
    Un match perfetto su un singolo token significativo lungo (es. "EUTELSAT")
    produce già un punteggio > 0.5 grazie alla componente Jaccard.
    """
    tok_a = set(_normalizza_titolo(title_a))
    tok_b = set(_normalizza_titolo(title_b))
    if not tok_a or not tok_b:
        return 0.0
    jaccard = len(tok_a & tok_b) / len(tok_a | tok_b)
    seq     = SequenceMatcher(None, title_a.upper(), title_b.upper()).ratio()
    return round(0.7 * jaccard + 0.3 * seq, 3)


# ============================================================
# Rilevamento orfani e suggerimenti
# ============================================================

def trova_orphan_sells(
    df_equity: pd.DataFrame,
    anno_from: int = 2025,
    alias_map: dict | None = None,
    ignored: list | None = None,
) -> pd.DataFrame:
    """
    Trova vendite (Segno==V) a partire da anno_from il cui ISIN non compare
    mai come acquisto (Segno==A) in tutto il dataset.

    Esclude automaticamente ISIN già mappati o già ignorati dall'utente.

    Args:
        df_equity:  DataFrame equity classificato (Segno, Isin, Titolo, Data valuta).
        anno_from:  Anno dal quale applicare il controllo (operazioni precedenti escluse).
        alias_map:  Alias già confermati — questi ISIN non vengono più mostrati.
        ignored:    ISIN già ignorati — non vengono più mostrati.

    Returns:
        DataFrame [Isin, Titolo, Data valuta], una riga per ISIN unico orfano.
    """
    alias_map = alias_map or {}
    ignored   = ignored   or []

    if df_equity.empty:
        return pd.DataFrame(columns=["Isin", "Titolo", "Data valuta"])

    isin_acquistati = set(
        df_equity[df_equity["Segno"].str.upper() == "A"]["Isin"]
        .dropna()
        .astype(str)
        .unique()
    )

    df_sells = df_equity[
        (df_equity["Segno"].str.upper() == "V") &
        (df_equity["Data valuta"].dt.year >= anno_from)
    ].copy()

    orfani = df_sells[
        ~df_sells["Isin"].astype(str).isin(isin_acquistati)
        & ~df_sells["Isin"].astype(str).isin(alias_map.keys())
        & ~df_sells["Isin"].astype(str).isin(ignored)
    ]

    return (
        orfani
        .sort_values("Data valuta")
        .drop_duplicates(subset=["Isin"])
        [["Isin", "Titolo", "Data valuta"]]
        .reset_index(drop=True)
    )


def suggerisci_alias(
    orphan_isin: str,
    orphan_title: str,
    df_equity: pd.DataFrame,
    min_score: float = 0.20,
    max_suggerimenti: int = 3,
) -> list[dict]:
    """
    Per un ISIN di vendita orfano, suggerisce ISIN di acquisto con titolo simile.

    Args:
        orphan_isin:      ISIN della vendita orfana (solo per escludersi da sé).
        orphan_title:     Nome del titolo venduto (es. "EUTELSAT DIR 9DC25").
        df_equity:        DataFrame equity per cercare i candidati acquisti.
        min_score:        Soglia minima di similarità (0..1).
        max_suggerimenti: Numero massimo di suggerimenti da restituire.

    Returns:
        Lista ordinata per score decrescente:
        [{"isin_acquisto": ..., "titolo_acquisto": ..., "score": 0..1}, ...]
    """
    if df_equity.empty:
        return []

    buy_rows = (
        df_equity[df_equity["Segno"].str.upper() == "A"]
        [["Isin", "Titolo"]]
        .drop_duplicates("Isin")
        .dropna(subset=["Isin", "Titolo"])
    )

    risultati = []
    for _, row in buy_rows.iterrows():
        buy_isin = str(row["Isin"])
        if buy_isin == orphan_isin:
            continue
        score = _similarity_score(orphan_title, str(row["Titolo"]))
        if score >= min_score:
            risultati.append({
                "isin_acquisto":    buy_isin,
                "titolo_acquisto":  str(row["Titolo"]),
                "score":            score,
            })

    return sorted(risultati, key=lambda x: -x["score"])[:max_suggerimenti]
