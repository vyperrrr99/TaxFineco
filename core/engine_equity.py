"""
engine_equity.py
----------------
Calcola plusvalenze e minusvalenze per strumenti equity:
azioni, ETF, ETP, obbligazioni, certificates.

Metodi supportati:
  - CMP  (Costo Medio Ponderato) — metodo fiscale italiano vigente
  - LIFO (Last In First Out)     — confronto alternativo

Gestisce:
  - Acquisti / Vendite / Rimborsi
  - Aumenti di capitale (con lotti a costo zero)
  - Strumenti in valuta estera (colonne aggiuntive)
  - Portafoglio con storia pregressa mancante (warning invece di crash)
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


# ============================================================
# Strutture dati
# ============================================================

@dataclass
class LottoLIFO:
    quantita: float
    costo_totale_eur: float
    costo_totale_orig: float

    def costo_unitario_eur(self) -> float:
        if self.quantita < 1e-9:
            return 0.0
        return self.costo_totale_eur / self.quantita

    def costo_unitario_orig(self) -> float:
        if self.quantita < 1e-9:
            return 0.0
        return self.costo_totale_orig / self.quantita


@dataclass
class PosizioneCMP:
    quantita: float = 0.0
    costo_totale_eur: float = 0.0
    costo_totale_orig: float = 0.0
    divisa: str = "EUR"

    def costo_medio_eur(self) -> float:
        if self.quantita < 1e-9:
            return 0.0
        return self.costo_totale_eur / self.quantita

    def costo_medio_orig(self) -> float:
        if self.quantita < 1e-9:
            return 0.0
        return self.costo_totale_orig / self.quantita


@dataclass
class RecordPlusvalenza:
    isin: str
    titolo: str
    data_vendita: pd.Timestamp
    quantita_venduta: float
    divisa: str
    controvalore_vendita_eur: float
    controvalore_vendita_orig: float
    costo_carico_eur_cmp: float
    costo_carico_eur_lifo: float
    costo_carico_orig_cmp: float
    costo_carico_orig_lifo: float
    tipo_operazione: str = "Vendita"  # "Vendita" | "Rimborso"
    warnings: list = field(default_factory=list)

    @property
    def plus_minus_eur_cmp(self) -> float:
        return self.controvalore_vendita_eur - self.costo_carico_eur_cmp

    @property
    def plus_minus_eur_lifo(self) -> float:
        return self.controvalore_vendita_eur - self.costo_carico_eur_lifo

    @property
    def plus_minus_orig_cmp(self) -> float:
        return self.controvalore_vendita_orig - self.costo_carico_orig_cmp

    @property
    def plus_minus_orig_lifo(self) -> float:
        return self.controvalore_vendita_orig - self.costo_carico_orig_lifo


# ============================================================
# Funzione LIFO core
# ============================================================

def _calcola_costo_lifo(
    lotti: list[LottoLIFO],
    quantita_da_vendere: float,
    epsilon: float = 1e-6,
) -> tuple[float, float, list[LottoLIFO]]:
    """
    Estrae il costo di carico secondo metodo LIFO dall'ultimo lotto verso i precedenti.

    Returns:
        (costo_eur, costo_orig, lotti_aggiornati)
        Se i lotti non coprono la quantità, restituisce un costo parziale
        e aggiunge un warning nei log (non crasha).
    """
    costo_eur = 0.0
    costo_orig = 0.0
    lotti_rimanenti = deepcopy(lotti)
    quantita_residua = quantita_da_vendere

    while quantita_residua > epsilon and lotti_rimanenti:
        ultimo = lotti_rimanenti[-1]

        if ultimo.quantita < epsilon:
            lotti_rimanenti.pop()
            continue

        qta_da_lotto = min(quantita_residua, ultimo.quantita)

        costo_eur_lotto = qta_da_lotto * ultimo.costo_unitario_eur()
        costo_orig_lotto = qta_da_lotto * ultimo.costo_unitario_orig()

        costo_eur += costo_eur_lotto
        costo_orig += costo_orig_lotto

        ultimo.quantita -= qta_da_lotto
        ultimo.costo_totale_eur -= costo_eur_lotto
        ultimo.costo_totale_orig -= costo_orig_lotto
        quantita_residua -= qta_da_lotto

        if ultimo.quantita < epsilon:
            lotti_rimanenti.pop()

    return costo_eur, costo_orig, lotti_rimanenti


# ============================================================
# Engine principale
# ============================================================

class EngineEquity:
    """
    Processa la movimentazione equity in ordine cronologico
    e accumula i record di plusvalenza/minusvalenza.
    """

    def __init__(self, anno_imposta: int = None, epsilon: float = 1e-6):
        self.anno_imposta = anno_imposta  # used only for backward-compat / CLI display
        self.epsilon = epsilon

        # Stato interno del portafoglio
        self._portfolio_cmp: dict[str, PosizioneCMP] = {}
        self._portfolio_lifo: dict[str, list[LottoLIFO]] = {}

        # Output
        self.records: list[RecordPlusvalenza] = []
        self.warnings: list[str] = []
        self.info_log: list[str] = []

    # ----------------------------------------------------------
    # Entrypoint pubblico
    # ----------------------------------------------------------

    def processa(self, df: pd.DataFrame) -> "EngineEquity":
        """
        Elabora il DataFrame riga per riga in ordine cronologico,
        garantendo che a parità di data gli acquisti precedano le vendite.
        Modifica lo stato interno accumulando records e warnings.
        """
        if df.empty:
            return self

        def _calc_prio(r: pd.Series) -> int:
            desc = str(r.get("Descrizione", "")).lower()
            segno = str(r.get("Segno", "")).upper()
            if "aumento capitale" in desc:
                return 0
            if segno == "A" or "acquisto" in desc:
                return 1
            if segno == "V" or "rimborso" in desc or "vendita" in desc:
                return 2
            return 3

        df_sorted = df.copy()
        df_sorted["_sort_prio"] = df_sorted.apply(_calc_prio, axis=1)
        # Assicuriamoci che Data valuta non sia NaT e ordiniamo per Data valuta e priorità
        df_sorted.sort_values(by=["Data valuta", "_sort_prio"], ascending=[True, True], inplace=True)
        df_sorted.drop(columns=["_sort_prio"], inplace=True)

        for _, row in df_sorted.iterrows():
            try:
                self._processa_riga(row)
            except Exception as e:
                data_str = row["Data valuta"].strftime("%d/%m/%Y") if pd.notna(row["Data valuta"]) else "N/D"
                self.warnings.append(
                    f"[{data_str}] Errore su {row.get('Titolo', '?')} ({row.get('Isin', '?')}): {e}"
                )

        return self

    def risultati_dataframe(self, anno: int = None) -> pd.DataFrame:
        """Converte i records in DataFrame. Se anno è specificato, filtra per quell'anno."""
        if not self.records:
            return pd.DataFrame()

        righe = []
        for r in self.records:
            if anno is not None and r.data_vendita.year != anno:
                continue
            riga = {
                "Anno": r.data_vendita.year,
                "ISIN": r.isin,
                "Titolo": r.titolo,
                "Data Vendita": r.data_vendita.strftime("%d/%m/%Y"),
                "Tipo": r.tipo_operazione,
                "Quantità Venduta": r.quantita_venduta,
                "Divisa": r.divisa,
                "Controvalore Vendita (€)": round(r.controvalore_vendita_eur, 2),
                "Costo Carico (€) CMP": round(r.costo_carico_eur_cmp, 2),
                "Costo Carico (€) LIFO": round(r.costo_carico_eur_lifo, 2),
                "Plus/Minus (€) CMP": round(r.plus_minus_eur_cmp, 2),
                "Plus/Minus (€) LIFO": round(r.plus_minus_eur_lifo, 2),
            }
            # Colonne in valuta originale solo se non EUR
            if r.divisa != "EUR":
                riga[f"Controvalore Vendita ({r.divisa})"] = round(r.controvalore_vendita_orig, 2)
                riga[f"Costo Carico ({r.divisa}) CMP"] = round(r.costo_carico_orig_cmp, 2)
                riga[f"Plus/Minus ({r.divisa}) CMP"] = round(r.plus_minus_orig_cmp, 2)
            righe.append(riga)

        return pd.DataFrame(righe)

    def totali(self, anno: int = None) -> dict:
        """Restituisce i totali aggregati. Se anno è specificato, filtra per quell'anno."""
        df = self.risultati_dataframe(anno=anno)
        if df.empty:
            return {
                "corrispettivi_eur": 0.0,
                "costi_eur_cmp": 0.0,
                "costi_eur_lifo": 0.0,
                "plus_minus_eur_cmp": 0.0,
                "plus_minus_eur_lifo": 0.0,
            }
        return {
            "corrispettivi_eur": df["Controvalore Vendita (€)"].sum(),
            "costi_eur_cmp": df["Costo Carico (€) CMP"].sum(),
            "costi_eur_lifo": df["Costo Carico (€) LIFO"].sum(),
            "plus_minus_eur_cmp": df["Plus/Minus (€) CMP"].sum(),
            "plus_minus_eur_lifo": df["Plus/Minus (€) LIFO"].sum(),
        }

    def anni_disponibili(self) -> list[int]:
        """Restituisce la lista degli anni con almeno una vendita registrata."""
        if not self.records:
            return []
        return sorted({r.data_vendita.year for r in self.records})

    def stato_portafoglio(self) -> pd.DataFrame:
        """Snapshot del portafoglio residuo dopo l'elaborazione."""
        righe = []
        for isin, pos in self._portfolio_cmp.items():
            if pos.quantita > self.epsilon:
                righe.append({
                    "ISIN": isin,
                    "Quantità": round(pos.quantita, 4),
                    "Divisa": pos.divisa,
                    "Costo Totale (€)": round(pos.costo_totale_eur, 2),
                    "Costo Medio (€)": round(pos.costo_medio_eur(), 4),
                })
        return pd.DataFrame(righe)

    # ----------------------------------------------------------
    # Logica interna per singola riga
    # ----------------------------------------------------------

    def _processa_riga(self, row: pd.Series):
        isin = str(row["Isin"]).strip()
        desc = str(row["Descrizione"]).strip().lower()
        titolo = str(row["Titolo"]).strip()
        quantita = float(row["Quantita"])
        controvalore_eur = float(row["Controvalore"])
        prezzo_orig = float(row["Prezzo"])
        divisa = str(row["Divisa"]).strip()
        cambio = float(row.get("Cambio", 1.0)) if pd.notna(row.get("Cambio")) else 1.0
        data = row["Data valuta"]
        segno = str(row.get("Segno", "")).strip().upper()

        # Controvalore in valuta originale
        controvalore_orig = quantita * prezzo_orig

        # --- Router ---
        if "aumento capitale" in desc:
            self._gestisci_aumento_capitale(isin, titolo, quantita, divisa)

        elif segno == "A" or "acquisto" in desc:
            self._gestisci_acquisto(isin, divisa, quantita, controvalore_eur, controvalore_orig)

        elif segno == "V" or "rimborso" in desc or "vendita" in desc:
            tipo_op = "Rimborso" if "rimborso" in desc else "Vendita"
            self._gestisci_vendita(
                isin=isin,
                titolo=titolo,
                data=data,
                quantita=quantita,
                controvalore_eur=controvalore_eur,
                controvalore_orig=controvalore_orig,
                divisa=divisa,
                tipo_op=tipo_op,
            )

    def _init_isin(self, isin: str, divisa: str = "EUR"):
        """Inizializza le strutture per un ISIN non ancora visto."""
        if isin not in self._portfolio_cmp:
            self._portfolio_cmp[isin] = PosizioneCMP(divisa=divisa)
            self._portfolio_lifo[isin] = []

    def _gestisci_acquisto(
        self, isin: str, divisa: str,
        quantita: float, controvalore_eur: float, controvalore_orig: float,
    ):
        self._init_isin(isin, divisa)

        self._portfolio_cmp[isin].quantita += quantita
        self._portfolio_cmp[isin].costo_totale_eur += controvalore_eur
        self._portfolio_cmp[isin].costo_totale_orig += controvalore_orig

        self._portfolio_lifo[isin].append(
            LottoLIFO(quantita, controvalore_eur, controvalore_orig)
        )

    def _gestisci_vendita(
        self, isin: str, titolo: str, data: pd.Timestamp,
        quantita: float, controvalore_eur: float, controvalore_orig: float,
        divisa: str, tipo_op: str,
    ):
        pos_cmp = self._portfolio_cmp.get(isin)

        # Controllo disponibilità
        if pos_cmp is None or pos_cmp.quantita < quantita - self.epsilon:
            data_str = data.strftime("%d/%m/%Y")
            qta_disponibile = pos_cmp.quantita if pos_cmp else 0
            self.warnings.append(
                f"[{data_str}] {tipo_op} {titolo} ({isin}): "
                f"quantità richiesta {quantita:.0f}, disponibile {qta_disponibile:.0f}. "
                f"Operazione saltata (probabile storico incompleto)."
            )
            return

        # --- Calcolo LIFO ---
        costo_eur_lifo, costo_orig_lifo, lotti_aggiornati = _calcola_costo_lifo(
            self._portfolio_lifo.get(isin, []),
            quantita,
            self.epsilon,
        )
        self._portfolio_lifo[isin] = lotti_aggiornati

        # --- Calcolo CMP ---
        costo_medio_eur = pos_cmp.costo_medio_eur()
        costo_medio_orig = pos_cmp.costo_medio_orig()
        costo_eur_cmp = costo_medio_eur * quantita
        costo_orig_cmp = costo_medio_orig * quantita

        # Aggiorna CMP
        pos_cmp.quantita -= quantita
        pos_cmp.costo_totale_eur -= costo_eur_cmp
        pos_cmp.costo_totale_orig -= costo_orig_cmp

        # Azzera residui floating point se posizione chiusa
        if pos_cmp.quantita < self.epsilon:
            pos_cmp.quantita = 0.0
            pos_cmp.costo_totale_eur = 0.0
            pos_cmp.costo_totale_orig = 0.0

        # --- Registra sempre (filtro anno applicato a valle in risultati_dataframe/totali) ---
        self.records.append(RecordPlusvalenza(
            isin=isin,
            titolo=titolo,
            data_vendita=data,
            quantita_venduta=quantita,
            divisa=divisa,
            controvalore_vendita_eur=controvalore_eur,
            controvalore_vendita_orig=controvalore_orig,
            costo_carico_eur_cmp=costo_eur_cmp,
            costo_carico_eur_lifo=costo_eur_lifo,
            costo_carico_orig_cmp=costo_orig_cmp,
            costo_carico_orig_lifo=costo_orig_lifo,
            tipo_operazione=tipo_op,
        ))

    def _gestisci_aumento_capitale(
        self, isin: str, titolo: str, quantita: float, divisa: str,
    ):
        """
        Aumento di capitale: aggiunge azioni a costo zero.
        Per LIFO: distribuisce la quantità proporzionalmente tra i lotti esistenti.
        """
        self.info_log.append(
            f"Aumento di capitale: {titolo} ({isin}) +{quantita:.0f} azioni a costo zero"
        )

        if isin not in self._portfolio_cmp:
            # Titolo nuovo: lotto a costo zero
            self._portfolio_cmp[isin] = PosizioneCMP(
                quantita=quantita, costo_totale_eur=0.0,
                costo_totale_orig=0.0, divisa=divisa,
            )
            self._portfolio_lifo[isin] = [LottoLIFO(quantita, 0.0, 0.0)]
        else:
            # Titolo esistente: aggiunge quantità, costo invariato
            pos = self._portfolio_cmp[isin]
            pos.quantita += quantita

            # LIFO: distribuzione proporzionale sui lotti esistenti
            lotti = self._portfolio_lifo.get(isin, [])
            qta_totale_prima = sum(l.quantita for l in lotti)

            if qta_totale_prima > self.epsilon:
                for lotto in lotti:
                    proporzione = lotto.quantita / qta_totale_prima
                    lotto.quantita += quantita * proporzione
            else:
                lotti.append(LottoLIFO(quantita, 0.0, 0.0))
