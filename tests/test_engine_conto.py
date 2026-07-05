import pytest
import pandas as pd
from datetime import datetime

from core.engine_conto import processa_conto, riepilogo_per_anno_strumento, totale_pnl_per_anno


def create_mock_conto_df(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if "Data valuta" in df.columns:
        df["Data valuta"] = pd.to_datetime(df["Data valuta"])
    if "Entrate" not in df.columns:
        df["Entrate"] = 0.0
    if "Uscite" not in df.columns:
        df["Uscite"] = 0.0
    return df


def test_processa_conto_cfd_aggregation():
    """Testa che il calcolo dei margini e oneri CFD avvenga correttamente."""
    descrizioni_cfd = ["CFD", "FUTURES"]
    
    df = create_mock_conto_df([
        {
            "Data valuta": "2026-05-10",
            "Descrizione": "Margine Variazione CFD",
            "Entrate": 100.0,
            "Uscite": 0.0,
            "Descrizione Completa": "Margine di variazione derivati CFD_A"
        },
        {
            "Data valuta": "2026-05-11",
            "Descrizione": "Margine Variazione CFD",
            "Entrate": 0.0,
            "Uscite": -50.0,
            "Descrizione Completa": "Margine di variazione derivati CFD_A"
        },
        {
            "Data valuta": "2026-05-12",
            "Descrizione": "Oneri CFD",
            "Entrate": 0.0,
            "Uscite": -10.0,
            "Descrizione Completa": "Oneri su derivati CFD_A"
        },
        {
            "Data valuta": "2026-06-01",
            "Descrizione": "Bonifico in uscita", # Non è CFD
            "Entrate": 0.0,
            "Uscite": -1000.0,
            "Descrizione Completa": "Spese varie"
        }
    ])
    
    processed = processa_conto(df, descrizioni_cfd)
    
    # Bonifico deve essere ignorato
    assert len(processed) == 3
    
    # Verifica che il riepilogo sommi bene
    riepilogo = riepilogo_per_anno_strumento(processed)
    
    assert len(riepilogo) == 1
    row = riepilogo.iloc[0]
    
    assert row["Strumento"] == "CFD_A"
    assert row["Margine variazione (€)"] == 50.0  # 100 - 50
    assert row["Oneri/Proventi (€)"] == -10.0
    assert row["Totale (€)"] == 40.0
    
    
def test_totale_pnl_per_anno():
    """Testa l'estrazione dizionario {anno: pnl_totale}."""
    descrizioni_cfd = ["CFD"]
    df = create_mock_conto_df([
        {
            "Data valuta": "2025-10-01",
            "Descrizione": "Margine Variazione CFD",
            "Entrate": 200.0,
            "Uscite": 0.0,
            "Descrizione Completa": "Margine di variazione derivati Strumento B"
        },
        {
            "Data valuta": "2026-01-01",
            "Descrizione": "Margine Variazione CFD",
            "Entrate": 0.0,
            "Uscite": -100.0,
            "Descrizione Completa": "Margine di variazione derivati Strumento C"
        }
    ])
    processed = processa_conto(df, descrizioni_cfd)
    pnl = totale_pnl_per_anno(processed)
    
    assert pnl.get(2025) == 200.0
    assert pnl.get(2026) == -100.0
