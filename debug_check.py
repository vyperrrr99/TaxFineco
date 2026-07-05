import pandas as pd
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
from core.loader import load_config, load_excel
from core.store import load_storico, STORICO_PATH, CHIAVE_DEDUP, merge_storico, _normalizza_chiavi
from core.engine_cfd import EngineCFD
from core.engine_equity import EngineEquity
from core.loader import classifica_operazioni

config = load_config()
with open('Dati usati per creazione/Movimentazione azionaria 2018-2024.xlsx','rb') as f:
    df_24 = load_excel(f.read(), config)
with open('Dati usati per creazione/Movimentazione azionaria 2025.xlsx','rb') as f:
    df_25_old = load_excel(f.read(), config)
with open('Dati usati per creazione/2025.xlsx','rb') as f:
    df_25_new = load_excel(f.read(), config)

# ============================================================
# TEST 1: merge simulato partendo da storico VUOTO
# ============================================================
print('=== TEST 1: merge da storico vuoto ===')
r1 = merge_storico(pd.DataFrame(), df_24)
print(f'Dopo 2018-2024:  {r1["n_nuove"]} nuove, {r1["n_duplicate"]} dup  (storico: {len(r1["df"])} righe)')
r2 = merge_storico(r1['df'], df_25_old)
print(f'Dopo 2025-old:   {r2["n_nuove"]} nuove, {r2["n_duplicate"]} dup  (storico: {len(r2["df"])} righe)')
r3 = merge_storico(r2['df'], df_25_new)
print(f'Dopo 2025-new:   {r3["n_nuove"]} nuove, {r3["n_duplicate"]} dup  (storico: {len(r3["df"])} righe)')

# ============================================================
# TEST 2: merge simulato partendo dal STORICO ATTUALE (2018-2024)
# ============================================================
print()
storico = load_storico()
print(f'=== TEST 2: merge partendo dallo storico attuale ({len(storico)} righe, {storico["Data valuta"].min().date()} -> {storico["Data valuta"].max().date()}) ===')
r_a = merge_storico(storico, df_25_old)
print(f'Dopo 2025-old:  {r_a["n_nuove"]} nuove, {r_a["n_duplicate"]} dup')
r_b = merge_storico(storico, df_25_new)
print(f'Dopo 2025-new:  {r_b["n_nuove"]} nuove, {r_b["n_duplicate"]} dup')

# ============================================================
# TEST 3: diagnosi CFD - calcolo PnL con prezzo vs controvalore
# ============================================================
print()
print('=== TEST 3: diagnosi CFD (2025-new) ===')
dataset = classifica_operazioni(df_25_new, config)
print(f'Righe CFD nel 2025-new: {len(dataset["cfd"])}')

engine = EngineCFD()
engine.processa(dataset["cfd"])
totali = engine.totali()
print(f'PnL CFD (metodo attuale - prezzo diff): {totali["pnl_totale_eur"]:+,.2f} EUR  ({totali["n_operazioni"]} chiusure)')

# Calcolo PnL basato su Controvalore (metodo corretto per futures)
df_cfd = dataset["cfd"].copy()
print('\nPrime 5 operazioni CFD nel 2025-new:')
print(df_cfd[['Data valuta','Titolo','Segno','Quantita','Prezzo','Cambio','Controvalore']].head(10).to_string())

# Verifica: per FTSEMIB il moltiplicatore è 2
# Controvalore = Prezzo * Quantita * Moltiplicatore
df_cfd['ctv_calcolato'] = df_cfd['Prezzo'] * df_cfd['Quantita'] * df_cfd['Cambio']
df_cfd['ratio_ctv'] = (df_cfd['Controvalore'] / df_cfd['ctv_calcolato']).round(3)
print('\nRatio Controvalore / (Prezzo*Qty*Cambio) per titolo (moltiplicatore contratto):')
print(df_cfd.groupby('Titolo')['ratio_ctv'].agg(['mean','min','max']).to_string())

# ============================================================
# TEST 4: differenze equity 2025-old vs 2025-new
# ============================================================
print()
print('=== TEST 4: equity - differenze tra i due file 2025 ===')
eq_old = dataset_old = classifica_operazioni(df_25_old, config)['equity']
eq_new = classifica_operazioni(df_25_new, config)['equity']
print(f'Righe equity 2025-old: {len(eq_old)}  date: {eq_old["Data valuta"].min().date()} -> {eq_old["Data valuta"].max().date()}')
print(f'Righe equity 2025-new: {len(eq_new)}  date: {eq_new["Data valuta"].min().date()} -> {eq_new["Data valuta"].max().date()}')

# Righe in new ma non in old (per ISIN+data)
s_old = set(zip(eq_old['Data valuta'].astype(str), eq_old['Isin'], eq_old['Segno'], eq_old['Quantita'].round(0).astype(int).astype(str)))
s_new = set(zip(eq_new['Data valuta'].astype(str), eq_new['Isin'], eq_new['Segno'], eq_new['Quantita'].round(0).astype(int).astype(str)))
print(f'In common: {len(s_old & s_new)}  solo in old: {len(s_old - s_new)}  solo in new: {len(s_new - s_old)}')
if s_new - s_old:
    print('Righe solo nel nuovo formato (non nel vecchio):')
    extra = eq_new[eq_new.apply(lambda r: (str(r['Data valuta']), r['Isin'], r['Segno'], str(int(round(r['Quantita'],0)))) in (s_new - s_old), axis=1)]
    print(extra[['Data valuta','Titolo','Isin','Segno','Quantita','Controvalore']].to_string())
