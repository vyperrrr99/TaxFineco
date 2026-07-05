import pytest
import pandas as pd
from datetime import datetime

from core.engine_equity import EngineEquity


def create_mock_equity_df(records: list[dict]) -> pd.DataFrame:
    """Helper to build a DataFrame in the format expected by EngineEquity."""
    df = pd.DataFrame(records)
    if "Data valuta" in df.columns:
        df["Data valuta"] = pd.to_datetime(df["Data valuta"])
    if "Divisa" not in df.columns:
        df["Divisa"] = "EUR"
    if "Descrizione" not in df.columns:
        df["Descrizione"] = "Compravendita titoli"
    return df


def test_acquisto_vendita_cmp_lifo():
    """Testa un ciclo semplice di acquisto e vendita."""
    df = create_mock_equity_df([
        {
            "Data valuta": "2026-01-10",
            "Segno": "A",
            "Titolo": "TITOLO A",
            "Isin": "IT0001",
            "Quantita": 100,
            "Prezzo": 10.0,
            "Controvalore": 1000.0,
        },
        {
            "Data valuta": "2026-02-15",
            "Segno": "V",
            "Titolo": "TITOLO A",
            "Isin": "IT0001",
            "Quantita": 50,
            "Prezzo": 15.0,
            "Controvalore": 750.0,
        }
    ])
    
    engine = EngineEquity()
    engine.processa(df)
    
    res = engine.risultati_dataframe()
    assert not res.empty
    assert len(res) == 1
    
    row = res.iloc[0]
    assert row["Titolo"] == "TITOLO A"
    assert row["Quantità Venduta"] == 50
    assert row["Controvalore Vendita (€)"] == 750.0
    
    # Costo di carico dovrebbe essere 50 * 10 = 500
    assert row["Costo Carico (€) CMP"] == 500.0
    assert row["Costo Carico (€) LIFO"] == 500.0
    
    # Plusvalenza dovrebbe essere 750 - 500 = 250
    assert row["Plus/Minus (€) CMP"] == 250.0
    assert row["Plus/Minus (€) LIFO"] == 250.0


def test_quantita_mancante_non_approvata():
    """Testa che una quantità mancante venga messa in pending_omaggi."""
    df = create_mock_equity_df([
        {
            "Data valuta": "2026-03-01",
            "Segno": "A",
            "Titolo": "TITOLO B",
            "Isin": "IT0002",
            "Quantita": 50,
            "Prezzo": 10.0,
            "Controvalore": 500.0,
        },
        {
            "Data valuta": "2026-04-01",
            "Segno": "V",
            "Titolo": "TITOLO B",
            "Isin": "IT0002",
            "Quantita": 100,  # Vendo 100 ma ne ho solo 50
            "Prezzo": 20.0,
            "Controvalore": 2000.0,
        }
    ])
    
    engine = EngineEquity()
    engine.processa(df)
    
    # Essendo un omaggio NON approvato, il trade viene sospeso.
    # Quindi non dovrebbe esserci alcun risultato definitivo.
    res = engine.risultati_dataframe()
    assert res.empty
    
    # Deve esserci 1 operazione in pending
    assert len(engine.pending_omaggi) == 1
    pending = engine.pending_omaggi[0]
    assert pending["isin"] == "IT0002"
    assert pending["quantita_mancante"] == 50.0


def test_quantita_mancante_approvata():
    """Testa che un omaggio approvato proceda con costo zero."""
    omaggi_confermati = [
        {
            "isin": "IT0002",
            "data_vendita": "2026-04-01",
            "quantita_mancante": 50.0
        }
    ]
    
    df = create_mock_equity_df([
        {
            "Data valuta": "2026-03-01",
            "Segno": "A",
            "Titolo": "TITOLO B",
            "Isin": "IT0002",
            "Quantita": 50,
            "Prezzo": 10.0,
            "Controvalore": 500.0,
        },
        {
            "Data valuta": "2026-04-01",
            "Segno": "V",
            "Titolo": "TITOLO B",
            "Isin": "IT0002",
            "Quantita": 100,  # Vendo 100 ma ne ho solo 50
            "Prezzo": 20.0,
            "Controvalore": 2000.0,
        }
    ])
    
    engine = EngineEquity(omaggi_confermati=omaggi_confermati)
    engine.processa(df)
    
    # Trade completato perché approvato
    assert len(engine.pending_omaggi) == 0
    res = engine.risultati_dataframe()
    assert not res.empty
    
    row = res.iloc[0]
    # Costo: le 50 comprate a 10 + 50 a costo 0 = 500
    assert row["Costo Carico (€) CMP"] == 500.0
    # LIFO: l'ultimo lotto aggiunto è l'omaggio a costo 0. Quindi consuma l'omaggio (0€) e poi il lotto a 10€.
    assert row["Costo Carico (€) LIFO"] == 500.0
