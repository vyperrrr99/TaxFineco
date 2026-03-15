"""
main.py
-------
Entrypoint CLI per eseguire il calcolo da terminale IDE.
Uso:
    python main.py --file "Movimentazione 2018-2025.xlsx" --anno 2025
    python main.py --file data/mov.xlsx --anno 2025 --minus-pregresse 1500.00
"""

import argparse
import sys
from pathlib import Path

from core.loader import load_config, load_excel, classifica_operazioni
from core.engine_equity import EngineEquity
from core.engine_cfd import EngineCFD
from core.report import calcola_quadro_rt, stampa_quadro_rt, esporta_excel


def main():
    parser = argparse.ArgumentParser(
        description="Calcolo plusvalenze/minusvalenze da movimentazione Fineco"
    )
    parser.add_argument(
        "--file", "-f", required=True,
        help="Path al file Excel di movimentazione (es. data/movimentazione.xlsx)"
    )
    parser.add_argument(
        "--anno", "-a", type=int, default=None,
        help="Anno di imposta (default: dalla config.yaml)"
    )
    parser.add_argument(
        "--minus-pregresse", "-m", type=float, default=0.0,
        help="Minusvalenze pregresse da RT27 anno precedente (default: 0)"
    )
    parser.add_argument(
        "--metodo", choices=["LIFO", "CMP"], default="LIFO",
        help="Metodo per il calcolo del costo di carico equity (default: LIFO)"
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path al file di configurazione (default: config.yaml)"
    )
    parser.add_argument(
        "--no-export", action="store_true",
        help="Non salvare il file Excel di output"
    )

    args = parser.parse_args()

    # --- Caricamento configurazione ---
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"ERRORE: {e}")
        sys.exit(1)

    anno = args.anno or config.get("anno_imposta", 2025)
    output_folder = config.get("output_folder", "output")

    print(f"\n{'─'*52}")
    print(f"  Calcolo Tasse Fineco — Anno {anno}")
    print(f"{'─'*52}")

    # --- Caricamento Excel ---
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"ERRORE: File non trovato: {file_path}")
        sys.exit(1)

    print(f"\n[1/4] Caricamento file: {file_path.name}")
    try:
        df = load_excel(file_path, config)
    except (ValueError, Exception) as e:
        print(f"ERRORE nel caricamento: {e}")
        sys.exit(1)

    # --- Classificazione operazioni ---
    print(f"\n[2/4] Classificazione operazioni...")
    dataset = classifica_operazioni(df, config)

    # --- Engine Equity ---
    print(f"\n[3/4] Calcolo equity (metodo {args.metodo})...")
    engine_eq = EngineEquity(anno_imposta=anno)
    engine_eq.processa(dataset["equity"])

    # Stampa warnings equity (storico incompleto, ecc.)
    if engine_eq.warnings:
        print(f"\n  ⚠  {len(engine_eq.warnings)} operazioni con storico incompleto (vendite precedenti al file):")
        for w in engine_eq.warnings[:5]:  # mostra solo le prime 5
            print(f"     {w}")
        if len(engine_eq.warnings) > 5:
            print(f"     ... e altre {len(engine_eq.warnings) - 5} (vedi log completo)")

    if engine_eq.info_log:
        for msg in engine_eq.info_log:
            print(f"  ℹ  {msg}")

    df_equity_risultati = engine_eq.risultati_dataframe()
    totali_eq = engine_eq.totali()

    print(f"\n  Equity {anno}: {len(df_equity_risultati)} operazioni di vendita")
    print(f"  Plus/Minus ({args.metodo}): {totali_eq.get(f'plus_minus_eur_{args.metodo.lower()}', 0):+,.2f} €")

    # --- Engine CFD ---
    print(f"\n[3b/4] Calcolo CFD...")
    engine_cfd = EngineCFD(anno_imposta=anno)
    if not dataset["cfd"].empty:
        engine_cfd.processa(dataset["cfd"])
        df_cfd_risultati = engine_cfd.risultati_dataframe()
        totali_cfd = engine_cfd.totali()
        print(f"  CFD {anno}: {totali_cfd.get('n_operazioni', 0)} chiusure | PnL: {totali_cfd.get('pnl_totale_eur', 0):+,.2f} €")

        pos_aperte = engine_cfd.posizioni_aperte()
        if not pos_aperte.empty:
            print(f"  ⚠  {len(pos_aperte)} posizioni CFD ancora aperte a fine elaborazione")
    else:
        df_cfd_risultati = __import__("pandas").DataFrame()
        totali_cfd = {"pnl_totale_eur": 0.0, "n_operazioni": 0}
        print("  Nessuna operazione CFD trovata nel file.")
        print("  (Se hai CFD, verifica le voci 'operazioni_cfd' in config.yaml)")

    # --- Quadro RT ---
    print(f"\n[4/4] Calcolo Quadro RT...")
    rt = calcola_quadro_rt(
        totali_equity=totali_eq,
        totali_cfd=totali_cfd,
        minusvalenze_pregresse=args.minus_pregresse,
        metodo=args.metodo,
    )
    stampa_quadro_rt(rt, anno)

    # --- Export ---
    if not args.no_export:
        output_file = esporta_excel(
            df_equity=df_equity_risultati,
            df_cfd=df_cfd_risultati,
            quadro_rt=rt,
            anno=anno,
            output_folder=output_folder,
        )
        print(f"  ✓ File Excel salvato: {output_file}")

    # Mostra portafoglio residuo
    stato_portafoglio = engine_eq.stato_portafoglio()
    if not stato_portafoglio.empty:
        n = len(stato_portafoglio)
        print(f"\n  Portafoglio equity residuo: {n} posizioni aperte")
        print(stato_portafoglio.to_string(index=False))


if __name__ == "__main__":
    main()
