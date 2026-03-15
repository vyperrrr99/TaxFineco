# Calcolo Tasse Fineco

Strumento per calcolare plusvalenze e minusvalenze dalla movimentazione azionaria Fineco.

## Setup

```bash
# 1. Crea virtualenv (consigliato)
python -m venv .venv
source .venv/bin/activate        # Linux/Mac
.venv\Scripts\activate           # Windows

# 2. Installa dipendenze
pip install -r requirements.txt

# 3. (Opzionale) Configura config.yaml con il tuo anno di imposta
```

## Uso — CLI

```bash
# Calcolo base
python main.py --file "Movimentazione 2018-2025.xlsx" --anno 2025

# Con minusvalenze pregresse
python main.py --file data/mov.xlsx --anno 2025 --minus-pregresse 1500.00

# Con metodo CMP invece di LIFO
python main.py --file data/mov.xlsx --anno 2025 --metodo CMP
```

## Uso — Streamlit (interfaccia grafica)

```bash
streamlit run app.py
```

Poi apri il browser su `http://localhost:8501`.

## Struttura progetto

```
calcolo_tasse/
├── app.py              # Interfaccia Streamlit
├── main.py             # CLI
├── config.yaml         # Configurazione (anno, colonne, ecc.)
├── requirements.txt
├── core/
│   ├── loader.py       # Caricamento e validazione Excel
│   ├── engine_equity.py # Calcolo CMP + LIFO per equity
│   ├── engine_cfd.py   # Calcolo PnL per CFD/Futures
│   └── report.py       # Quadro RT + export Excel
├── data/               # Metti qui i file Excel Fineco
└── output/             # File Excel risultati (generati automaticamente)
```

## Aggiornamento dati

Ogni volta che hai una nuova movimentazione da Fineco:
1. Scarica il file Excel aggiornato da Fineco (coprendo tutti gli anni dall'apertura del conto)
2. Caricalo nella UI Streamlit oppure passalo via `--file` da CLI
3. Il calcolo viene rieseguito da zero sull'intero storico

## Nota sui CFD

Il riconoscimento delle operazioni CFD dipende dalla colonna "Descrizione" 
nel tuo Excel Fineco. Verifica le descrizioni esatte nel tuo file e aggiorna 
`operazioni_cfd` in `config.yaml` se necessario.

## Limiti noti

- Le vendite precedenti alla data di inizio del file Excel vengono saltate
  (warning a console/UI). Per risolvere, usa sempre il file che copre l'intera
  storia del conto.
- Il calcolo del Quadro RT è indicativo: verifica sempre con un commercialista.
