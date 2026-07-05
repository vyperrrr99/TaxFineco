"""
tests/test_store.py
-------------------
Unit test per core/store.py

Esecuzione:
    cd "c:\\App AI\\Fineco PL & Tax Calculator"
    python -m pytest tests/test_store.py -v
"""

import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch

from core.store import load_storico, save_storico, merge_storico, CHIAVE_DEDUP_BASE


# ============================================================
# Helpers
# ============================================================

def _row(
    data="2025-01-15",
    isin="IT0001234567",
    descrizione="Compravendita titoli",
    segno="A",
    quantita=100.0,
    controvalore=1500.00,
    titolo="TestTitolo",
    **extra,
) -> dict:
    """Crea una riga di movimentazione con valori di default."""
    row = {
        "Data valuta": pd.Timestamp(data),
        "Isin": isin,
        "Descrizione": descrizione,
        "Segno": segno,
        "Quantita": quantita,
        "Controvalore": controvalore,
        "Titolo": titolo,
    }
    row.update(extra)
    return row


def _df(*rows) -> pd.DataFrame:
    """Crea un DataFrame da una lista di dict-riga."""
    return pd.DataFrame(list(rows))


# ============================================================
# load_storico
# ============================================================

class TestLoadStorico:
    def test_file_inesistente_ritorna_dataframe_vuoto(self, tmp_path):
        path = tmp_path / "storico.csv"
        with patch("core.store.STORICO_PATH", path):
            df = load_storico()
        assert df.empty

    def test_carica_csv_esistente(self, tmp_path):
        path = tmp_path / "storico.csv"
        df_orig = _df(_row())
        df_orig.to_csv(path, index=False)
        with patch("core.store.STORICO_PATH", path):
            df = load_storico()
        assert len(df) == 1

    def test_data_valuta_parsata_come_datetime(self, tmp_path):
        path = tmp_path / "storico.csv"
        _df(_row()).to_csv(path, index=False)
        with patch("core.store.STORICO_PATH", path):
            df = load_storico()
        assert pd.api.types.is_datetime64_any_dtype(df["Data valuta"])


# ============================================================
# save_storico
# ============================================================

class TestSaveStorico:
    def test_crea_cartella_se_non_esiste(self, tmp_path):
        path = tmp_path / "data" / "storico.csv"
        with patch("core.store.STORICO_PATH", path):
            save_storico(_df(_row()))
        assert path.exists()

    def test_salva_ordinato_cronologicamente(self, tmp_path):
        path = tmp_path / "storico.csv"
        df = _df(
            _row(data="2025-03-01"),
            _row(data="2025-01-01", isin="IT0009999999"),
        )
        with patch("core.store.STORICO_PATH", path):
            save_storico(df)
            df_loaded = load_storico()
        dates = df_loaded["Data valuta"].tolist()
        assert dates == sorted(dates)


# ============================================================
# merge_storico
# ============================================================

class TestMergeStorico:

    def test_storico_vuoto_ritorna_tutto_nuovo(self):
        df_storico = pd.DataFrame()
        df_nuovo = _df(_row())
        result = merge_storico(df_storico, df_nuovo)
        assert result["n_nuove"] == 1
        assert result["n_duplicate"] == 0
        assert len(result["df"]) == 1

    def test_righe_identiche_zero_nuove(self):
        df_base = _df(_row())
        result = merge_storico(df_base, df_base.copy())
        assert result["n_nuove"] == 0
        assert result["n_duplicate"] == 1

    def test_righe_diverse_aggiunge_nuove(self):
        df_storico = _df(_row())
        df_nuovo = _df(_row(isin="IT0009999999", titolo="Altro"))
        result = merge_storico(df_storico, df_nuovo)
        assert result["n_nuove"] == 1
        assert result["n_duplicate"] == 0
        assert len(result["df"]) == 2

    def test_mix_nuove_e_duplicate(self):
        df_storico = _df(_row())
        df_nuovo = _df(
            _row(),                                          # duplicata
            _row(isin="IT0009999999", titolo="Altro"),       # nuova
        )
        result = merge_storico(df_storico, df_nuovo)
        assert result["n_nuove"] == 1
        assert result["n_duplicate"] == 1
        assert len(result["df"]) == 2

    def test_ordinamento_cronologico_nel_merge(self):
        df_storico = _df(_row(data="2025-03-01"))
        df_nuovo = _df(_row(data="2025-01-01", isin="IT0009999999", titolo="Early"))
        result = merge_storico(df_storico, df_nuovo)
        dates = result["df"]["Data valuta"].tolist()
        assert dates == sorted(dates)

    def test_controvalore_arrotondamento_non_crea_falsi_nuovi(self):
        """
        Due righe con Controvalore che differisce solo per floating point
        devono essere considerate duplicate.
        """
        df_storico = _df(_row(controvalore=1500.00))
        df_nuovo = _df(_row(controvalore=1500.004))  # differenza <0.01 → round a 1500.00
        result = merge_storico(df_storico, df_nuovo)
        assert result["n_nuove"] == 0
        assert result["n_duplicate"] == 1

    def test_stesso_isin_acquisto_e_vendita_sono_distinti(self):
        """Righe con stesso ISIN ma Segno diverso (A vs V) non sono duplicate."""
        df_storico = _df(_row(segno="A"))
        df_nuovo = _df(_row(segno="V"))
        result = merge_storico(df_storico, df_nuovo)
        assert result["n_nuove"] == 1
        assert result["n_duplicate"] == 0

    def test_caricamento_multiplo_stesso_file_idempotente(self):
        """Caricare lo stesso file 3 volte non accumula duplicati."""
        df = _df(_row(), _row(isin="IT0002222222", titolo="Titolo2"))
        result1 = merge_storico(pd.DataFrame(), df)
        result2 = merge_storico(result1["df"], df)
        result3 = merge_storico(result2["df"], df)
        # Dopo il primo caricamento: 2 righe; i successivi non aggiungono nulla
        assert len(result1["df"]) == 2
        assert len(result2["df"]) == 2
        assert len(result3["df"]) == 2
        assert result2["n_nuove"] == 0
        assert result3["n_nuove"] == 0

    def test_storico_vuoto_deduplicazione_interna_file_sorgente(self):
        """
        Nel nuovo comportamento, le righe identiche all'interno dello stesso file
        sorgente vengono considerate transazioni intraday valide e mantenute.
        """
        riga = _row()
        df_con_dup = _df(riga, riga)  # stesso record due volte

        result1 = merge_storico(pd.DataFrame(), df_con_dup)
        # Entrambe le righe vengono mantenute
        assert len(result1["df"]) == 2
        assert result1["n_nuove"] == 2
        assert result1["n_duplicate"] == 0

        # Il secondo caricamento dello STESSO file non deve trovare nuove righe
        result2 = merge_storico(result1["df"], df_con_dup)
        assert len(result2["df"]) == 2
        assert result2["n_nuove"] == 0
