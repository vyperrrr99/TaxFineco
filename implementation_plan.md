# DB Storico Movimentazione — Implementation Plan

## Descrizione

Il sistema attuale richiede di ricaricare ogni volta l'intero file Excel multi-anno. L'obiettivo è:

1. **Persistenza locale** — salvare tutte le movimentazioni in un CSV storico (`data/movimentazione_storico.csv`)
2. **Deduplicazione automatica** — ogni riga è identificata univocamente da una chiave composita; i duplicati vengono scartati silenziosamente
3. **Caricamento automatico all'avvio** — l'app parte dallo storico già salvato, senza bisogno di caricare il file ogni volta
4. **Upload incrementale** — si può caricare un file con la movimentazione degli ultimi giorni/mesi e lo storico si aggiorna automaticamente
5. **Compatibilità** — supporto per i due formati Excel Fineco rilevati:
   - **Formato A** (attuale, header a riga 0): `Movimentazione azionaria 2018-2025.xlsx`
   - **Formato B** (nuovo, header a riga 5 con meta-info Fineco): `Esempio file fineco.xlsx`

---

## Chiave di deduplicazione

Ogni riga di movimentazione sarà identificata da:

| Campo | Motivazione |
|---|---|
| `Data valuta` | Data effettiva dell'operazione |
| `Isin` | Strumento finanziario |
| `Descrizione` | Tipo operazione |
| `Segno` | Acquisto / Vendita |
| `Quantita` | Quantità |
| `Controvalore` | Importo (arrotondato a 2 decimali) |

Se tutti questi campi coincidono, la riga è considerata duplicata e scartata.

> [!NOTE]
> Un caso edge teorico: due operazioni identiche nello stesso giorno sullo stesso titolo allo stesso prezzo. Questo è estremamente raro ma possibile. Se si verifica, il sistema mostrerà un avviso nell'UI. Come comportamento di default, **conserviamo la prima occorrenza**.

---

## Proposed Changes

### Loader ([core/loader.py](file:///c:/App%20AI/Fineco%20PL%20&%20Tax%20Calculator/core/loader.py))

#### [MODIFY] [loader.py](file:///c:/App%20AI/Fineco%20PL%20%26%20Tax%20Calculator/core/loader.py)

- Aggiungere funzione `_detect_header_row(raw_df)` che individua automaticamente la riga con gli header Fineco (cerca la riga contenente `"Data valuta"`)
- Aggiornare [load_excel()](file:///c:/App%20AI/Fineco%20PL%20&%20Tax%20Calculator/core/loader.py#25-112) per gestire entrambi i formati (con e senza `skiprows`)

---

### Store (`core/store.py`) — [NEW]

#### [NEW] [store.py](file:///c:/App%20AI/Fineco%20PL%20%26%20Tax%20Calculator/core/store.py)

Nuovo modulo che gestisce la persistenza del CSV storico:

```python
STORICO_PATH = Path("data/movimentazione_storico.csv")
CHIAVE_DEDUP = ["Data valuta", "Isin", "Descrizione", "Segno", "Quantita", "Controvalore"]

def load_storico() -> pd.DataFrame        # carica il CSV, ritorna df vuoto se non esiste
def save_storico(df: pd.DataFrame)        # salva il CSV ordinato cronologicamente  
def merge_storico(storico, nuovo) -> dict # merge con deduplicazione, ritorna stats
```

Il `merge_storico` restituisce:
```python
{
    "df": pd.DataFrame,     # storico aggiornato
    "n_nuove": int,         # righe effettivamente aggiunte
    "n_duplicate": int,     # righe scartate perché già presenti
}
```

---

### App ([app.py](file:///c:/App%20AI/Fineco%20PL%20&%20Tax%20Calculator/app.py))

#### [MODIFY] [app.py](file:///c:/App%20AI/Fineco%20PL%20%26%20Tax%20Calculator/app.py)

**Flusso dati rivisto:**

```
[Avvio app]
  └─► load_storico() → se esiste CSV → elabora direttamente
  └─► se CSV vuoto → mostra "nessun dato storico, carica un file"

[Caricamento nuovo file]
  └─► load_excel(nuovo_file)
  └─► merge_storico(storico, nuovo)
  └─► se n_nuove > 0 → save_storico() → rielabora tutto
  └─► mostra feedback: "X nuove righe aggiunte, Y duplicate scartate"
```

**UI Changes:**
- Sidebar: aggiungere sezione "📂 Gestione dati" con:
  - Stato storico: data ultima operazione, numero righe totali, periodo coperto
  - Uploader per caricare nuovi file incrementali
  - Pulsante "🗑️ Reset storico" (con conferma) per ricominciare da zero
- Il file uploader attuale **rimane** per compatibilità con il vecchio flusso (caricamento diretto senza salvataggio)

> [!IMPORTANT]
> Il file `Movimentazione azionaria 2018-2025.xlsx` rimane utilizzabile: può essere caricato tramite il nuovo uploader e verrà aggiunto allo storico come qualsiasi altro file. Questo permette di testare la deduplicazione caricando prima il file completo, poi i file annuali uno per uno.

> [!WARNING]
> **Cambio di flusso cache**: la funzione `@st.cache_data` attuale usa `file_bytes` come chiave di cache. Con il DB storico, la chiave di cache deve essere basata sul contenuto del CSV (es. hash MD5 o timestamp ultima modifica). Questo verrà aggiornato per garantire che la UI si aggiorni correttamente dopo ogni nuovo upload.

---

### Struttura file finale

```
c:\App AI\Fineco PL & Tax Calculator\
├── data/
│   └── movimentazione_storico.csv   ← NEW (creato automaticamente)
├── core/
│   ├── loader.py                    ← MODIFY (rilevamento formato automatico)
│   ├── store.py                     ← NEW
│   ├── engine_equity.py
│   ├── engine_cfd.py
│   └── report.py
├── app.py                           ← MODIFY
└── ...
```

---

## Verification Plan

### 1. Test unitario store.py
```powershell
cd "c:\App AI\Fineco PL & Tax Calculator"
python -m pytest tests/test_store.py -v
```
_(I test verranno scritti come parte dell'implementazione)_

Test da coprire:
- `load_storico()` su file inesistente → DataFrame vuoto
- `merge_storico()` con righe identiche → 0 nuove, N duplicate
- `merge_storico()` con righe nuove → N nuove, 0 duplicate
- `merge_storico()` con mix → conteggi corretti, ordinamento cronologico preservato

### 2. Test formato Excel
```powershell
cd "c:\App AI\Fineco PL & Tax Calculator"
python -c "
from core.loader import load_config, load_excel
config = load_config()

# Formato A (header a riga 0)
with open('Movimentazione azionaria 2018-2025.xlsx', 'rb') as f:
    df_a = load_excel(f.read(), config)
print('Formato A:', len(df_a), 'righe')

# Formato B (header a riga 5)
with open('Esempio file fineco.xlsx', 'rb') as f:
    df_b = load_excel(f.read(), config)
print('Formato B:', len(df_b), 'righe')
"
```

### 3. Test deduplicazione end-to-end (manuale)
1. Avviare l'app: `python -m streamlit run app.py`
2. Caricare `Movimentazione azionaria 2018-2025.xlsx` → verificare che lo storico riporti il numero di righe caricato e il periodo 2018–2025
3. Ricaricare lo stesso file → verificare che il messaggio indichi **0 nuove righe, N duplicate**
4. Caricare `Esempio file fineco.xlsx` → verificare che vengano aggiunte solo le righe non già presenti nello storico
5. Confrontare i totali dell'anno 2025 prima e dopo → devono essere identici (le righe del 2025 già presenti non devono essere duplicate)

### 4. Test caricamento file annuali sequenziali
1. Reset storico (pulsante nell'UI)
2. Caricare i file annuali 2018, 2019, ... 2025 uno per uno
3. Dopo ogni caricamento verificare che i KPI del rispettivo anno corrispondano a quelli ottenuti con il file completo
4. Confronto finale: i KPI di tutti gli anni con lo storico completo devono essere identici a quelli ottenuti caricando `Movimentazione azionaria 2018-2025.xlsx` in un colpo solo
