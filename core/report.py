"""
report.py
---------
Aggregazione risultati, calcolo Quadro RT e export Excel.
"""

from pathlib import Path
from datetime import datetime
import pandas as pd


def calcola_quadro_rt(
    totali_equity: dict,
    totali_cfd: dict,
    minusvalenze_pregresse: float = 0.0,
    metodo: str = "LIFO",  # "LIFO" | "CMP"
) -> dict:
    """
    Calcola i righi RT21–RT27 per la dichiarazione dei redditi (Quadro RT).

    I CFD rientrano nei "redditi diversi" assieme alle plusvalenze equity,
    quindi si sommano ai fini del calcolo fiscale.

    Args:
        totali_equity: dizionario restituito da EngineEquity.totali()
        totali_cfd:    dizionario restituito da EngineCFD.totali()
        minusvalenze_pregresse: da rigo RT27 dell'anno precedente
        metodo: quale metodo usare per l'equity ("LIFO" o "CMP")

    Returns:
        Dizionario con tutti i righi RT.
    """
    chiave_costi = f"costi_eur_{metodo.lower()}"
    chiave_plus = f"plus_minus_eur_{metodo.lower()}"

    # RT21: corrispettivi totali (equity + equivalente CFD)
    rt21 = totali_equity.get("corrispettivi_eur", 0.0) + abs(
        totali_cfd.get("pnl_totale_eur", 0.0)
        + totali_equity.get(chiave_costi, 0.0)
    )
    # Semplificato: per i CFD usiamo direttamente il PnL netto
    # perché il "controvalore" non è comparabile a quello equity
    rt21_equity = totali_equity.get("corrispettivi_eur", 0.0)
    rt22_equity = totali_equity.get(chiave_costi, 0.0)

    # Plus/minus equity
    plus_minus_equity = totali_equity.get(chiave_plus, 0.0)

    # Plus/minus CFD (già netto)
    plus_minus_cfd = totali_cfd.get("pnl_totale_eur", 0.0)

    # RT23: risultato complessivo dell'anno
    rt23 = plus_minus_equity + plus_minus_cfd

    # Distinzione plus/minus anno
    plusvalenza_anno = max(rt23, 0.0)
    minusvalenza_anno = abs(min(rt23, 0.0))

    # RT25: imponibile (plusvalenze - minus pregresse utilizzate)
    minus_utilizzate = min(plusvalenza_anno, minusvalenze_pregresse)
    rt25 = max(plusvalenza_anno - minus_utilizzate, 0.0)

    # RT26: imposta sostitutiva 26%
    rt26 = rt25 * 0.26

    # RT27: minusvalenze residue da riportare
    minus_pregresse_residue = minusvalenze_pregresse - minus_utilizzate
    rt27 = minusvalenza_anno + minus_pregresse_residue

    return {
        "metodo": metodo,
        "rt21_corrispettivi_equity": round(rt21_equity, 2),
        "rt22_costi_equity": round(rt22_equity, 2),
        "pnl_cfd": round(plus_minus_cfd, 2),
        "plus_minus_equity": round(plus_minus_equity, 2),
        "rt23_plus_minus_anno": round(rt23, 2),
        "rt24_minus_pregresse": round(minusvalenze_pregresse, 2),
        "minus_utilizzate": round(minus_utilizzate, 2),
        "rt25_imponibile": round(rt25, 2),
        "rt26_imposta": round(rt26, 2),
        "rt27_da_riportare": round(rt27, 2),
    }


def stampa_quadro_rt(rt: dict, anno: int):
    """Stampa il riepilogo Quadro RT a console."""
    print(f"\n{'='*52}")
    print(f"  QUADRO RT — Anno {anno} (metodo {rt['metodo']})")
    print(f"{'='*52}")
    print(f"  Rigo RT21 (Corrispettivi equity):    {rt['rt21_corrispettivi_equity']:>12,.2f} €")
    print(f"  Rigo RT22 (Costi equity):            {rt['rt22_costi_equity']:>12,.2f} €")
    print(f"  PnL CFD netto:                       {rt['pnl_cfd']:>12,.2f} €")
    print(f"  {'─'*46}")
    print(f"  Rigo RT23 (Plus/Minus anno):         {rt['rt23_plus_minus_anno']:>12,.2f} €")
    print(f"  Rigo RT24 (Minus pregresse):         {rt['rt24_minus_pregresse']:>12,.2f} €")
    print(f"  {'─'*46}")
    print(f"  Rigo RT25 (Imponibile):              {rt['rt25_imponibile']:>12,.2f} €")
    print(f"{'='*52}")
    print(f"  Rigo RT26 (Imposta 26%):          ►  {rt['rt26_imposta']:>12,.2f} €")
    print(f"{'='*52}")
    print(f"  Rigo RT27 (Minus da riportare):      {rt['rt27_da_riportare']:>12,.2f} €")
    print(f"{'─'*52}\n")


def esporta_excel(
    df_equity: pd.DataFrame,
    df_cfd: pd.DataFrame,
    quadro_rt: dict,
    anno: int,
    output_folder: str = "output",
) -> Path:
    """
    Esporta tutti i risultati in un file Excel multi-foglio.

    Fogli prodotti:
      1. Equity — dettaglio operazioni
      2. CFD — dettaglio operazioni
      3. Quadro_RT — riepilogo fiscale
      4. Info — metadata (data elaborazione, file sorgente, ecc.)

    Returns:
        Path del file creato.
    """
    folder = Path(output_folder)
    folder.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = folder / f"CalcoloTasse_{anno}_{timestamp}.xlsx"

    with pd.ExcelWriter(filename, engine="openpyxl") as writer:

        # --- Foglio 1: Equity ---
        if not df_equity.empty:
            df_equity.to_excel(writer, sheet_name="Equity", index=False)
            _formatta_foglio(writer, "Equity", df_equity)
        else:
            pd.DataFrame({"Info": ["Nessuna operazione equity nell'anno"]}).to_excel(
                writer, sheet_name="Equity", index=False
            )

        # --- Foglio 2: CFD ---
        if not df_cfd.empty:
            df_cfd.to_excel(writer, sheet_name="CFD", index=False)
            _formatta_foglio(writer, "CFD", df_cfd)
        else:
            pd.DataFrame({"Info": ["Nessuna operazione CFD nell'anno"]}).to_excel(
                writer, sheet_name="CFD", index=False
            )

        # --- Foglio 3: Quadro RT ---
        rt_data = [
            {"Rigo": "RT21", "Descrizione": "Corrispettivi vendite equity", "Importo (€)": quadro_rt["rt21_corrispettivi_equity"]},
            {"Rigo": "RT22", "Descrizione": "Costi di acquisto equity", "Importo (€)": quadro_rt["rt22_costi_equity"]},
            {"Rigo": "–",    "Descrizione": "PnL CFD netto", "Importo (€)": quadro_rt["pnl_cfd"]},
            {"Rigo": "RT23", "Descrizione": "Plusvalenza/Minusvalenza anno", "Importo (€)": quadro_rt["rt23_plus_minus_anno"]},
            {"Rigo": "RT24", "Descrizione": "Minusvalenze pregresse", "Importo (€)": quadro_rt["rt24_minus_pregresse"]},
            {"Rigo": "RT25", "Descrizione": "Imponibile netto", "Importo (€)": quadro_rt["rt25_imponibile"]},
            {"Rigo": "RT26", "Descrizione": "Imposta sostitutiva (26%)", "Importo (€)": quadro_rt["rt26_imposta"]},
            {"Rigo": "RT27", "Descrizione": "Minusvalenze da riportare", "Importo (€)": quadro_rt["rt27_da_riportare"]},
        ]
        pd.DataFrame(rt_data).to_excel(writer, sheet_name="Quadro_RT", index=False)

        # --- Foglio 4: Info ---
        info_data = [
            {"Chiave": "Anno di imposta", "Valore": anno},
            {"Chiave": "Metodo", "Valore": quadro_rt["metodo"]},
            {"Chiave": "Data elaborazione", "Valore": datetime.now().strftime("%d/%m/%Y %H:%M")},
            {"Chiave": "File output", "Valore": str(filename.name)},
        ]
        pd.DataFrame(info_data).to_excel(writer, sheet_name="Info", index=False)

    print(f"[report] File salvato: {filename}")
    return filename


def _formatta_foglio(writer: pd.ExcelWriter, nome_foglio: str, df: pd.DataFrame):
    """Auto-adatta la larghezza delle colonne."""
    ws = writer.sheets[nome_foglio]
    for col_idx, col_name in enumerate(df.columns, 1):
        max_len = max(
            len(str(col_name)),
            df[col_name].astype(str).map(len).max() if not df.empty else 0,
        )
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(max_len + 3, 40)
