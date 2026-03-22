import pandas as pd
from core.loader import load_excel, load_config
from core.store import merge_storico, _normalizza_chiavi
import pickle

# Carica il file caricato dall'utente
config = load_config()
df_base = load_excel("Esempio file fineco.xlsx", config)

# Simula save_storico e load_storico
df_base.to_csv("data/debug_storico.csv", index=False)
storico = pd.read_csv("data/debug_storico.csv", parse_dates=["Data valuta"])

# Adesso carica di nuovo lo stesso file
df_nuovo = load_excel("Esempio file fineco.xlsx", config)

# Compara i tipi 
print("--- Types STORICO ---")
print(storico.dtypes)
print("\n--- Types NUOVO ---")
print(df_nuovo.dtypes)

res = merge_storico(storico, df_nuovo)
print("\nMerge result:", res["n_nuove"], "nuove", res["n_duplicate"], "duplicate")

if res["n_nuove"] > 0:
    print("\nEsempio nuove righe aggiunte:")
    stor_len = len(storico)
    df_res = res["df"]
    
    # Perché non si sono deduplicate? Guardiamo la prima che non si è deduplicata
    st_norm = _normalizza_chiavi(storico.copy())
    nw_norm = _normalizza_chiavi(df_nuovo.copy())
    
    print("\nStorico norm row 0:")
    print("Segno:", repr(st_norm.iloc[0].get("Segno")))
    print("\nNuovo norm row 0:")
    print("Segno:", repr(nw_norm.iloc[0].get("Segno")))

