"""
app.py
------
Interfaccia Streamlit per il calcolo tasse Fineco.
Avvio:
    streamlit run app.py
"""

import io
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from core.loader import load_config, load_excel, classifica_operazioni
from core.engine_equity import EngineEquity
from core.engine_cfd import EngineCFD
from core.report import calcola_quadro_rt, esporta_excel

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
# Sidebar — Parametri fissi
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
# Upload file
# ============================================================
uploaded_file = st.file_uploader(
    "Carica il file Excel di movimentazione",
    type=["xlsx", "xls"],
    help="Esporta da Fineco: Portafoglio → Movimentazione → Esporta Excel",
)

if not uploaded_file:
    st.info("👆 Carica il file Excel per iniziare.")
    st.stop()

# ============================================================
# Elaborazione (con cache per evitare ricalcoli)
# Processa TUTTO il dataset una volta sola → filtro anno a valle
# ============================================================
@st.cache_data(show_spinner="Elaborazione in corso...")
def elabora_tutto(file_bytes: bytes, metodo: str) -> dict:
    """Esegue la pipeline completa su tutti gli anni e restituisce i motori serializzati."""
    config = load_config()
    df = load_excel(file_bytes, config)
    dataset = classifica_operazioni(df, config)

    # Equity — processa tutto
    engine_eq = EngineEquity()
    engine_eq.processa(dataset["equity"])

    # CFD — processa tutto
    engine_cfd = EngineCFD()
    if not dataset["cfd"].empty:
        engine_cfd.processa(dataset["cfd"])

    # Anni disponibili (unione equity + CFD)
    anni_eq = engine_eq.anni_disponibili()
    anni_cfd = engine_cfd.anni_disponibili()
    tutti_anni = sorted(set(anni_eq) | set(anni_cfd))

    return {
        # DataFrame completi (tutti gli anni)
        "df_equity_all": engine_eq.risultati_dataframe(),
        "df_cfd_all": engine_cfd.risultati_dataframe(),
        # Per-anno: costruiamo un dict anni → dati
        "anni": tutti_anni,
        "anni_eq": anni_eq,
        "anni_cfd": anni_cfd,
        # Funzioni serializzabili: salviamo records e li filtriamo dopo
        # (Streamlit cache non può serializzare le classi engine, passiamo i df)
        # DataFrames per il portafoglio e posizioni aperte
        "portafoglio": engine_eq.stato_portafoglio(),
        "posizioni_cfd_aperte": engine_cfd.posizioni_aperte(),
        "warnings_eq": engine_eq.warnings,
        "info_eq": engine_eq.info_log,
    }


try:
    file_bytes = uploaded_file.read()
    risultati = elabora_tutto(file_bytes, metodo)
except ValueError as e:
    st.error(f"❌ Errore nel file: {e}")
    st.stop()
except Exception as e:
    st.error(f"❌ Errore imprevisto: {e}")
    st.stop()

anni_disponibili = risultati["anni"]

if not anni_disponibili:
    st.warning("⚠️ Nessuna operazione trovata nel file.")
    st.stop()

# ============================================================
# Helper: calcola dati per un anno specifico
# ============================================================
def dati_per_anno(anno: int, metodo: str) -> dict:
    """Filtra i DataFrame per anno e calcola totali + Quadro RT."""
    df_eq_all = risultati["df_equity_all"]
    df_cfd_all = risultati["df_cfd_all"]

    # Filtro anno
    df_eq = df_eq_all[df_eq_all["Anno"] == anno].copy() if not df_eq_all.empty else pd.DataFrame()
    df_cfd = df_cfd_all[df_cfd_all["Anno"] == anno].copy() if not df_cfd_all.empty else pd.DataFrame()

    col_pm = f"Plus/Minus (€) {metodo}"
    totali_eq = {
        "corrispettivi_eur": df_eq["Controvalore Vendita (€)"].sum() if not df_eq.empty else 0.0,
        "costi_eur_cmp": df_eq["Costo Carico (€) CMP"].sum() if not df_eq.empty else 0.0,
        "costi_eur_lifo": df_eq["Costo Carico (€) LIFO"].sum() if not df_eq.empty else 0.0,
        "plus_minus_eur_cmp": df_eq["Plus/Minus (€) CMP"].sum() if not df_eq.empty else 0.0,
        "plus_minus_eur_lifo": df_eq["Plus/Minus (€) LIFO"].sum() if not df_eq.empty else 0.0,
    }
    totali_cfd = {
        "pnl_totale_eur": df_cfd["PnL (€)"].sum() if not df_cfd.empty else 0.0,
        "n_operazioni": len(df_cfd),
    }

    rt = calcola_quadro_rt(
        totali_equity=totali_eq,
        totali_cfd=totali_cfd,
        minusvalenze_pregresse=minus_pregresse,
        metodo=metodo,
    )
    return {"df_eq": df_eq, "df_cfd": df_cfd, "totali_eq": totali_eq, "totali_cfd": totali_cfd, "rt": rt}


def dati_storico(metodo: str) -> pd.DataFrame:
    """Costruisce DataFrame storico per tutti gli anni."""
    df_eq_all = risultati["df_equity_all"]
    df_cfd_all = risultati["df_cfd_all"]
    righe = []
    for anno in anni_disponibili:
        df_eq = df_eq_all[df_eq_all["Anno"] == anno] if not df_eq_all.empty else pd.DataFrame()
        df_cfd = df_cfd_all[df_cfd_all["Anno"] == anno] if not df_cfd_all.empty else pd.DataFrame()
        pm_eq = df_eq[f"Plus/Minus (€) {metodo}"].sum() if not df_eq.empty else 0.0
        pm_cfd = df_cfd["PnL (€)"].sum() if not df_cfd.empty else 0.0
        righe.append({
            "Anno": anno,
            "Plus/Minus Equity": round(pm_eq, 2),
            "PnL CFD": round(pm_cfd, 2),
            "Totale": round(pm_eq + pm_cfd, 2),
        })
    df = pd.DataFrame(righe)
    if not df.empty:
        df["Totale Cumulato"] = df["Totale"].cumsum().round(2)
    return df


# ============================================================
# TAB NAVIGATION
# ============================================================
tab1, tab2 = st.tabs(["📋 Anno Selezionato", "📈 Storico Multi-Anno"])


# ============================================================
# TAB 1 — Anno Selezionato
# ============================================================
with tab1:

    # Dropdown anno (basato sui dati reali del file)
    anno_default = max(anni_disponibili)
    anno_sel = st.selectbox(
        "📅 Anno di imposta",
        options=anni_disponibili[::-1],  # più recenti prima
        index=0,
        key="anno_tab1",
    )

    dati = dati_per_anno(anno_sel, metodo)
    df_eq = dati["df_eq"]
    df_cfd = dati["df_cfd"]
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
    plus_minus_cfd = totali_cfd.get("pnl_totale_eur", 0)
    plus_minus_totale = rt["rt23_plus_minus_anno"]

    with col1:
        st.metric("Plus/Minus Equity", f"{plus_minus_equity:+,.2f} €",
                  help=f"Metodo {metodo}")
    with col2:
        st.metric("PnL CFD", f"{plus_minus_cfd:+,.2f} €",
                  help="PnL netto su CFD/Futures")
    with col3:
        st.metric("Totale anno (RT23)", f"{plus_minus_totale:+,.2f} €",
                  help="Somma equity + CFD")
    with col4:
        st.metric("Imposta 26% (RT26)", f"{rt['rt26_imposta']:,.2f} €",
                  help="Calcolata sull'imponibile netto RT25")

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
            st.markdown("**Totale anno**")
            for k, v in {
                "PnL CFD netto": f"{rt['pnl_cfd']:+,.2f} €",
                "RT23 — Plus/Minus anno": f"{rt['rt23_plus_minus_anno']:+,.2f} €",
                "RT24 — Minus pregresse": f"{rt['rt24_minus_pregresse']:,.2f} €",
                "RT25 — Imponibile": f"{rt['rt25_imponibile']:,.2f} €",
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
        # Aggregazione per ISIN/Titolo
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

        # Bar chart per titolo
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

        # Tabella aggregata
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
            # Filtri rapidi
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
    # Tabella CFD
    # ----------------------------------------------------------
    with st.expander(f"📉 Operazioni CFD / Futures {anno_sel}", expanded=False):
        if df_cfd.empty:
            st.info("Nessuna operazione CFD in questo anno.")
        else:
            st.dataframe(df_cfd, use_container_width=True, hide_index=True)
            st.caption(f"{len(df_cfd)} chiusure CFD")

    # ----------------------------------------------------------
    # Portafoglio residuo e posizioni aperte
    # ----------------------------------------------------------
    portafoglio = risultati["portafoglio"]
    pos_aperte_cfd = risultati["posizioni_cfd_aperte"]

    col_p1, col_p2 = st.columns(2)
    with col_p1:
        with st.expander("💼 Portafoglio equity residuo"):
            if portafoglio.empty:
                st.info("Nessuna posizione equity aperta.")
            else:
                st.dataframe(portafoglio, use_container_width=True, hide_index=True)

    with col_p2:
        with st.expander(f"⚠️ {'%d posizioni' % len(pos_aperte_cfd) if not pos_aperte_cfd.empty else 'Nessuna posizione'} CFD aperte"):
            if pos_aperte_cfd.empty:
                st.info("Nessuna posizione CFD aperta.")
            else:
                st.dataframe(pos_aperte_cfd, use_container_width=True, hide_index=True)

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
        output_path = esporta_excel(
            df_equity=df_eq,
            df_cfd=df_cfd,
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

    # Range selezione anni
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

    # Ricalcola cumulato sul range filtrato
    df_storico_filt["Totale Cumulato"] = df_storico_filt["Totale"].cumsum().round(2)

    # ----------------------------------------------------------
    # KPI riepilogo range
    # ----------------------------------------------------------
    totale_range = df_storico_filt["Totale"].sum()
    eq_range = df_storico_filt["Plus/Minus Equity"].sum()
    cfd_range = df_storico_filt["PnL CFD"].sum()
    n_anni = len(df_storico_filt)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Totale periodo", f"{totale_range:+,.2f} €")
    k2.metric("Equity periodo", f"{eq_range:+,.2f} €")
    k3.metric("CFD periodo", f"{cfd_range:+,.2f} €")
    k4.metric("Anni nel range", str(n_anni))

    st.divider()

    # ----------------------------------------------------------
    # Bar chart: plus/minus annuale (equity + CFD)
    # ----------------------------------------------------------
    df_melt = df_storico_filt.melt(
        id_vars=["Anno"],
        value_vars=["Plus/Minus Equity", "PnL CFD"],
        var_name="Tipo",
        value_name="Valore",
    )
    fig_bar = px.bar(
        df_melt,
        x="Anno",
        y="Valore",
        color="Tipo",
        barmode="group",
        color_discrete_map={"Plus/Minus Equity": "#636EFA", "PnL CFD": "#EF553B"},
        labels={"Valore": "Plus/Minus (€)", "Anno": "Anno"},
        title=f"Plus/Minus annuale Equity vs CFD — {metodo}",
        template="plotly_white",
    )
    fig_bar.add_hline(y=0, line_width=1, line_color="gray")
    fig_bar.update_layout(legend_title_text="", margin=dict(l=0, r=20, t=40, b=20))
    st.plotly_chart(fig_bar, use_container_width=True)

    # ----------------------------------------------------------
    # Line chart: cumulato
    # ----------------------------------------------------------
    fig_line = go.Figure()
    fig_line.add_trace(go.Scatter(
        x=df_storico_filt["Anno"],
        y=df_storico_filt["Totale Cumulato"],
        mode="lines+markers",
        name="Cumulato",
        line=dict(color="#00CC96", width=2),
        marker=dict(size=8),
        fill="tozeroy",
        fillcolor="rgba(0,204,150,0.1)",
    ))
    fig_line.add_hline(y=0, line_width=1, line_color="gray", line_dash="dot")
    fig_line.update_layout(
        title="Plus/Minus cumulato nel periodo",
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
    st.subheader("📋 Riepilogo per anno")
    st.dataframe(
        df_storico_filt.rename(columns={
            "Plus/Minus Equity": f"Equity ({metodo}) (€)",
            "PnL CFD": "PnL CFD (€)",
            "Totale": "Totale (€)",
            "Totale Cumulato": "Cumulato (€)",
        }),
        use_container_width=True,
        hide_index=True,
    )
