"""
app.py
------
Interfaccia Streamlit per il calcolo tasse Fineco.
Avvio:
    streamlit run app.py
"""

import hashlib
import io
import json

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.loader import load_config, load_excel, load_excel_conto, classifica_operazioni
from core.engine_equity import EngineEquity
from core.engine_conto import processa_conto, riepilogo_per_anno_strumento, totale_pnl_per_anno
from core.report import calcola_quadro_rt, esporta_excel
from core.store import load_storico, save_storico, merge_storico, STORICO_PATH
from core.store_conto import (
    load_storico_conto, save_storico_conto, merge_storico_conto, STORICO_CONTO_PATH
)
from core import isin_alias

# ============================================================
# Config Streamlit
# ============================================================
st.set_page_config(
    page_title="Calcolo Tasse Fineco",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.title("⚙️ Parametri")

    metodo = st.radio(
        "Metodo calcolo costo di carico",
        options=["LIFO", "CMP"],
        index=0,
        help="LIFO = Last In First Out (Fineco default). CMP = Costo Medio Ponderato (metodo fiscale italiano).",
    )

    minus_pregresse = st.number_input(
        "Minusvalenze pregresse (RT27 anno prec.)",
        min_value=0.0, value=0.0, step=100.0,
        format="%.2f",
        help="Inserisci 0 se non hai minusvalenze da riportare dagli anni precedenti.",
    )

    # ----------------------------------------------------------
    # Sezione: Gestione dati storici
    # ----------------------------------------------------------
    st.divider()
    st.markdown("### 📂 Gestione dati")

    storico_sidebar = load_storico()

    if not storico_sidebar.empty:
        n_righe = len(storico_sidebar)
        data_min = storico_sidebar["Data valuta"].min().strftime("%d/%m/%Y")
        data_max = storico_sidebar["Data valuta"].max().strftime("%d/%m/%Y")
        st.caption(f"**{n_righe}** righe &nbsp;|&nbsp; {data_min} → {data_max}")
    else:
        st.caption("Nessun dato storico salvato.")

    nuovo_file_sidebar = st.file_uploader(
        "Aggiungi movimentazione",
        type=["xlsx", "xls"],
        key="sidebar_uploader",
        help="Carica un file Excel Fineco per aggiungerlo allo storico permanente.",
    )

    if nuovo_file_sidebar:
        file_bytes_sb = nuovo_file_sidebar.read()
        file_hash_sb = hashlib.md5(file_bytes_sb).hexdigest()

        # Processa solo se è un file nuovo (evita rielaborazione ad ogni rerun)
        if st.session_state.get("_sb_last_hash") != file_hash_sb:
            st.session_state["_sb_last_hash"] = file_hash_sb
            with st.spinner("Elaborazione e merge in corso..."):
                try:
                    config = load_config()
                    df_nuovo = load_excel(file_bytes_sb, config)
                    storico_curr = load_storico()
                    result = merge_storico(storico_curr, df_nuovo)
                    if result["n_nuove"] > 0:
                        save_storico(result["df"])
                    st.session_state["_sb_merge_result"] = result
                except Exception as e:
                    st.session_state["_sb_merge_result"] = {"error": str(e)}
            st.rerun()

    if "_sb_merge_result" in st.session_state:
        mr = st.session_state["_sb_merge_result"]
        if "error" in mr:
            st.error(f"Errore: {mr['error']}")
        elif mr["n_nuove"] > 0:
            st.success(f"✅ +{mr['n_nuove']} nuove righe aggiunte")
            if mr["n_duplicate"] > 0:
                st.caption(f"{mr['n_duplicate']} duplicate scartate")
        else:
            st.info(f"ℹ️ Nessuna nuova riga ({mr['n_duplicate']} duplicate scartate)")

    # Pulsante reset storico
    if not storico_sidebar.empty:
        st.divider()
        if st.button("🗑️ Reset storico", use_container_width=True):
            st.session_state["_confirm_reset"] = True

        if st.session_state.get("_confirm_reset"):
            st.warning("Eliminare definitivamente lo storico?")
            c1, c2 = st.columns(2)
            if c1.button("Sì, elimina", type="primary", use_container_width=True):
                STORICO_PATH.unlink(missing_ok=True)
                for key in ["_confirm_reset", "_sb_last_hash", "_sb_merge_result"]:
                    st.session_state.pop(key, None)
                st.rerun()
            if c2.button("Annulla", use_container_width=True):
                st.session_state.pop("_confirm_reset", None)
                st.rerun()

    # ----------------------------------------------------------
    # Sezione: Movimentazione conto (CFD/Derivati)
    # ----------------------------------------------------------
    st.divider()
    st.markdown("### 📊 Movimentazione conto (CFD)")

    storico_conto_sidebar = load_storico_conto()

    if not storico_conto_sidebar.empty:
        n_conto = len(storico_conto_sidebar)
        dc_min = storico_conto_sidebar["Data valuta"].min().strftime("%d/%m/%Y")
        dc_max = storico_conto_sidebar["Data valuta"].max().strftime("%d/%m/%Y")
        st.caption(f"**{n_conto}** righe &nbsp;|&nbsp; {dc_min} → {dc_max}")
    else:
        st.caption("Nessun dato conto salvato.")

    nuovo_file_conto_sb = st.file_uploader(
        "Aggiungi movimentazione conto",
        type=["xlsx", "xls"],
        key="sidebar_conto_uploader",
        help="Carica il file Excel della movimentazione del conto Fineco (CFD/Derivati).",
    )

    if nuovo_file_conto_sb:
        file_bytes_conto_sb = nuovo_file_conto_sb.read()
        file_hash_conto_sb = hashlib.md5(file_bytes_conto_sb).hexdigest()

        if st.session_state.get("_sb_conto_last_hash") != file_hash_conto_sb:
            st.session_state["_sb_conto_last_hash"] = file_hash_conto_sb
            with st.spinner("Elaborazione conto in corso..."):
                try:
                    config_c = load_config()
                    df_conto_nuovo = load_excel_conto(file_bytes_conto_sb, config_c)
                    storico_conto_curr = load_storico_conto()
                    result_c = merge_storico_conto(storico_conto_curr, df_conto_nuovo)
                    if result_c["n_nuove"] > 0:
                        save_storico_conto(result_c["df"])
                    st.session_state["_sb_conto_merge_result"] = result_c
                except Exception as e:
                    st.session_state["_sb_conto_merge_result"] = {"error": str(e)}
            st.rerun()

    if "_sb_conto_merge_result" in st.session_state:
        mr_c = st.session_state["_sb_conto_merge_result"]
        if "error" in mr_c:
            st.error(f"Errore: {mr_c['error']}")
        elif mr_c["n_nuove"] > 0:
            st.success(f"✅ +{mr_c['n_nuove']} nuove righe aggiunte")
            if mr_c["n_duplicate"] > 0:
                st.caption(f"{mr_c['n_duplicate']} duplicate scartate")
        else:
            st.info(f"ℹ️ Nessuna nuova riga ({mr_c['n_duplicate']} duplicate scartate)")

    # Pulsante reset storico conto
    if not storico_conto_sidebar.empty:
        if st.button("🗑️ Reset conto", use_container_width=True):
            st.session_state["_confirm_reset_conto"] = True

        if st.session_state.get("_confirm_reset_conto"):
            st.warning("Eliminare definitivamente lo storico conto?")
            cc1, cc2 = st.columns(2)
            if cc1.button("Sì, elimina", type="primary",
                          use_container_width=True, key="reset_conto_yes"):
                STORICO_CONTO_PATH.unlink(missing_ok=True)
                for key in ["_confirm_reset_conto", "_sb_conto_last_hash",
                             "_sb_conto_merge_result"]:
                    st.session_state.pop(key, None)
                st.rerun()
            if cc2.button("Annulla", use_container_width=True, key="reset_conto_no"):
                st.session_state.pop("_confirm_reset_conto", None)
                st.rerun()

    # --- Alias ISIN attivi ---
    _sb_alias = isin_alias.load_alias_map()
    if _sb_alias["alias"] or _sb_alias["ignored"]:
        st.divider()
        with st.expander("🔗 Alias ISIN", expanded=False):
            if _sb_alias["alias"]:
                st.markdown("**Associazioni attive:**")
                st.dataframe(
                    pd.DataFrame([
                        {"ISIN vendita": k, "→ ISIN acquisto": v}
                        for k, v in _sb_alias["alias"].items()
                    ]),
                    hide_index=True,
                    use_container_width=True,
                )
            if _sb_alias["ignored"]:
                st.markdown("**Ignorati:**")
                st.caption(", ".join(f"`{x}`" for x in _sb_alias["ignored"]))
            if st.button("🗑️ Rimuovi tutti gli alias", key="del_all_alias",
                         use_container_width=True):
                isin_alias.save_alias_map({}, [])
                st.rerun()

    st.divider()
    st.caption("Calcolo Tasse Fineco v2.0")
    st.caption("Solo per uso personale — verifica sempre con un commercialista.")


# ============================================================
# Main — Header
# ============================================================
st.title("📊 Calcolo Tasse Fineco")
st.markdown(
    "Carica il file Excel della movimentazione azionaria esportato da Fineco "
    "per calcolare plusvalenze, minusvalenze e il riepilogo per il Quadro RT."
)


# ============================================================
# Pipeline di elaborazione (cached)
# La cache è invalidata automaticamente quando il CSV storico cambia
# (la chiave di cache è csv_bytes, che cambia ad ogni modifica del file).
# ============================================================
@st.cache_data(show_spinner="Elaborazione in corso...")
def elabora_tutto(csv_bytes: bytes, alias_bytes: bytes = b"{}") -> dict:
    """
    Esegue la pipeline completa sul dataset (passato come CSV bytes).
    Calcola sia CMP che LIFO — il filtro metodo è applicato a valle.

    alias_bytes: JSON-serializzato del mapping {sell_isin: buy_isin}.
        Fa parte della chiave di cache: cambiare gli alias invalida il calcolo.
    """
    config = load_config()
    df = pd.read_csv(io.BytesIO(csv_bytes), parse_dates=["Data valuta"])

    # Applica alias ISIN (operazioni societarie: cambio codice/nome).
    # Il remapping avviene PRIMA di classifica_operazioni, in modo che
    # l'engine equity trovi la posizione usando l'ISIN di acquisto corretto.
    _alias_map = json.loads(alias_bytes)
    if _alias_map:
        df["Isin"] = df["Isin"].astype(str).map(lambda x: _alias_map.get(x, x))

    dataset = classifica_operazioni(df, config)

    engine_eq = EngineEquity()
    engine_eq.processa(dataset["equity"])

    # CFD/Futures esclusi dal calcolo fiscale.
    # Il file Fineco riporta il Controvalore del contratto (già in EUR con
    # moltiplicatore e cambio), ma per i futures serve il mark-to-market
    # giornaliero (margini) che non è disponibile nell'export standard.
    # Il PnL futures viene quindi impostato a 0 nel Quadro RT.
    anni_eq = engine_eq.anni_disponibili()
    tutti_anni = sorted(set(anni_eq))

    # --- Commissioni (solo nel nuovo formato Fineco) ---
    # Rilevate dinamicamente: qualsiasi colonna con "commissioni", "commissione" o "spese"
    col_comm = [
        c for c in df.columns
        if any(kw in c.lower() for kw in ["commissioni", "commissione", "spese"])
    ]
    if col_comm:
        for c in col_comm:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        df_comm = df.copy()
        df_comm["Anno"] = df_comm["Data valuta"].dt.year
        df_comm_yearly = (
            df_comm.groupby("Anno")[col_comm]
            .sum()
            .reset_index()
        )
        df_comm_yearly["Totale commissioni"] = df_comm_yearly[col_comm].sum(axis=1).round(2)
        for c in col_comm:
            df_comm_yearly[c] = df_comm_yearly[c].round(2)
    else:
        df_comm_yearly = pd.DataFrame()

    return {
        "df_equity_all": engine_eq.risultati_dataframe(),
        "df_cfd_all": pd.DataFrame(),          # CFD esclusi — vedi commento sopra
        "anni": tutti_anni,
        "anni_eq": anni_eq,
        "anni_cfd": [],
        "portafoglio": engine_eq.stato_portafoglio(),
        "posizioni_cfd_aperte": pd.DataFrame(),
        "warnings_eq": engine_eq.warnings,
        "info_eq": engine_eq.info_log,
        "df_commissioni_yearly": df_comm_yearly,
        "col_commissioni": col_comm,
    }


@st.cache_data(show_spinner="Elaborazione CFD in corso...")
def elabora_conto(conto_csv_bytes: bytes) -> dict:
    """
    Elabora lo storico della movimentazione conto (CFD/Derivati).
    Cached per conto_csv_bytes: ricalcola solo quando cambia il file storico conto.
    """
    config = load_config()
    descrizioni_cfd = config.get("descrizioni_cfd_conto", [])
    df = pd.read_csv(io.BytesIO(conto_csv_bytes), parse_dates=["Data valuta"])
    df_processed = processa_conto(df, descrizioni_cfd)
    return {
        "dettaglio":      df_processed,
        "riepilogo":      riepilogo_per_anno_strumento(df_processed),
        "pnl_per_anno":   totale_pnl_per_anno(df_processed),
        "anni":           sorted(df_processed["Anno"].unique().tolist())
                          if not df_processed.empty else [],
    }


@st.cache_data(show_spinner=False)
def _prescan_equity(csv_bytes: bytes) -> pd.DataFrame:
    """
    Carica e classifica la sola colonna equity del dataset.
    Usato per il rilevamento delle vendite orfane — NON applica alias.
    Cached per csv_bytes: ricalcola solo quando cambia il dataset.
    """
    config = load_config()
    df = pd.read_csv(io.BytesIO(csv_bytes), parse_dates=["Data valuta"])
    return classifica_operazioni(df, config)["equity"]


# ============================================================
# Determina sorgente dati: storico CSV oppure upload diretto
# ============================================================
storico_main = load_storico()

if not storico_main.empty:
    # --- Flusso primario: storico persistente ---
    with open(STORICO_PATH, "rb") as _f:
        csv_bytes = _f.read()

else:
    # --- Flusso di fallback: caricamento diretto senza salvataggio ---
    st.info(
        "ℹ️ Nessun dato storico. Carica un file qui sotto per un'analisi immediata, "
        "oppure usa **📂 Gestione dati** nella sidebar per salvare i dati in modo permanente."
    )

    uploaded_file = st.file_uploader(
        "Carica il file Excel di movimentazione",
        type=["xlsx", "xls"],
        help="Esporta da Fineco: Portafoglio → Movimentazione → Esporta Excel",
    )

    if not uploaded_file:
        st.stop()

    try:
        file_bytes = uploaded_file.read()
        _config_fb = load_config()
        df_direct = load_excel(file_bytes, _config_fb)
        # Converti in CSV bytes per uniformità con il flusso storico
        csv_bytes = df_direct.to_csv(index=False).encode("utf-8")
    except ValueError as e:
        st.error(f"❌ Errore nel file: {e}")
        st.stop()
    except Exception as e:
        st.error(f"❌ Errore imprevisto: {e}")
        st.stop()


# ============================================================
# Caricamento storico conto (CFD/Derivati) — separato da equity
# ============================================================
storico_conto_main = load_storico_conto()
_risultati_conto = None
_conto_pnl_per_anno: dict = {}

if not storico_conto_main.empty:
    with open(STORICO_CONTO_PATH, "rb") as _fc:
        conto_csv_bytes = _fc.read()
    try:
        _risultati_conto = elabora_conto(conto_csv_bytes)
        _conto_pnl_per_anno = _risultati_conto["pnl_per_anno"]
    except Exception as _e_conto:
        st.warning(f"⚠️ Errore elaborazione conto CFD: {_e_conto}")


# ============================================================
# Alias ISIN + calcolo risultati (cached)
# ============================================================
_alias_data = isin_alias.load_alias_map()
_alias_bytes = json.dumps(_alias_data["alias"], sort_keys=True).encode()

try:
    risultati = elabora_tutto(csv_bytes, _alias_bytes)
except ValueError as e:
    st.error(f"❌ Errore elaborazione: {e}")
    st.stop()
except Exception as e:
    st.error(f"❌ Errore imprevisto: {e}")
    st.stop()

anni_disponibili = risultati["anni"]

if not anni_disponibili:
    st.warning("⚠️ Nessuna operazione trovata nel file.")
    st.stop()


# ============================================================
# Sezione alias: vendite orfane (solo per anni ≥ anno_from)
# ============================================================
_config_alias = load_config()
_anno_alias_from = _config_alias.get("isin_alias_check_from_anno", 2025)
_df_eq_prescan = _prescan_equity(csv_bytes)

_orphans = isin_alias.trova_orphan_sells(
    _df_eq_prescan,
    anno_from=_anno_alias_from,
    alias_map=_alias_data["alias"],
    ignored=_alias_data["ignored"],
)

if not _orphans.empty:
    _n = len(_orphans)
    with st.expander(
        f"⚠️ {_n} {'vendita' if _n == 1 else 'vendite'} dal {_anno_alias_from} "
        f"senza corrispondenza acquisto — espandi per associare",
        expanded=True,
    ):
        st.info(
            f"Le seguenti vendite (anno ≥ **{_anno_alias_from}**) hanno un ISIN che non "
            f"compare tra gli acquisti in storico. Potrebbe trattarsi di un cambio ISIN "
            f"a seguito di un'operazione societaria (fusione, scissione, cambio nome). "
            f"Associa ogni vendita all'acquisto corrispondente, oppure ignorala. "
            f"Le associazioni vengono salvate permanentemente in `data/isin_alias.json`."
        )

        def _fmt_tx(df_src: pd.DataFrame, isin_filter: str, segno: str) -> pd.DataFrame:
            """
            Filtra per ISIN e segno (A/V), restituisce una tabella compatta
            ordinata cronologicamente con le colonne rilevanti.
            """
            mask = (
                df_src["Isin"].astype(str) == isin_filter
            ) & (
                df_src["Segno"].str.upper() == segno
            )
            cols = [c for c in ["Data valuta", "Quantita", "Prezzo", "Controvalore"]
                    if c in df_src.columns]
            df_f = df_src.loc[mask, cols].sort_values("Data valuta").copy().reset_index(drop=True)
            if "Data valuta" in df_f.columns:
                df_f["Data valuta"] = df_f["Data valuta"].dt.strftime("%d/%m/%Y")
            for _col, _dec in [("Quantita", 4), ("Prezzo", 4), ("Controvalore", 2)]:
                if _col in df_f.columns:
                    df_f[_col] = df_f[_col].round(_dec)
            return df_f.rename(columns={
                "Data valuta":  "Data",
                "Quantita":     "Qtà",
                "Controvalore": "Ctv (€)",
            })

        with st.form("orphan_alias_form"):
            _choices: dict[str, str] = {}   # sell_isin → buy_isin | "IGNORE"

            for _, _orphan in _orphans.iterrows():
                _sell_isin  = str(_orphan["Isin"])
                _sell_title = str(_orphan["Titolo"])

                # ── Intestazione ─────────────────────────────────────────
                st.markdown(f"#### `{_sell_isin}` — {_sell_title}")

                _suggestions = isin_alias.suggerisci_alias(
                    _sell_isin, _sell_title, _df_eq_prescan
                )

                # ── Layout a due colonne: vendite | acquisti candidati ───
                _col_v, _col_a = st.columns(2)

                with _col_v:
                    st.markdown("**📤 Vendite da riconciliare**")
                    _tbl_v = _fmt_tx(_df_eq_prescan, _sell_isin, "V")
                    if not _tbl_v.empty:
                        st.dataframe(_tbl_v, hide_index=True, use_container_width=True)
                    else:
                        st.caption("Nessuna vendita trovata.")

                with _col_a:
                    st.markdown("**📥 Acquisti candidati**")
                    if not _suggestions:
                        st.caption("Nessun titolo simile trovato negli acquisti.")
                    else:
                        for _s in _suggestions:
                            _buy_isin  = _s["isin_acquisto"]
                            _buy_title = _s["titolo_acquisto"]
                            _score     = _s["score"]
                            st.markdown(
                                f"**`{_buy_isin}`** — {_buy_title} &nbsp; "
                                f"*({_score * 100:.0f}% simile)*"
                            )
                            _tbl_a = _fmt_tx(_df_eq_prescan, _buy_isin, "A")
                            if not _tbl_a.empty:
                                st.dataframe(_tbl_a, hide_index=True, use_container_width=True)
                            else:
                                st.caption("Nessun acquisto trovato.")

                # ── Selezione ────────────────────────────────────────────
                if not _suggestions:
                    _choices[_sell_isin] = "IGNORE"
                else:
                    _opt_labels = ["— Ignora (nessuna corrispondenza) —"] + [
                        f"{s['titolo_acquisto']}  [{s['isin_acquisto']}]  — {s['score'] * 100:.0f}% simile"
                        for s in _suggestions
                    ]
                    _sel = st.radio(
                        "Associa a:",
                        options=list(range(len(_opt_labels))),
                        format_func=lambda i, opts=_opt_labels: opts[i],
                        key=f"alias_sel_{_sell_isin}",
                        index=1,          # default: primo suggerimento
                        horizontal=True,
                    )
                    _choices[_sell_isin] = (
                        "IGNORE" if _sel == 0
                        else _suggestions[_sel - 1]["isin_acquisto"]
                    )

                st.divider()

            if st.form_submit_button("💾 Salva associazioni", type="primary"):
                _new_alias   = dict(_alias_data["alias"])
                _new_ignored = list(_alias_data["ignored"])
                for _s_isin, _choice in _choices.items():
                    if _choice == "IGNORE":
                        if _s_isin not in _new_ignored:
                            _new_ignored.append(_s_isin)
                    else:
                        _new_alias[_s_isin] = _choice
                isin_alias.save_alias_map(_new_alias, _new_ignored)
                st.rerun()


# ============================================================
# Helper: calcola dati per un anno specifico
# ============================================================
def dati_per_anno(anno: int, metodo: str) -> dict:
    """Filtra i DataFrame per anno e calcola totali + Quadro RT."""
    df_eq_all = risultati["df_equity_all"]

    df_eq = df_eq_all[df_eq_all["Anno"] == anno].copy() if not df_eq_all.empty else pd.DataFrame()

    totali_eq = {
        "corrispettivi_eur":    df_eq["Controvalore Vendita (€)"].sum() if not df_eq.empty else 0.0,
        "costi_eur_cmp":        df_eq["Costo Carico (€) CMP"].sum()    if not df_eq.empty else 0.0,
        "costi_eur_lifo":       df_eq["Costo Carico (€) LIFO"].sum()   if not df_eq.empty else 0.0,
        "plus_minus_eur_cmp":   df_eq["Plus/Minus (€) CMP"].sum()      if not df_eq.empty else 0.0,
        "plus_minus_eur_lifo":  df_eq["Plus/Minus (€) LIFO"].sum()     if not df_eq.empty else 0.0,
    }

    # PnL CFD dal file della movimentazione conto (non dal file titoli)
    cfd_pnl = _conto_pnl_per_anno.get(anno, 0.0)
    totali_cfd = {"pnl_totale_eur": cfd_pnl}

    rt = calcola_quadro_rt(
        totali_equity=totali_eq,
        totali_cfd=totali_cfd,
        minusvalenze_pregresse=minus_pregresse,
        metodo=metodo,
    )
    return {"df_eq": df_eq, "totali_eq": totali_eq, "totali_cfd": totali_cfd, "rt": rt}


def dati_storico(metodo: str) -> pd.DataFrame:
    """Costruisce DataFrame storico per tutti gli anni (solo equity, CFD esclusi)."""
    df_eq_all = risultati["df_equity_all"]
    righe = []
    for anno in anni_disponibili:
        df_eq = df_eq_all[df_eq_all["Anno"] == anno] if not df_eq_all.empty else pd.DataFrame()
        pm_eq = df_eq[f"Plus/Minus (€) {metodo}"].sum() if not df_eq.empty else 0.0
        righe.append({
            "Anno": anno,
            "Plus/Minus Equity": round(pm_eq, 2),
        })
    df = pd.DataFrame(righe)
    if not df.empty:
        df["Cumulato"] = df["Plus/Minus Equity"].cumsum().round(2)
    return df


# ============================================================
# TAB NAVIGATION
# ============================================================
tab1, tab2, tab3 = st.tabs([
    "📋 Anno Selezionato",
    "📈 Storico Multi-Anno",
    "📉 CFD / Derivati",
])


# ============================================================
# TAB 1 — Anno Selezionato
# ============================================================
with tab1:

    anno_default = max(anni_disponibili)
    anno_sel = st.selectbox(
        "📅 Anno di imposta",
        options=anni_disponibili[::-1],
        index=0,
        key="anno_tab1",
    )

    dati = dati_per_anno(anno_sel, metodo)
    df_eq = dati["df_eq"]
    totali_eq = dati["totali_eq"]
    totali_cfd = dati["totali_cfd"]
    rt = dati["rt"]
    col_pm = f"Plus/Minus (€) {metodo}"

    # ----------------------------------------------------------
    # KPI cards
    # ----------------------------------------------------------
    st.subheader(f"📊 Riepilogo {anno_sel}")
    col1, col2, col3, col4 = st.columns(4)

    plus_minus_equity = totali_eq.get(f"plus_minus_eur_{metodo.lower()}", 0)
    plus_minus_cfd    = totali_cfd.get("pnl_totale_eur", 0)
    plus_minus_totale = rt["rt23_plus_minus_anno"]

    with col1:
        st.metric("Plus/Minus Equity", f"{plus_minus_equity:+,.2f} €",
                  help=f"Metodo {metodo}")
    with col2:
        if _risultati_conto is not None:
            st.metric(
                "Plus/Minus CFD / Derivati",
                f"{plus_minus_cfd:+,.2f} €",
                help="Margine variazione + Oneri/Proventi derivati dal file conto Fineco",
            )
        else:
            st.metric(
                "CFD / Derivati",
                "n/d",
                help="Carica il file della movimentazione conto nella sidebar per includere i CFD.",
            )
    with col3:
        cfd_label = "equity + CFD" if _risultati_conto is not None else "solo equity"
        st.metric("Totale anno (RT23)", f"{plus_minus_totale:+,.2f} €",
                  help=cfd_label)
    with col4:
        st.metric("Imposta 26% (RT26)", f"{rt['rt26_imposta']:,.2f} €",
                  help="Calcolata sull'imponibile netto RT25")

    # ----------------------------------------------------------
    # Commissioni (solo se presenti nel file caricato)
    # ----------------------------------------------------------
    df_comm_yearly = risultati["df_commissioni_yearly"]
    col_comm = risultati["col_commissioni"]

    if not df_comm_yearly.empty:
        df_comm_anno = df_comm_yearly[df_comm_yearly["Anno"] == anno_sel]
        if not df_comm_anno.empty:
            totale_comm = df_comm_anno["Totale commissioni"].values[0]
            with st.expander(
                f"💳 Commissioni e spese pagate nel {anno_sel} — totale: **{totale_comm:,.2f} €**",
                expanded=False,
            ):
                st.caption(
                    "ℹ️ Le commissioni **non sono deducibili** ai fini del calcolo delle "
                    "plusvalenze/minusvalenze per la tassazione italiana sui capital gain (Quadro RT)."
                )
                comm_cols_display = st.columns(min(len(col_comm), 3))
                col_idx = 0
                for c in col_comm:
                    val = df_comm_anno[c].values[0]
                    if val != 0.0:
                        comm_cols_display[col_idx % len(comm_cols_display)].metric(c, f"{val:,.2f} €")
                        col_idx += 1

    # ----------------------------------------------------------
    # Quadro RT
    # ----------------------------------------------------------
    with st.expander("📄 Quadro RT — Dettaglio righi", expanded=False):
        col_rt1, col_rt2 = st.columns(2)

        with col_rt1:
            st.markdown("**Equity**")
            for k, v in {
                "RT21 — Corrispettivi": f"{rt['rt21_corrispettivi_equity']:,.2f} €",
                "RT22 — Costi": f"{rt['rt22_costi_equity']:,.2f} €",
                "Plus/Minus equity": f"{rt['plus_minus_equity']:+,.2f} €",
            }.items():
                c1, c2 = st.columns([2, 1])
                c1.markdown(k)
                c2.markdown(f"**{v}**")

        with col_rt2:
            st.markdown("**CFD / Derivati + Totale anno**")
            _cfd_label = (
                f"{rt['pnl_cfd']:+,.2f} €"
                if _risultati_conto is not None
                else "n/d — carica file conto"
            )
            c1, c2 = st.columns([2, 1])
            c1.markdown("PnL CFD netto (Margine + Oneri/Proventi)")
            c2.markdown(f"**{_cfd_label}**")
            st.divider()
            for k, v in {
                "RT23 — Plus/Minus anno": f"{rt['rt23_plus_minus_anno']:+,.2f} €",
                "RT24 — Minus pregresse": f"{rt['rt24_minus_pregresse']:,.2f} €",
                "RT25 — Imponibile":      f"{rt['rt25_imponibile']:,.2f} €",
            }.items():
                c1, c2 = st.columns([2, 1])
                c1.markdown(k)
                c2.markdown(f"**{v}**")

        st.divider()
        ci1, ci2 = st.columns(2)
        ci1.metric("RT26 — Imposta sostitutiva 26%", f"{rt['rt26_imposta']:,.2f} €")
        ci2.metric("RT27 — Minus da riportare", f"{rt['rt27_da_riportare']:,.2f} €")

    st.divider()

    # ----------------------------------------------------------
    # Breakdown per titolo
    # ----------------------------------------------------------
    st.subheader(f"📌 Riepilogo per titolo — {anno_sel}")

    if df_eq.empty:
        st.info(f"Nessuna vendita equity nell'anno {anno_sel}.")
    else:
        agg = (
            df_eq.groupby(["ISIN", "Titolo"], as_index=False)
            .agg(
                Corrispettivi=("Controvalore Vendita (€)", "sum"),
                Costo=("Costo Carico (€) " + metodo, "sum"),
                PlusMinus=(col_pm, "sum"),
                N_ops=(col_pm, "count"),
            )
            .sort_values("PlusMinus", ascending=True)
        )
        agg["Corrispettivi"] = agg["Corrispettivi"].round(2)
        agg["Costo"] = agg["Costo"].round(2)
        agg["PlusMinus"] = agg["PlusMinus"].round(2)

        fig_titolo = px.bar(
            agg,
            x="PlusMinus",
            y="Titolo",
            orientation="h",
            color="PlusMinus",
            color_continuous_scale=["#d62728", "#aec7e8", "#2ca02c"],
            color_continuous_midpoint=0,
            labels={"PlusMinus": "Plus/Minus (€)", "Titolo": ""},
            title=f"Plus/Minus per titolo — {anno_sel} ({metodo})",
            template="plotly_white",
            height=max(300, len(agg) * 35 + 100),
        )
        fig_titolo.update_layout(coloraxis_showscale=False, margin=dict(l=0, r=20, t=40, b=20))
        fig_titolo.add_vline(x=0, line_width=1, line_color="gray")
        st.plotly_chart(fig_titolo, use_container_width=True)

        st.dataframe(
            agg.rename(columns={
                "Corrispettivi": "Corrispettivi (€)",
                "Costo": f"Costo ({metodo}) (€)",
                "PlusMinus": "Plus/Minus (€)",
                "N_ops": "# Operazioni",
            }),
            use_container_width=True,
            hide_index=True,
        )

    st.divider()

    # ----------------------------------------------------------
    # Tabella dettaglio operazioni equity
    # ----------------------------------------------------------
    with st.expander(f"📈 Dettaglio operazioni equity {anno_sel}", expanded=False):
        if df_eq.empty:
            st.info("Nessuna operazione.")
        else:
            col_f1, col_f2 = st.columns([2, 1])
            with col_f1:
                isin_filter = st.multiselect(
                    "Filtra per Titolo",
                    options=df_eq["Titolo"].unique().tolist(),
                    default=[],
                    key="isin_filter_tab1",
                )
            with col_f2:
                tipo_filter = st.multiselect(
                    "Tipo operazione",
                    options=df_eq["Tipo"].unique().tolist() if "Tipo" in df_eq.columns else [],
                    default=[],
                    key="tipo_filter_tab1",
                )

            df_show = df_eq.copy()
            if isin_filter:
                df_show = df_show[df_show["Titolo"].isin(isin_filter)]
            if tipo_filter and "Tipo" in df_show.columns:
                df_show = df_show[df_show["Tipo"].isin(tipo_filter)]

            st.dataframe(df_show, use_container_width=True, hide_index=True)
            st.caption(f"{len(df_show)} operazioni mostrate")

    # ----------------------------------------------------------
    # Riepilogo CFD anno selezionato
    # ----------------------------------------------------------
    _cfd_title = (
        f"📉 CFD / Derivati {anno_sel} — PnL: {plus_minus_cfd:+,.2f} €"
        if _risultati_conto is not None
        else f"📉 CFD / Derivati {anno_sel} — dati non disponibili"
    )
    with st.expander(_cfd_title, expanded=False):
        if _risultati_conto is None:
            st.info(
                "Carica il file della **movimentazione del conto** Fineco nella sidebar "
                "per visualizzare i PnL di futures e CFD e includerli nel Quadro RT."
            )
        else:
            _riepilogo_conto = _risultati_conto["riepilogo"]
            _riepilogo_anno  = (
                _riepilogo_conto[_riepilogo_conto["Anno"] == anno_sel]
                if not _riepilogo_conto.empty else pd.DataFrame()
            )
            if _riepilogo_anno.empty:
                st.info(f"Nessuna operazione CFD/Derivati nel {anno_sel}.")
            else:
                _cols_show = [c for c in [
                    "Strumento",
                    "Margine variazione (€)",
                    "Oneri/Proventi (€)",
                    "Totale (€)",
                ] if c in _riepilogo_anno.columns]
                st.dataframe(
                    _riepilogo_anno[_cols_show],
                    hide_index=True,
                    use_container_width=True,
                )
                st.caption(
                    "**Margine variazione** = mark-to-market giornaliero (guadagni/perdite realizzati). "
                    "**Oneri/Proventi** = costi di finanziamento, dividendi su derivati. "
                    "Entrambi concorrono al calcolo del Quadro RT."
                )

    # ----------------------------------------------------------
    # Portafoglio residuo e posizioni aperte
    # ----------------------------------------------------------
    portafoglio = risultati["portafoglio"]

    with st.expander("💼 Portafoglio equity residuo"):
        if portafoglio.empty:
            st.info("Nessuna posizione equity aperta.")
        else:
            st.dataframe(portafoglio, use_container_width=True, hide_index=True)

    # ----------------------------------------------------------
    # Warnings
    # ----------------------------------------------------------
    warnings = risultati["warnings_eq"]
    if warnings:
        with st.expander(f"⚠️ {len(warnings)} operazioni con storico incompleto"):
            st.caption(
                "Queste vendite sono state saltate perché la quantità disponibile "
                "nel file è insufficiente (storico acquisti non coperto dal file)."
            )
            for w in warnings:
                st.text(w)

    # ----------------------------------------------------------
    # Export
    # ----------------------------------------------------------
    st.divider()
    st.subheader("💾 Esporta risultati")
    if st.button("Genera file Excel", type="primary", key="export_tab1"):
        # df_cfd per l'export: riepilogo strumenti dal conto per l'anno selezionato
        _df_cfd_export = pd.DataFrame()
        if _risultati_conto is not None and not _risultati_conto["riepilogo"].empty:
            _df_cfd_export = _risultati_conto["riepilogo"][
                _risultati_conto["riepilogo"]["Anno"] == anno_sel
            ].drop(columns=["Anno"], errors="ignore")
        output_path = esporta_excel(
            df_equity=df_eq,
            df_cfd=_df_cfd_export,
            quadro_rt=rt,
            anno=anno_sel,
            output_folder="output",
        )
        with open(output_path, "rb") as f:
            st.download_button(
                label="⬇️ Scarica Excel",
                data=f.read(),
                file_name=output_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_tab1",
            )
        st.success(f"File generato: {output_path.name}")


# ============================================================
# TAB 2 — Storico Multi-Anno
# ============================================================
with tab2:
    st.subheader("📈 Storico Multi-Anno")

    df_storico = dati_storico(metodo)

    if df_storico.empty:
        st.info("Nessun dato storico disponibile.")
        st.stop()

    anni_min = int(df_storico["Anno"].min())
    anni_max = int(df_storico["Anno"].max())

    if anni_min == anni_max:
        anno_range = (anni_min, anni_max)
        st.info(f"Dati disponibili solo per l'anno {anni_min}.")
    else:
        anno_range = st.select_slider(
            "📅 Range anni",
            options=list(range(anni_min, anni_max + 1)),
            value=(anni_min, anni_max),
            key="range_storico",
        )

    df_storico_filt = df_storico[
        (df_storico["Anno"] >= anno_range[0]) &
        (df_storico["Anno"] <= anno_range[1])
    ].copy()

    df_storico_filt["Cumulato"] = df_storico_filt["Plus/Minus Equity"].cumsum().round(2)

    # ----------------------------------------------------------
    # KPI riepilogo range
    # ----------------------------------------------------------
    eq_range = df_storico_filt["Plus/Minus Equity"].sum()
    n_anni = len(df_storico_filt)

    k1, k2, k3 = st.columns(3)
    k1.metric("Equity periodo", f"{eq_range:+,.2f} €")
    k2.metric("Anni nel range", str(n_anni))
    k3.metric("Futures / CFD", "esclusi", help="Esclusi dal calcolo — vedi sezione futures in Tab 1")

    st.divider()

    # ----------------------------------------------------------
    # Bar chart: plus/minus annuale (equity + CFD)
    # ----------------------------------------------------------
    fig_bar = px.bar(
        df_storico_filt,
        x="Anno",
        y="Plus/Minus Equity",
        color="Plus/Minus Equity",
        color_continuous_scale=["#d62728", "#aec7e8", "#2ca02c"],
        color_continuous_midpoint=0,
        labels={"Plus/Minus Equity": "Plus/Minus (€)", "Anno": "Anno"},
        title=f"Plus/Minus annuale Equity — {metodo} (Futures/CFD esclusi)",
        template="plotly_white",
    )
    fig_bar.add_hline(y=0, line_width=1, line_color="gray")
    fig_bar.update_layout(coloraxis_showscale=False, margin=dict(l=0, r=20, t=40, b=20))
    st.plotly_chart(fig_bar, use_container_width=True)

    # ----------------------------------------------------------
    # Line chart: cumulato
    # ----------------------------------------------------------
    fig_line = go.Figure()
    fig_line.add_trace(go.Scatter(
        x=df_storico_filt["Anno"],
        y=df_storico_filt["Cumulato"],
        mode="lines+markers",
        name="Cumulato",
        line=dict(color="#00CC96", width=2),
        marker=dict(size=8),
        fill="tozeroy",
        fillcolor="rgba(0,204,150,0.1)",
    ))
    fig_line.add_hline(y=0, line_width=1, line_color="gray", line_dash="dot")
    fig_line.update_layout(
        title="Plus/Minus cumulato nel periodo (solo Equity)",
        xaxis_title="Anno",
        yaxis_title="Cumulato (€)",
        template="plotly_white",
        showlegend=False,
        margin=dict(l=0, r=20, t=40, b=20),
    )
    st.plotly_chart(fig_line, use_container_width=True)

    # ----------------------------------------------------------
    # Tabella riepilogo per anno
    # ----------------------------------------------------------
    st.subheader("📋 Riepilogo per anno (solo Equity)")
    st.dataframe(
        df_storico_filt.rename(columns={
            "Plus/Minus Equity": f"Plus/Minus Equity ({metodo}) (€)",
            "Cumulato": "Cumulato (€)",
        }),
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# TAB 3 — CFD / Derivati
# ============================================================
with tab3:
    st.subheader("📉 CFD / Derivati — Riepilogo per Strumento")

    if _risultati_conto is None:
        st.info(
            "ℹ️ Nessun dato disponibile. "
            "Carica il file della **movimentazione del conto** Fineco "
            "nella sidebar (sezione **📊 Movimentazione conto (CFD)**) "
            "per calcolare i PnL di futures e CFD."
        )
        st.stop()

    _det_conto    = _risultati_conto["dettaglio"]
    _riepi_conto  = _risultati_conto["riepilogo"]
    _anni_conto   = _risultati_conto["anni"]

    if not _anni_conto:
        st.warning("Nessuna operazione CFD/Derivati trovata nel file caricato.")
        st.stop()

    # Selettore anno
    _anno_cfd = st.selectbox(
        "📅 Anno",
        options=sorted(_anni_conto, reverse=True),
        index=0,
        key="anno_tab3",
    )

    _riepi_anno = (
        _riepi_conto[_riepi_conto["Anno"] == _anno_cfd].copy()
        if not _riepi_conto.empty else pd.DataFrame()
    )

    # ----------------------------------------------------------
    # KPI anno CFD
    # ----------------------------------------------------------
    _tot_margine = _riepi_anno["Margine variazione (€)"].sum() if not _riepi_anno.empty else 0.0
    _tot_oneri   = _riepi_anno["Oneri/Proventi (€)"].sum()     if not _riepi_anno.empty else 0.0
    _tot_cfd     = _riepi_anno["Totale (€)"].sum()             if not _riepi_anno.empty else 0.0

    kc1, kc2, kc3 = st.columns(3)
    kc1.metric("Margine variazione", f"{_tot_margine:+,.2f} €",
               help="Somma dei margini di variazione derivati (mark-to-market)")
    kc2.metric("Oneri / Proventi",   f"{_tot_oneri:+,.2f} €",
               help="Oneri CFD, Proventi CFD, dividendi su derivati")
    kc3.metric("Totale PnL CFD",     f"{_tot_cfd:+,.2f} €",
               help="Margine + Oneri/Proventi — incluso nel Quadro RT")

    st.divider()

    # ----------------------------------------------------------
    # Bar chart per strumento
    # ----------------------------------------------------------
    if not _riepi_anno.empty:
        _chart_data = _riepi_anno.sort_values("Totale (€)", ascending=True)

        fig_cfd = px.bar(
            _chart_data,
            x="Totale (€)",
            y="Strumento",
            orientation="h",
            color="Totale (€)",
            color_continuous_scale=["#d62728", "#aec7e8", "#2ca02c"],
            color_continuous_midpoint=0,
            labels={"Totale (€)": "PnL (€)", "Strumento": ""},
            title=f"PnL per Strumento CFD/Derivati — {_anno_cfd}",
            template="plotly_white",
            height=max(300, len(_chart_data) * 40 + 100),
        )
        fig_cfd.update_layout(
            coloraxis_showscale=False,
            margin=dict(l=0, r=20, t=40, b=20),
        )
        fig_cfd.add_vline(x=0, line_width=1, line_color="gray")
        st.plotly_chart(fig_cfd, use_container_width=True)

    # ----------------------------------------------------------
    # Tabella dettaglio per strumento
    # ----------------------------------------------------------
    st.subheader(f"📋 Dettaglio per strumento — {_anno_cfd}")
    if _riepi_anno.empty:
        st.info(f"Nessuna operazione CFD/Derivati nel {_anno_cfd}.")
    else:
        _cols_tbl = [c for c in [
            "Strumento",
            "Margine variazione (€)",
            "Oneri/Proventi (€)",
            "Totale (€)",
        ] if c in _riepi_anno.columns]
        st.dataframe(
            _riepi_anno[_cols_tbl],
            hide_index=True,
            use_container_width=True,
        )

    # ----------------------------------------------------------
    # Storico multi-anno CFD
    # ----------------------------------------------------------
    st.divider()
    st.subheader("📈 Storico PnL CFD — tutti gli anni")

    if not _det_conto.empty:
        _storico_cfd = (
            _det_conto.groupby("Anno")["PnL (€)"]
            .sum()
            .round(2)
            .reset_index()
        )
        _storico_cfd["Cumulato (€)"] = _storico_cfd["PnL (€)"].cumsum().round(2)

        fig_cfd_hist = px.bar(
            _storico_cfd,
            x="Anno",
            y="PnL (€)",
            color="PnL (€)",
            color_continuous_scale=["#d62728", "#aec7e8", "#2ca02c"],
            color_continuous_midpoint=0,
            labels={"PnL (€)": "PnL (€)", "Anno": "Anno"},
            title="PnL CFD/Derivati per anno",
            template="plotly_white",
        )
        fig_cfd_hist.add_hline(y=0, line_width=1, line_color="gray")
        fig_cfd_hist.update_layout(
            coloraxis_showscale=False,
            margin=dict(l=0, r=20, t=40, b=20),
        )
        st.plotly_chart(fig_cfd_hist, use_container_width=True)

        st.dataframe(
            _storico_cfd.rename(columns={"PnL (€)": f"PnL CFD (€)"}),
            hide_index=True,
            use_container_width=True,
        )
