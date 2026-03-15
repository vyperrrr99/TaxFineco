"""
engine_cfd.py
-------------
Calcola il PnL realizzato su strumenti CFD / Futures / Derivati.

Differenze chiave rispetto all'equity:
  - Non esistono "lotti" nel senso fiscale tradizionale
  - Il PnL è il saldo tra aperture e chiusure su ogni strumento
  - Il "Controvalore" in Fineco per i CFD spesso rappresenta il margine
    o il P&L della singola giornata, non il valore nozionale
  - La colonna "Segno" (A/V) indica acquisto/vendita del contratto,
    ma per i CFD short si può aprire con V e chiudere con A
  - In attesa di vedere i dati reali CFD di Fineco, questa implementazione
    usa la logica più robusta: traccia posizione netta e realizza il P&L
    quando la posizione si azzera o si inverte

NOTA: questa classe è costruita per essere facile da correggere
una volta che si vede l'export reale dei CFD da Fineco.
"""

from dataclasses import dataclass, field
import pandas as pd


@dataclass
class PosizioneCFD:
    """Stato di una posizione CFD aperta su un singolo strumento (ISIN o simbolo)."""
    isin: str
    titolo: str
    divisa: str = "EUR"

    # Quantità netta (positiva = long, negativa = short)
    quantita_netta: float = 0.0

    # Costo medio di apertura (sempre positivo, indipendentemente dalla direzione)
    prezzo_medio_apertura: float = 0.0

    # Controvalore totale del lato aperto (in EUR)
    controvalore_apertura_eur: float = 0.0

    # PnL realizzato accumulato su questo strumento (include operazioni chiuse parzialmente)
    pnl_realizzato_eur: float = 0.0

    # Quante unità sono state chiuse (per reporting)
    quantita_chiusa: float = 0.0


@dataclass
class RecordCFD:
    """Un evento di chiusura (totale o parziale) di una posizione CFD."""
    isin: str
    titolo: str
    data_chiusura: pd.Timestamp
    divisa: str
    quantita_chiusa: float
    direzione_chiusura: str          # "LONG_CLOSE" | "SHORT_CLOSE"
    prezzo_medio_apertura: float
    prezzo_chiusura: float
    pnl_eur: float                   # positivo = guadagno, negativo = perdita
    controvalore_apertura_eur: float
    controvalore_chiusura_eur: float


class EngineCFD:
    """
    Processa la movimentazione CFD e calcola il PnL realizzato.

    Logica di matching:
      - Ogni ISIN ha una posizione netta (long/short).
      - Un trade nella stessa direzione della posizione = apertura/ampliamento.
      - Un trade nella direzione opposta = chiusura (parziale o totale).
      - Se la posizione si inverte, la parte che chiude genera P&L,
        la parte eccedente apre una nuova posizione nella direzione opposta.

    Compatibilità Fineco:
      - Colonna "Segno": A = acquisto contratto, V = vendita contratto
      - Per CFD long: A apre, V chiude
      - Per CFD short: V apre, A chiude
      - Il "Controvalore" è usato come controvalore della transazione in EUR
    """

    def __init__(self, anno_imposta: int = None, epsilon: float = 1e-6):
        self.anno_imposta = anno_imposta  # used only for backward-compat / CLI display
        self.epsilon = epsilon

        self._posizioni: dict[str, PosizioneCFD] = {}
        self.records: list[RecordCFD] = []
        self.warnings: list[str] = []
        self.info_log: list[str] = []

    def processa(self, df: pd.DataFrame) -> "EngineCFD":
        """Elabora il DataFrame CFD riga per riga in ordine cronologico."""
        for _, row in df.iterrows():
            try:
                self._processa_riga(row)
            except Exception as e:
                data_str = row["Data valuta"].strftime("%d/%m/%Y") if pd.notna(row.get("Data valuta")) else "N/D"
                self.warnings.append(f"[CFD][{data_str}] Errore su {row.get('Titolo', '?')}: {e}")

        return self

    def _processa_riga(self, row: pd.Series):
        isin = str(row.get("Isin", "")).strip()
        titolo = str(row.get("Titolo", "")).strip()
        divisa = str(row.get("Divisa", "EUR")).strip()
        data = row["Data valuta"]
        segno = str(row.get("Segno", "")).strip().upper()
        quantita = abs(float(row.get("Quantita", 0)))
        prezzo = float(row.get("Prezzo", 0))
        controvalore_eur = abs(float(row.get("Controvalore", 0)))

        if quantita < self.epsilon:
            return

        # Converti segno in delta quantità netta
        # A = acquisto = +quantità  /  V = vendita = -quantità
        delta = quantita if segno == "A" else -quantita

        if isin not in self._posizioni:
            self._posizioni[isin] = PosizioneCFD(isin=isin, titolo=titolo, divisa=divisa)

        pos = self._posizioni[isin]
        qta_prima = pos.quantita_netta

        # Determina se questa riga è apertura o chiusura (o mix)
        if qta_prima == 0:
            # Nuova posizione
            self._apri_posizione(pos, delta, prezzo, controvalore_eur)

        elif (qta_prima > 0 and delta > 0) or (qta_prima < 0 and delta < 0):
            # Stessa direzione: ampliamento della posizione
            self._amplia_posizione(pos, delta, prezzo, controvalore_eur)

        elif (qta_prima > 0 and delta < 0) or (qta_prima < 0 and delta > 0):
            # Direzione opposta: chiusura (parziale o totale, o inversione)
            self._chiudi_posizione(pos, delta, prezzo, controvalore_eur, data, isin, titolo, divisa)

    def _apri_posizione(self, pos: PosizioneCFD, delta: float, prezzo: float, controvalore_eur: float):
        pos.quantita_netta = delta
        pos.prezzo_medio_apertura = prezzo
        pos.controvalore_apertura_eur = controvalore_eur
        self.info_log.append(
            f"CFD aperto: {pos.titolo} ({pos.isin}) "
            f"{'LONG' if delta > 0 else 'SHORT'} {abs(delta):.2f} @ {prezzo:.4f}"
        )

    def _amplia_posizione(self, pos: PosizioneCFD, delta: float, prezzo: float, controvalore_eur: float):
        """Media il prezzo di carico sulla quantità aggiuntiva."""
        qta_vecchia = abs(pos.quantita_netta)
        qta_nuova = abs(delta)
        qta_totale = qta_vecchia + qta_nuova

        # Prezzo medio ponderato
        pos.prezzo_medio_apertura = (
            (pos.prezzo_medio_apertura * qta_vecchia + prezzo * qta_nuova) / qta_totale
        )
        pos.controvalore_apertura_eur += controvalore_eur
        pos.quantita_netta += delta

    def _chiudi_posizione(
        self, pos: PosizioneCFD, delta: float, prezzo_chiusura: float,
        controvalore_chiusura_eur: float, data: pd.Timestamp,
        isin: str, titolo: str, divisa: str,
    ):
        """
        Gestisce la chiusura parziale, totale o l'inversione.
        """
        qta_aperta = abs(pos.quantita_netta)
        qta_chiesta = abs(delta)
        direzione_pos = "LONG" if pos.quantita_netta > 0 else "SHORT"

        # Quantità effettivamente chiusa in questa operazione
        qta_chiusa = min(qta_aperta, qta_chiesta)

        # PnL per la parte chiusa
        if direzione_pos == "LONG":
            # Long: guadagno se prezzo_chiusura > prezzo_apertura
            pnl_per_unita = prezzo_chiusura - pos.prezzo_medio_apertura
        else:
            # Short: guadagno se prezzo_chiusura < prezzo_apertura
            pnl_per_unita = pos.prezzo_medio_apertura - prezzo_chiusura

        pnl_eur = pnl_per_unita * qta_chiusa

        # Controvalore proporzionale dell'apertura per la parte chiusa
        controvalore_apertura_chiusa = (qta_chiusa / qta_aperta) * pos.controvalore_apertura_eur

        # Registra sempre (filtro anno applicato a valle in risultati_dataframe/totali)
        self.records.append(RecordCFD(
            isin=isin,
            titolo=titolo,
            data_chiusura=data,
            divisa=divisa,
            quantita_chiusa=qta_chiusa,
            direzione_chiusura=f"{direzione_pos}_CLOSE",
            prezzo_medio_apertura=pos.prezzo_medio_apertura,
            prezzo_chiusura=prezzo_chiusura,
            pnl_eur=pnl_eur,
            controvalore_apertura_eur=controvalore_apertura_chiusa,
            controvalore_chiusura_eur=(qta_chiusa / qta_chiesta) * controvalore_chiusura_eur,
        ))

        pos.pnl_realizzato_eur += pnl_eur
        pos.quantita_chiusa += qta_chiusa

        # Aggiorna la posizione residua
        if qta_chiesta >= qta_aperta - self.epsilon:
            # Posizione chiusa interamente
            qta_eccedente = qta_chiesta - qta_aperta
            pos.quantita_netta = 0.0
            pos.controvalore_apertura_eur = 0.0

            # Se c'è eccedente, apre una posizione inversa
            if qta_eccedente > self.epsilon:
                nuovo_delta = -delta / abs(delta) * qta_eccedente  # direzione opposta
                controvalore_eccedente = (qta_eccedente / qta_chiesta) * controvalore_chiusura_eur
                self._apri_posizione(pos, nuovo_delta, prezzo_chiusura, controvalore_eccedente)
        else:
            # Chiusura parziale: scala controvalore proporzionalmente
            pos.quantita_netta += delta
            pos.controvalore_apertura_eur *= (1 - qta_chiusa / qta_aperta)

    # ----------------------------------------------------------
    # Output
    # ----------------------------------------------------------

    def risultati_dataframe(self, anno: int = None) -> pd.DataFrame:
        """Converte i records in DataFrame. Se anno è specificato, filtra per quell'anno."""
        if not self.records:
            return pd.DataFrame()

        records_filtrati = [
            r for r in self.records
            if anno is None or r.data_chiusura.year == anno
        ]
        if not records_filtrati:
            return pd.DataFrame()

        return pd.DataFrame([{
            "Anno": r.data_chiusura.year,
            "ISIN": r.isin,
            "Titolo": r.titolo,
            "Data Chiusura": r.data_chiusura.strftime("%d/%m/%Y"),
            "Direzione": r.direzione_chiusura,
            "Quantità Chiusa": r.quantita_chiusa,
            "Divisa": r.divisa,
            "Prezzo Apertura Medio": round(r.prezzo_medio_apertura, 4),
            "Prezzo Chiusura": round(r.prezzo_chiusura, 4),
            "Controvalore Apertura (€)": round(r.controvalore_apertura_eur, 2),
            "Controvalore Chiusura (€)": round(r.controvalore_chiusura_eur, 2),
            "PnL (€)": round(r.pnl_eur, 2),
        } for r in records_filtrati])

    def totali(self, anno: int = None) -> dict:
        """Restituisce i totali aggregati. Se anno è specificato, filtra per quell'anno."""
        if not self.records:
            return {"pnl_totale_eur": 0.0, "n_operazioni": 0}
        df = self.risultati_dataframe(anno=anno)
        if df.empty:
            return {"pnl_totale_eur": 0.0, "n_operazioni": 0}
        return {
            "pnl_totale_eur": df["PnL (€)"].sum(),
            "n_operazioni": len(df),
            "posizioni_aperte": sum(
                1 for p in self._posizioni.values() if abs(p.quantita_netta) > self.epsilon
            ),
        }

    def anni_disponibili(self) -> list[int]:
        """Restituisce la lista degli anni con almeno una chiusura CFD registrata."""
        if not self.records:
            return []
        return sorted({r.data_chiusura.year for r in self.records})

    def posizioni_aperte(self) -> pd.DataFrame:
        """Snapshot delle posizioni CFD ancora aperte a fine elaborazione."""
        righe = [
            {
                "ISIN": p.isin,
                "Titolo": p.titolo,
                "Direzione": "LONG" if p.quantita_netta > 0 else "SHORT",
                "Quantità Netta": round(p.quantita_netta, 4),
                "Prezzo Medio Apertura": round(p.prezzo_medio_apertura, 4),
                "Divisa": p.divisa,
            }
            for p in self._posizioni.values()
            if abs(p.quantita_netta) > self.epsilon
        ]
        return pd.DataFrame(righe)
