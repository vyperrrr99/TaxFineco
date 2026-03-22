# Contesto sviluppo — Calcolo Tasse Fineco

> Ultimo aggiornamento: 2026-03-16
> Branch: `master` (ahead of origin by 2 commits)
> Ultimo commit: `92c2b8c` — feat: storico multi-anno con filtro Equity / CFD / Totale

---

## Scopo dell'app

Applicazione Streamlit per il calcolo delle plusvalenze/minusvalenze ai fini
fiscali italiani (Quadro RT) a partire dai file Excel esportati da Fineco.
Supporta due tipologie di file:

1. **Movimentazione titoli** (equity: azioni, ETF, bond, ETC) → calcolo LIFO/CMP
2. **Movimentazione conto** (CFD, futures, derivati) → PnL da Entrate/Uscite

---

## Architettura file

```
app.py                        # Interfaccia Streamlit principale
config.yaml                   # Configurazione (colonne attese, metodi, alias check anno)
core/
  loader.py                   # load_excel() per titoli, load_excel_conto() per conto
  engine_equity.py            # Engine LIFO/CMP per equity
  engine_conto.py             # Engine PnL CFD da movimentazione conto
  engine_cfd.py               # (legacy — non usato nel flusso principale)
  report.py                   # calcola_quadro_rt(), esporta_excel()
  store.py                    # Storico persistente movimentazione titoli (CSV)
  store_conto.py              # Storico persistente movimentazione conto (CSV)
  isin_alias.py               # Gestione alias ISIN per operazioni societarie
data/
  movimentazione_storico.csv  # Storico titoli (dedup automatico)
  storico_conto.csv           # Storico conto/CFD (dedup automatico)
  isin_alias.json             # Mapping alias ISIN { sell_isin: buy_isin } + ignored[]
output/                       # File Excel generati dall'export
```

---

## Formato file Fineco

### Movimentazione titoli (equity)
- Header rilevato automaticamente (cerca "Data valuta" o "Operazione" nelle prime righe)
- Colonna data: usa **"Operazione"** (trade date), NON "Data valuta" (settlement date)
  - Se entrambe presenti: rinomina "Operazione" → "Data valuta", droppa l'originale "Data valuta"
- Colonne chiave: `Data valuta`, `Isin`, `Descrizione operazione`, `Quantita`, `Prezzo`, `Controvalore`
- Segno operazione: da `Descrizione operazione` (contiene "acquisto" o "vendita")
- Due formati supportati: vecchio (senza commissioni) e nuovo (con colonna commissioni)

### Movimentazione conto (CFD/Derivati)
- Preamble di 12 righe con info conto → header alla riga 12
- Colonne con underscores (`Data_Operazione`, `Data_Valuta`) → normalizzate a spazi
- Rename: `Data Operazione` → `Data valuta` (con drop di `Data Valuta` se presente)
- **Entrate**: numeri positivi (guadagni entrati nel conto)
- **Uscite**: numeri NEGATIVI (costi/perdite usciti dal conto) ← CRITICO
- Formula PnL corretta: `PnL = Entrate + Uscite` (somma algebrica, NON Entrate - Uscite)
- Dedup key: `["Data valuta", "Descrizione Completa", "Entrate", "Uscite"]`

---

## Engine CFD (engine_conto.py)

### Filtro descrizioni (da config.yaml → `descrizioni_cfd_conto`)
```yaml
descrizioni_cfd_conto:
  - "Margine di variazione derivati"
  - "Oneri CFD"
  - "Proventi Societari CFD Long"
  - "Oneri Societari CFD Short"
```
NON inclusi (esclusi di proposito): "Margini di garanzia derivati" (depositi collaterale)

### Estrazione strumento da `Descrizione Completa`
- Strip prefissi operazione (es. "Oneri su derivati ", "Margine di variazione derivati ")
- Strip codici contratto: regex `\b[A-Z]{2}[0-9]{10,}\b` (copre CD... e CX...)
- Strip codici mese-anno: regex `\b(JAN|FEB|...|DEC)\d{2}\b`
- Strip modificatori: "SUPER ", "USA ", "MINI "
- Strumenti estratti verificati: PETROLIO, NASDAQ, GAS NATURALE, EURUSD,
  ITALY FTSEMIB, RUSSELL 2000, RHEINMETALL, BOOKING HOLD, BITCOIN,
  ALPHABET, EXXON MOBIL, META PLATFORMS, NVIDIA

### Classificazione tipo
- "Margine variazione" → righe con "Margine di variazione" in Descrizione Completa
- "Oneri/Proventi" → tutto il resto (Oneri CFD, Proventi, ecc.)

### Output `processa_conto()`
DataFrame con colonne: `Anno`, `Strumento`, `Tipo`, `PnL (€)`
(aggregato per Anno × Strumento × Tipo)

---

## Alias ISIN (isin_alias.py)

Gestisce il caso in cui un titolo cambia ISIN a seguito di operazioni societarie
(fusioni, scissioni, cambio denominazione). Permette di associare l'ISIN di vendita
all'ISIN di acquisto per il calcolo corretto del costo di carico.

- Fuzzy matching titolo (difflib SequenceMatcher) per suggerire candidati
- UI: form con due colonne (vendite orfane | acquisti candidati per ISIN)
- Salvataggio permanente in `data/isin_alias.json`
- Applicato PRIMA di classifica_operazioni() in elabora_tutto()
- Anno minimo controllo orfani: `isin_alias_check_from_anno: 2025` (config.yaml)

---

## Tab dell'app

### Tab 1 — Anno Selezionato
- Selectbox anno
- 4 KPI: Plus/Minus Equity | Plus/Minus CFD | Totale (RT23) | Imposta 26%
- Expander commissioni (solo formato nuovo Fineco)
- Expander Quadro RT dettaglio (RT21–RT27)
- Bar chart per titolo equity
- Tabella dettaglio operazioni equity (con filtri)
- Expander CFD riepilogo per strumento
- Portafoglio equity residuo
- Warnings acquisti mancanti
- Export Excel

### Tab 2 — Storico Multi-Anno ← MODIFICATO ULTIMO
- **Radio selector**: 🏦 Equity | 📉 CFD / Derivati | 📊 Totale (Equity + CFD)
  - Le opzioni CFD e Totale appaiono solo se il file conto è caricato
  - Tutti i widget (KPI, bar chart, line chart, tabella) reagiscono alla selezione
- Select slider range anni
- 4 KPI: Equity periodo | CFD periodo | Totale periodo | Anni nel range
- Bar chart annuale (colonna dinamica in base alla selezione)
- Line chart cumulato (colonna dinamica)
- Tabella con tutte e tre le colonne (Equity, CFD, Totale) se dati CFD presenti

### Tab 3 — CFD / Derivati
- Selectbox anno
- 3 KPI: Margine variazione | Oneri/Proventi | Totale PnL CFD
- Bar chart orizzontale per strumento
- Tabella dettaglio per strumento
- Storico multi-anno CFD (bar chart + tabella)

---

## Commit history (ultimi significativi)

```
92c2b8c  feat: storico multi-anno con filtro Equity / CFD / Totale
2994b63  fix: formula PnL CFD corretta (Entrate + Uscite, non Entrate - Uscite)
8f558b4  fix: loader conto e raggruppamento strumenti CFD
bca3542  feat: calcolo PnL CFD/Derivati da movimentazione conto Fineco
64045ce  fix: usa data operazione (trade date) come riferimento e migliora UI alias ISIN
6b1da4d  feat: alias ISIN per operazioni societarie con fuzzy matching
380955b  feat: escludi futures/CFD dal calcolo Quadro RT
e1f3367  fix: correggi dedup storico vuoto e PnL CFD
a7dcf93  fix: supporto formato nativo Fineco senza colonne derivate + commissioni
ffcf8bd  feat: implementa DB storico movimentazioni con deduplicazione automatica
62623a3  Checkpoint stabile (tag: stable)
```

---

## Stato branch

- `master`: sviluppo attivo (HEAD = 92c2b8c), ahead of origin/master by 2
- `stable` (62623a3): checkpoint precedente all'introduzione dello storico DB
- **Da fare**: push di master su origin quando opportuno

---

## Bug noti / TODO potenziali (non ancora richiesti)

- Il cumulato nel Tab 2 usa cumsum() sull'intervallo filtrato (non su tutto lo storico):
  se si filtra un sotto-range, il cumulato parte da 0 invece che dal valore precedente.
  Comportamento attuale probabilmente accettabile ma da verificare con l'utente.
- `engine_cfd.py` è legacy e non viene usato nel flusso principale; potrebbe essere rimosso.
- `debug_check.py` nella root è untracked — probabilmente file temporaneo di debug.
- I 2 commit in ahead of origin non sono ancora stati pushati.

---

## Come avviare l'app

```bash
cd "C:\App AI\Fineco PL & Tax Calculator"
streamlit run app.py --server.port 8501
```
URL: http://localhost:8501
