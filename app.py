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
from core import isin_alias, classificazione, omaggi
from core.backup import create_auto_backup, get_backup_zip_bytes, restore_from_zip

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
    st.markdown("### 📂 Movimentazione azionaria")

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
                    create_auto_backup("pre_merge_equity")
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
                create_auto_backup("pre_reset_equity")
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
                    create_auto_backup("pre_merge_conto")
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
                create_auto_backup("pre_reset_conto")
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
                create_auto_backup("pre_alias_clear")
                isin_alias.save_alias_map({}, [])
                st.rerun()

    # ----------------------------------------------------------
    # Sicurezza e Backup
    # ----------------------------------------------------------
    st.divider()
    with st.expander("🛡️ Sicurezza & Backup", expanded=False):
        st.markdown("**Esporta Backup**")
        st.download_button(
            label="⬇️ Scarica ZIP Database",
            data=get_backup_zip_bytes(),
            file_name="fineco_tax_backup.zip",
            mime="application/zip",
            use_container_width=True,
            help="Scarica tutti gli storici, alias e configurazioni."
        )
        st.markdown("**Ripristina da Backup**")
        upl_backup = st.file_uploader("Carica file .zip", type=["zip"], key="backup_upl")
        if upl_backup:
            if st.button("🔄 Conferma Ripristino", use_container_width=True, type="primary"):
                try:
                    restore_from_zip(upl_backup.read())
                    st.success("Ripristino completato!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Errore ripristino: {e}")

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
def elabora_tutto(csv_bytes: bytes, alias_bytes: bytes = b"{}", class_bytes: bytes = b"{}", omaggi_bytes: bytes = b"[]") -> dict:
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

    _omaggi_list = json.loads(omaggi_bytes)
    engine_eq = EngineEquity(omaggi_confermati=_omaggi_list)
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

    df_eq_all = engine_eq.risultati_dataframe()
    if not df_eq_all.empty:
        class_map = json.loads(class_bytes)
        df_eq_all["Classe"] = df_eq_all["Titolo"].apply(lambda t: class_map.get(t, "Investing"))

    return {
        "df_equity_all": df_eq_all,
        "df_cfd_all": pd.DataFrame(),          # CFD esclusi — vedi commento sopra
        "anni": tutti_anni,
        "anni_eq": anni_eq,
        "anni_cfd": [],
        "portafoglio": engine_eq.stato_portafoglio(),
        "posizioni_cfd_aperte": pd.DataFrame(),
        "warnings_eq": engine_eq.warnings,
        "info_eq": engine_eq.info_log,
        "pending_omaggi": engine_eq.pending_omaggi,
        "df_commissioni_yearly": df_comm_yearly,
        "col_commissioni": col_comm,
        "matches_equity": engine_eq.matches_dataframe(),
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
        "oppure usa **📂 Movimentazione azionaria** nella sidebar per salvare i dati in modo permanente."
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


_classificazioni_data = classificazione.load_classificazioni()
_classificazioni_bytes = json.dumps(_classificazioni_data, sort_keys=True).encode()

_omaggi_data = omaggi.load_omaggi()
_omaggi_bytes = json.dumps(_omaggi_data, sort_keys=True).encode()

# ---- Bottone Svuota Cache (sidebar) ----
with st.sidebar:
    if st.button("🔄 Svuota Cache e Ricalcola", help="Forza il ricalcolo completo cancellando la cache di Streamlit"):
        elabora_tutto.clear()
        elabora_conto.clear()
        st.rerun()

try:
    risultati = elabora_tutto(csv_bytes, _alias_bytes, _classificazioni_bytes, _omaggi_bytes)
except ValueError as e:
    st.error(f"❌ Errore elaborazione: {e}")
    st.stop()
except Exception as e:
    st.error(f"❌ Errore imprevisto: {e}")
    st.stop()

# ============================================================
# Intercettazione Quote Mancanti (Omaggi Pendenti)
# ============================================================
pending_omaggi = risultati.get("pending_omaggi", [])
if pending_omaggi:
    st.error("🛑 Rilevate Quantità Mancanti in Portafoglio", icon="🚨")
    st.warning(
        "Sono state trovate transazioni in cui hai venduto più quote di quante ne risultassero disponibili "
        "dal calcolo storico. Questo accade spesso con **assegnazioni gratuite** o diritti."
    )
    st.info("Scegli come gestire queste vendite. Puoi approvare l'assunzione di **costo zero** per le quote mancanti, ignorarle/escluderle dal calcolo, oppure fermarti e ricontrollare l'Excel.")
    
    with st.form("omaggi_form"):
        st.write("### Operazioni in Sospeso")
        scelte_omaggi = {}
        for i, p in enumerate(pending_omaggi):
            isin = p["isin"]
            data_vendita = p["data_vendita"]
            qta_manc = p["quantita_mancante"]
            # Aggiungiamo l'indice i per garantire che la chiave sia sempre unica per Streamlit
            chiave_univoca = f"{isin} - {data_vendita} - {qta_manc}_{i}"
            
            st.markdown(f"**{p['titolo']} ({isin})**")
            st.write(f"- **Data vendita:** {data_vendita}")
            st.write(f"- **Quantità venduta:** {p['quantita_richiesta']:.2f}")
            st.write(f"- **Disponibili in ptf:** {p['quantita_disponibile']:.2f}")
            st.write(f"- **Quote scoperte (mancanti):** {qta_manc:.2f}")
            
            scelte_omaggi[chiave_univoca] = st.radio(
                "Azione per questa vendita scoperta:",
                options=["Attendi (blocca calcolo)", "Conferma come Costo Zero", "Escludi dal calcolo (Ignora)"],
                index=0,
                key=f"rad_omaggio_{chiave_univoca}"
            )
            st.divider()
            
        col_btn1, col_btn2 = st.columns([1, 4])
        with col_btn1:
            submit_omaggi = st.form_submit_button("Salva Scelte", type="primary")
            
        if submit_omaggi:
            # Raccogli le approvazioni
            _nuovi_omaggi = list(_omaggi_data)
            da_aggiungere = []
            for i, p in enumerate(pending_omaggi):
                isin = p["isin"]
                data_vendita = p["data_vendita"]
                qta_manc = p["quantita_mancante"]
                chiave = f"{isin} - {data_vendita} - {qta_manc}_{i}"
                
                scelta = scelte_omaggi[chiave]
                if scelta == "Conferma come Costo Zero":
                    da_aggiungere.append({
                        "isin": isin,
                        "data_vendita": data_vendita,
                        "quantita_mancante": qta_manc,
                        "titolo": p["titolo"],
                        "azione": "costo_zero"
                    })
                elif scelta == "Escludi dal calcolo (Ignora)":
                    da_aggiungere.append({
                        "isin": isin,
                        "data_vendita": data_vendita,
                        "quantita_mancante": qta_manc,
                        "titolo": p["titolo"],
                        "azione": "ignora"
                    })

            if da_aggiungere:
                _nuovi_omaggi.extend(da_aggiungere)
                create_auto_backup("pre_approvazione_omaggi")
                omaggi.save_omaggi(_nuovi_omaggi)
                st.success("Approvazioni salvate! Ricarico...")
                st.rerun()
            else:
                st.info("Non hai approvato né ignorato nessuna operazione.")
                
    st.stop()  # Ferma il rendering del resto dell'app finché ci sono problemi


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

# ============================================================
# Sezione classificazione: Titoli sconosciuti
# ============================================================
titoli_unici = _df_eq_prescan["Titolo"].dropna().unique()
titoli_sconosciuti = [t for t in titoli_unici if t not in _classificazioni_data]

if titoli_sconosciuti:
    n_scon = len(titoli_sconosciuti)
    with st.expander(f"⚠️ {n_scon} titoli non classificati (Trading vs Investing)", expanded=True):
        st.info("Scegli se le operazioni su questi titoli sono da considerarsi Trading oppure Investing.")
        with st.form("classificazione_form"):
            scelte_class = {}
            for t in titoli_sconosciuti:
                sugg = classificazione.suggerisci_classificazione(t, _df_eq_prescan)
                st.markdown(f"**{t}**")
                scelte_class[t] = st.radio(
                    "Classe",
                    options=["Trading", "Investing"],
                    index=0 if sugg == "Trading" else 1,
                    key=f"class_{t}",
                    horizontal=True,
                    label_visibility="collapsed"
                )
                st.divider()
                
            if st.form_submit_button("💾 Salva Classificazione", type="primary"):
                create_auto_backup("pre_classificazione")
                _new_class = dict(_classificazioni_data)
                _new_class.update(scelte_class)
                classificazione.save_classificazioni(_new_class)
                st.rerun()


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
                create_auto_backup("pre_alias_save")
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
    """Costruisce DataFrame storico per tutti gli anni (Equity + CFD)."""
    df_eq_all = risultati["df_equity_all"]
    righe = []
    for anno in anni_disponibili:
        df_eq  = df_eq_all[df_eq_all["Anno"] == anno] if not df_eq_all.empty else pd.DataFrame()
        pm_eq  = df_eq[f"Plus/Minus (€) {metodo}"].sum() if not df_eq.empty else 0.0
        pm_cfd = _conto_pnl_per_anno.get(anno, 0.0)
        righe.append({
            "Anno":              anno,
            "Plus/Minus Equity": round(pm_eq, 2),
            "Plus/Minus CFD":    round(pm_cfd, 2),
            "Plus/Minus Totale": round(pm_eq + pm_cfd, 2),
        })
    return pd.DataFrame(righe)


# ============================================================
# TAB NAVIGATION
# ============================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📋 Anno Selezionato",
    "📈 Storico Multi-Anno",
    "📉 CFD / Derivati",
    "🔄 Trading vs Investing",
    "🔍 Dettaglio Movimenti",
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
    # Filtro per data all'interno dell'anno selezionato
    # ----------------------------------------------------------
    import datetime as _dt
    _date_min_t1 = _dt.date(anno_sel, 1, 1)
    _date_max_t1 = _dt.date(anno_sel, 12, 31)
    # Impostiamo di default il filtro all'intero anno per non scartare
    # operazioni (es. CFD) che potrebbero avvenire al di fuori del range
    # min-max limitato ai soli movimenti equity.

    with st.expander("🗓️ Filtra per data", expanded=False):
        _fc0, _fc1, _fc2 = st.columns([1, 4, 4])
        if _fc0.button("🔄 Reset", key="reset_date_tab1", help="Azzera il filtro date all'anno intero", use_container_width=True):
            st.session_state["data_da_tab1"] = _date_min_t1
            st.session_state["data_a_tab1"] = _date_max_t1
            st.rerun()
        _data_da_t1 = _fc1.date_input(
            "Dal", value=_date_min_t1,
            min_value=_dt.date(anno_sel, 1, 1),
            max_value=_dt.date(anno_sel, 12, 31),
            format="DD/MM/YYYY",
            key="data_da_tab1",
        )
        _data_a_t1 = _fc2.date_input(
            "Al", value=_date_max_t1,
            min_value=_dt.date(anno_sel, 1, 1),
            max_value=_dt.date(anno_sel, 12, 31),
            format="DD/MM/YYYY",
            key="data_a_tab1",
        )
    _filtro_data_attivo_t1 = (
        _data_da_t1 != _dt.date(anno_sel, 1, 1)
        or _data_a_t1 != _dt.date(anno_sel, 12, 31)
    )
    
    _riepilogo_cfd_tab1 = pd.DataFrame()
    
    if _filtro_data_attivo_t1:
        # 1. Filtro Equity
        if not df_eq.empty and "Data Vendita" in df_eq.columns:
            _dv_dates = pd.to_datetime(df_eq["Data Vendita"], format="%d/%m/%Y", errors="coerce").dt.date
            df_eq = df_eq[
                (_dv_dates >= _data_da_t1)
                & (_dv_dates <= _data_a_t1)
            ].copy()
            # Ricalcola totali sul filtro date
            totali_eq["corrispettivi_eur"]   = df_eq["Controvalore Vendita (€)"].sum()
            totali_eq["costi_eur_cmp"]       = df_eq["Costo Carico (€) CMP"].sum()
            totali_eq["costi_eur_lifo"]      = df_eq["Costo Carico (€) LIFO"].sum()
            totali_eq["plus_minus_eur_cmp"]  = df_eq["Plus/Minus (€) CMP"].sum()
            totali_eq["plus_minus_eur_lifo"] = df_eq["Plus/Minus (€) LIFO"].sum()

        # 2. Filtro CFD
        if _risultati_conto is not None:
            _det_t1 = _risultati_conto["dettaglio"]
            if not _det_t1.empty and "Anno" in _det_t1.columns and "Data valuta" in _det_t1.columns:
                _det_t1_filt = _det_t1[
                    (_det_t1["Anno"] == anno_sel)
                    & (_det_t1["Data valuta"].dt.date >= _data_da_t1)
                    & (_det_t1["Data valuta"].dt.date <= _data_a_t1)
                ]
                _cfd_pnl_filt = _det_t1_filt["PnL (€)"].sum() if "PnL (€)" in _det_t1_filt.columns else 0.0
                totali_cfd["pnl_totale_eur"] = _cfd_pnl_filt
                _riepilogo_cfd_tab1 = riepilogo_per_anno_strumento(_det_t1_filt)
            else:
                totali_cfd["pnl_totale_eur"] = 0.0
        else:
            totali_cfd["pnl_totale_eur"] = 0.0

        # Ricalcola Quadro RT con dati filtrati
        rt = calcola_quadro_rt(
            totali_equity=totali_eq,
            totali_cfd=totali_cfd,
            minusvalenze_pregresse=minus_pregresse,
            metodo=metodo,
        )

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
            if _filtro_data_attivo_t1:
                _riepilogo_anno = _riepilogo_cfd_tab1
            else:
                _riepilogo_conto = _risultati_conto["riepilogo"]
                _riepilogo_anno  = (
                    _riepilogo_conto[_riepilogo_conto["Anno"] == anno_sel]
                    if not _riepilogo_conto.empty else pd.DataFrame()
                )
            if _riepilogo_anno.empty:
                st.info(f"Nessuna operazione CFD/Derivati nel periodo selezionato.")
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
        with st.expander(f"⚠️ {len(warnings)} operazioni con quantità mancante"):
            st.caption(
                "Per queste vendite la quantità in portafoglio era insufficiente "
                "(spesso accade per diritti assegnati gratuitamente o storico incompleto). "
                "È stato **assunto un costo di carico pari a ZERO** per la parte mancante, "
                "al fine di calcolare correttamente la plusvalenza."
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

    # ----------------------------------------------------------
    # Selettore visualizzazione
    # ----------------------------------------------------------
    _ha_cfd_storico = _risultati_conto is not None and bool(_conto_pnl_per_anno)
    _opzioni_vista = (
        ["🏦 Equity", "📉 CFD / Derivati", "📊 Totale (Equity + CFD)"]
        if _ha_cfd_storico
        else ["🏦 Equity"]
    )
    _vista = st.radio(
        "Visualizza",
        options=_opzioni_vista,
        horizontal=True,
        key="vista_storico",
    )

    # Mappa selezione → colonna DataFrame e testi UI
    if "CFD" in _vista and "Totale" not in _vista:
        _col_pm      = "Plus/Minus CFD"
        _titolo_bar  = "Plus/Minus annuale CFD / Derivati"
        _titolo_line = "Plus/Minus CFD cumulato nel periodo"
        _titolo_tab  = "📋 Riepilogo per anno — CFD / Derivati"
    elif "Totale" in _vista:
        _col_pm      = "Plus/Minus Totale"
        _titolo_bar  = f"Plus/Minus annuale Totale (Equity {metodo} + CFD)"
        _titolo_line = "Plus/Minus totale cumulato nel periodo"
        _titolo_tab  = "📋 Riepilogo per anno — Totale"
    else:
        _col_pm      = "Plus/Minus Equity"
        _titolo_bar  = f"Plus/Minus annuale Equity — {metodo}"
        _titolo_line = f"Plus/Minus Equity cumulato ({metodo})"
        _titolo_tab  = "📋 Riepilogo per anno — solo Equity"

    # ----------------------------------------------------------
    # Range anni
    # ----------------------------------------------------------
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

    df_storico_filt["Cumulato"] = df_storico_filt[_col_pm].cumsum().round(2)

    # ----------------------------------------------------------
    # KPI riepilogo range
    # ----------------------------------------------------------
    _tot_eq_rng  = df_storico_filt["Plus/Minus Equity"].sum()
    _tot_cfd_rng = df_storico_filt["Plus/Minus CFD"].sum() if "Plus/Minus CFD" in df_storico_filt.columns else 0.0
    n_anni       = len(df_storico_filt)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Equity periodo", f"{_tot_eq_rng:+,.2f} €", help=f"Metodo {metodo}")
    if _ha_cfd_storico:
        k2.metric("CFD / Derivati periodo", f"{_tot_cfd_rng:+,.2f} €")
        k3.metric("Totale periodo", f"{(_tot_eq_rng + _tot_cfd_rng):+,.2f} €")
    else:
        k2.metric("CFD / Derivati", "n/d", help="Carica il file conto per includere i CFD")
        k3.metric("Totale periodo", f"{_tot_eq_rng:+,.2f} €", help="Solo equity")
    k4.metric("Anni nel range", str(n_anni))

    st.divider()

    # ----------------------------------------------------------
    # Bar chart: plus/minus annuale
    # ----------------------------------------------------------
    fig_bar = px.bar(
        df_storico_filt,
        x="Anno",
        y=_col_pm,
        color=_col_pm,
        color_continuous_scale=["#d62728", "#aec7e8", "#2ca02c"],
        color_continuous_midpoint=0,
        labels={_col_pm: "Plus/Minus (€)", "Anno": "Anno"},
        title=_titolo_bar,
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
        title=_titolo_line,
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
    st.subheader(_titolo_tab)
    _rename_map = {
        "Plus/Minus Equity":  f"Equity ({metodo}) (€)",
        "Plus/Minus CFD":     "CFD / Derivati (€)",
        "Plus/Minus Totale":  "Totale (€)",
        "Cumulato":           "Cumulato (€)",
    }
    _cols_tab = ["Anno", "Plus/Minus Equity"]
    if _ha_cfd_storico:
        _cols_tab += ["Plus/Minus CFD", "Plus/Minus Totale"]
    _cols_tab.append("Cumulato")
    st.dataframe(
        df_storico_filt[_cols_tab].rename(columns=_rename_map),
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

    # Filtro per data all'interno dell'anno CFD selezionato
    import datetime as _dt
    _det_anno_cfd = (
        _det_conto[_det_conto["Anno"] == _anno_cfd].copy()
        if not _det_conto.empty else pd.DataFrame()
    )
    _cfd_date_min = _dt.date(_anno_cfd, 1, 1)
    _cfd_date_max = _dt.date(_anno_cfd, 12, 31)
    # Impostiamo di default il range all'intero anno per coerenza

    with st.expander("🗓️ Filtra per data", expanded=False):
        _cfd_fc0, _cfd_fc1, _cfd_fc2 = st.columns([1, 4, 4])
        if _cfd_fc0.button("🔄 Reset", key="reset_date_tab3", help="Azzera il filtro date all'anno intero", use_container_width=True):
            st.session_state["data_da_tab3"] = _cfd_date_min
            st.session_state["data_a_tab3"] = _cfd_date_max
            st.rerun()
        _cfd_da = _cfd_fc1.date_input(
            "Dal", value=_cfd_date_min,
            min_value=_dt.date(_anno_cfd, 1, 1),
            max_value=_dt.date(_anno_cfd, 12, 31),
            format="DD/MM/YYYY",
            key="data_da_tab3",
        )
        _cfd_a = _cfd_fc2.date_input(
            "Al", value=_cfd_date_max,
            min_value=_dt.date(_anno_cfd, 1, 1),
            max_value=_dt.date(_anno_cfd, 12, 31),
            format="DD/MM/YYYY",
            key="data_a_tab3",
        )

    _riepi_anno = (
        _riepi_conto[_riepi_conto["Anno"] == _anno_cfd].copy()
        if not _riepi_conto.empty else pd.DataFrame()
    )
    # Applica filtro date al dettaglio (per KPI e tabella strumenti)
    if not _det_anno_cfd.empty and "Data valuta" in _det_anno_cfd.columns:
        _det_anno_cfd_filt = _det_anno_cfd[
            (_det_anno_cfd["Data valuta"].dt.date >= _cfd_da)
            & (_det_anno_cfd["Data valuta"].dt.date <= _cfd_a)
        ].copy()
    else:
        _det_anno_cfd_filt = _det_anno_cfd.copy()

    # Riepilogo filtrato per data — usato da KPI, grafico e tabella
    _riepi_uso = riepilogo_per_anno_strumento(_det_anno_cfd_filt)

    # ----------------------------------------------------------
    # KPI anno CFD
    # ----------------------------------------------------------
    _tot_margine = _riepi_uso["Margine variazione (€)"].sum() if not _riepi_uso.empty else 0.0
    _tot_oneri   = _riepi_uso["Oneri/Proventi (€)"].sum()     if not _riepi_uso.empty else 0.0
    _tot_cfd     = _riepi_uso["Totale (€)"].sum()             if not _riepi_uso.empty else 0.0

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
    if not _riepi_uso.empty:
        _chart_data = _riepi_uso.sort_values("Totale (€)", ascending=True)

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
    if _riepi_uso.empty:
        st.info(f"Nessuna operazione CFD/Derivati nel periodo selezionato.")
    else:
        _cols_tbl = [c for c in [
            "Strumento",
            "Margine variazione (€)",
            "Oneri/Proventi (€)",
            "Totale (€)",
        ] if c in _riepi_uso.columns]
        st.dataframe(
            _riepi_uso[_cols_tbl],
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


# ============================================================
# TAB 4 — Trading vs Investing
# ============================================================
with tab4:
    st.header("🔄 Analisi Trading vs Investing")
    st.write("Confronto performance tra operatività a breve termine (Trading) e lungo termine (Investing). I titoli azionari non ancora classificati verranno richiesti all'apertura dell'app, mentre le operazioni CFD ricadono nativamente nel comparto Trading.")

    # ----------------------------------------------------------
    # Filtro anno + date per Tab4
    # ----------------------------------------------------------
    import datetime as _dt
    _t4_anni = sorted(anni_disponibili, reverse=True)
    _t4_col1, _t4_col2 = st.columns([1, 3])
    with _t4_col1:
        _t4_anno = st.selectbox(
            "📅 Anno",
            options=["Tutti"] + [str(a) for a in _t4_anni],
            index=0,
            key="anno_tab4",
        )
    with _t4_col2:
        if _t4_anno != "Tutti":
            _t4_anno_int = int(_t4_anno)
            with st.expander("🗓️ Filtra per data", expanded=False):
                _t4_dc0, _t4_dc1, _t4_dc2 = st.columns([1, 4, 4])
                
                _date_min_t4 = _dt.date(_t4_anno_int, 1, 1)
                _date_max_t4 = _dt.date(_t4_anno_int, 12, 31)

                if _t4_dc0.button("🔄 Reset", key="reset_date_tab4", help="Azzera il filtro date all'anno intero", use_container_width=True):
                    st.session_state["data_da_tab4"] = _date_min_t4
                    st.session_state["data_a_tab4"] = _date_max_t4
                    st.rerun()
                
                _t4_da = _t4_dc1.date_input(
                    "Dal", value=_date_min_t4,
                    min_value=_date_min_t4,
                    max_value=_date_max_t4,
                    format="DD/MM/YYYY",
                    key="data_da_tab4",
                )
                _t4_a = _t4_dc2.date_input(
                    "Al", value=_dt.date(_t4_anno_int, 12, 31),
                    min_value=_dt.date(_t4_anno_int, 1, 1),
                    max_value=_dt.date(_t4_anno_int, 12, 31),
                    format="DD/MM/YYYY",
                    key="data_a_tab4",
                )
        else:
            _t4_da = None
            _t4_a = None
    
    # Costruiamo un dataset combinato per gli anni disponibili
    righe_ti = []
    df_eq_all = risultati.get("df_equity_all", pd.DataFrame())

    # Anni da includere in base al filtro
    _anni_t4 = [int(_t4_anno)] if _t4_anno != "Tutti" else anni_disponibili

    for anno in _anni_t4:
        # Equity
        if not df_eq_all.empty and "Classe" in df_eq_all.columns:
            df_eq_anno = df_eq_all[df_eq_all["Anno"] == anno].copy()
            # Applica filtro date se attivo
            if _t4_da is not None and _t4_a is not None and "Data Vendita" in df_eq_anno.columns:
                _dv_t4 = pd.to_datetime(df_eq_anno["Data Vendita"], format="%d/%m/%Y", errors="coerce").dt.date
                df_eq_anno = df_eq_anno[
                    (_dv_t4 >= _t4_da)
                    & (_dv_t4 <= _t4_a)
                ]
            pnl_eq_inv = df_eq_anno[df_eq_anno["Classe"] == "Investing"][f"Plus/Minus (€) {metodo}"].sum()
            pnl_eq_trad = df_eq_anno[df_eq_anno["Classe"] == "Trading"][f"Plus/Minus (€) {metodo}"].sum()
        else:
            pnl_eq_inv = 0.0
            pnl_eq_trad = 0.0

        # CFD (tutto trading) — filtrabile per data se dettaglio disponibile
        if _risultati_conto is not None and _t4_da is not None and _t4_a is not None:
            _det_t4 = _risultati_conto["dettaglio"]
            if not _det_t4.empty and "Anno" in _det_t4.columns and "Data valuta" in _det_t4.columns:
                _det_t4_filt = _det_t4[
                    (_det_t4["Anno"] == anno)
                    & (_det_t4["Data valuta"].dt.date >= _t4_da)
                    & (_det_t4["Data valuta"].dt.date <= _t4_a)
                ]
                pnl_cfd = _det_t4_filt["PnL (€)"].sum() if "PnL (€)" in _det_t4_filt.columns else 0.0
            else:
                pnl_cfd = _conto_pnl_per_anno.get(anno, 0.0)
        else:
            pnl_cfd = _conto_pnl_per_anno.get(anno, 0.0)
        pnl_trad_tot = pnl_eq_trad + pnl_cfd

        righe_ti.append({
            "Anno": str(anno),
            "Classe": "Investing",
            "PnL": round(float(pnl_eq_inv), 2)
        })
        righe_ti.append({
            "Anno": str(anno),
            "Classe": "Trading",
            "PnL": round(float(pnl_trad_tot), 2)
        })

    if righe_ti:
        df_ti = pd.DataFrame(righe_ti)
        
        col_t1, col_t2 = st.columns(2)
        tot_trad = df_ti[df_ti["Classe"] == "Trading"]["PnL"].sum()
        tot_inv = df_ti[df_ti["Classe"] == "Investing"]["PnL"].sum()
        
        with col_t1:
            st.metric("Totale Storico Trading", f"{tot_trad:+,.2f} €")
        with col_t2:
            st.metric("Totale Storico Investing", f"{tot_inv:+,.2f} €")
            
        fig_ti = px.bar(
            df_ti, x="Anno", y="PnL", color="Classe", barmode="group",
            title="Trading vs Investing PnL per Anno",
            color_discrete_map={"Trading": "#e377c2", "Investing": "#1f77b4"},
            template="plotly_white"
        )
        st.plotly_chart(fig_ti, use_container_width=True)
        
        st.dataframe(
            df_ti.pivot(index="Anno", columns="Classe", values="PnL").reset_index(),
            use_container_width=True, hide_index=True
        )
    else:
        st.info("Nessun dato disponibile.")

# ============================================================
# TAB 5 — Dettaglio Movimenti
# ============================================================
with tab5:
    st.header("🔍 Dettaglio Movimenti")
    st.write("Vista analitica delle operazioni di chiusura, con data di apertura e chiusura esatte, per facilitare la rendicontazione (es. Dichiarazione dei Redditi).")

    # Recupera i matches equity
    df_matches = risultati.get("matches_equity", pd.DataFrame())
    
    # Recupera i movimenti CFD
    df_cfd_det = _risultati_conto["dettaglio"] if _risultati_conto is not None else pd.DataFrame()
    
    # Prepara dataset unificato
    righe_det = []
    
    # Aggiungi Equity Matches
    if not df_matches.empty:
        for _, r in df_matches.iterrows():
            righe_det.append({
                "Strumento": "Equity",
                "ISIN": r["ISIN"],
                "Titolo": r["Titolo"],
                "Data Apertura": pd.to_datetime(r["Data Apertura"], format="%d/%m/%Y").date(),
                "Data Chiusura": pd.to_datetime(r["Data Chiusura"], format="%d/%m/%Y").date(),
                "Anno": r["Anno"],
                "Quantità": r["Quantità"],
                "Divisa": r["Divisa"],
                "Costo LIFO (€)": r["Costo Carico (€) LIFO"],
                "Costo CMP (€)": r["Costo Carico (€) CMP"],
                "Corrispettivo (€)": r["Controvalore Vendita (€)"],
                "PnL LIFO (€)": r["Plus/Minus (€) LIFO"],
                "PnL CMP (€)": r["Plus/Minus (€) CMP"],
                "Costo Orig LIFO": r.get("Costo Carico (" + r["Divisa"] + ") LIFO", r["Costo Carico (€) LIFO"]),
                "Costo Orig CMP": r.get("Costo Carico (" + r["Divisa"] + ") CMP", r["Costo Carico (€) CMP"]),
                "Corrispettivo Orig": r.get("Controvalore Vendita (" + r["Divisa"] + ")", r["Controvalore Vendita (€)"]),
            })
            
    # Aggiungi CFD
    if not df_cfd_det.empty:
        for _, r in df_cfd_det.iterrows():
            d_val = pd.to_datetime(r["Data valuta"]).date() if pd.notna(r["Data valuta"]) else None
            if d_val:
                righe_det.append({
                    "Strumento": "CFD",
                    "ISIN": "N/D",
                    "Titolo": r["Strumento"],
                    "Data Apertura": d_val,
                    "Data Chiusura": d_val,
                    "Anno": r["Anno"],
                    "Quantità": None, # Non disponibile nel file Fineco per i CFD (margini)
                    "Divisa": "EUR",
                    "Costo LIFO (€)": 0.0,
                    "Costo CMP (€)": 0.0,
                    "Corrispettivo (€)": r["PnL (€)"], # Per semplificare mostriamo il netto
                    "PnL LIFO (€)": r["PnL (€)"],
                    "PnL CMP (€)": r["PnL (€)"],
                    "Costo Orig LIFO": 0.0,
                    "Costo Orig CMP": 0.0,
                    "Corrispettivo Orig": r["PnL (€)"],
                })

    if not righe_det:
        st.info("Nessun movimento da visualizzare.")
    else:
        df_unificato = pd.DataFrame(righe_det)
        
        # Filtri
        col_f1, col_f2, col_f3, col_f4 = st.columns(4)
        
        with col_f1:
            anni_disp = sorted(df_unificato["Anno"].dropna().unique().tolist(), reverse=True)
            filtro_anno = st.selectbox("📅 Anno", options=["Tutti"] + anni_disp, key="t5_anno")
        
        with col_f2:
            filtro_strum = st.selectbox("📈 Tipo Strumento", options=["Entrambi", "Solo Equity", "Solo CFD"], key="t5_strum")
        
        with col_f3:
            import datetime as _dt
            min_date = df_unificato["Data Chiusura"].min()
            max_date = df_unificato["Data Chiusura"].max()
            date_range = st.date_input("🗓️ Range Date (Chiusura)", value=(min_date, max_date), min_value=min_date, max_value=max_date, key="t5_dates")
        
        with col_f4:
            titoli_disp = sorted(df_unificato["Titolo"].dropna().unique().tolist())
            filtro_titoli = st.multiselect("📌 Filtra Titoli", options=titoli_disp, key="t5_titoli")
            
        # Applica filtri
        df_filt = df_unificato.copy()
        
        if filtro_anno != "Tutti":
            df_filt = df_filt[df_filt["Anno"] == filtro_anno]
            
        if filtro_strum == "Solo Equity":
            df_filt = df_filt[df_filt["Strumento"] == "Equity"]
        elif filtro_strum == "Solo CFD":
            df_filt = df_filt[df_filt["Strumento"] == "CFD"]
            
        if len(date_range) == 2:
            df_filt = df_filt[(df_filt["Data Chiusura"] >= date_range[0]) & (df_filt["Data Chiusura"] <= date_range[1])]
            
        if filtro_titoli:
            df_filt = df_filt[df_filt["Titolo"].isin(filtro_titoli)]
            
        # Scegli le colonne in base al metodo (LIFO/CMP)
        col_costo = f"Costo {metodo} (€)"
        col_pnl = f"PnL {metodo} (€)"
        col_costo_orig = f"Costo Orig {metodo}"
        
        df_disp = pd.DataFrame()
        df_disp["Titolo"] = df_filt["Titolo"]
        df_disp["ISIN"] = df_filt["ISIN"]
        df_disp["Data Apertura"] = pd.to_datetime(df_filt["Data Apertura"]).dt.strftime("%d/%m/%Y")
        df_disp["Data Chiusura"] = pd.to_datetime(df_filt["Data Chiusura"]).dt.strftime("%d/%m/%Y")
        df_disp["Quantità"] = df_filt["Quantità"]
        df_disp["Valore Iniziale (€)"] = df_filt[f"Costo {metodo} (€)"]
        df_disp["Valore Finale (€)"] = df_filt["Corrispettivo (€)"]
        df_disp["PnL (€)"] = df_filt[f"PnL {metodo} (€)"]
        df_disp["Divisa"] = df_filt["Divisa"]
        df_disp["Val. Iniz. (Orig)"] = df_filt[f"Costo Orig {metodo}"]
        df_disp["Val. Fin. (Orig)"] = df_filt["Corrispettivo Orig"]
        df_disp["Tipo"] = df_filt["Strumento"]
        
        # Formattazione
        for col in ["Valore Iniziale (€)", "Valore Finale (€)", "PnL (€)", "Val. Iniz. (Orig)", "Val. Fin. (Orig)"]:
            df_disp[col] = df_disp[col].round(2)
            
        # Summary
        tot_pnl = df_disp["PnL (€)"].sum()
        st.metric(f"Totale PnL Filtrato ({metodo})", f"{tot_pnl:+,.2f} €")
        
        st.dataframe(df_disp, use_container_width=True, hide_index=True)

