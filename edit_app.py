import os
import json

with open("app.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Imports
if "from core import classificazione" not in code:
    code = code.replace("from core import isin_alias", "from core import isin_alias, classificazione")

# 2. elabora_tutto signature and logic
if "class_bytes: bytes" not in code:
    code = code.replace(
        'def elabora_tutto(csv_bytes: bytes, alias_bytes: bytes = b"{}") -> dict:',
        'def elabora_tutto(csv_bytes: bytes, alias_bytes: bytes = b"{}", class_bytes: bytes = b"{}") -> dict:'
    )
    target_return = """    return {
        "df_equity_all": engine_eq.risultati_dataframe(),"""
    replacement_return = """    df_eq_all = engine_eq.risultati_dataframe()
    if not df_eq_all.empty:
        class_map = json.loads(class_bytes)
        df_eq_all["Classe"] = df_eq_all["Titolo"].apply(lambda t: class_map.get(t, "Investing"))

    return {
        "df_equity_all": df_eq_all,"""
    code = code.replace(target_return, replacement_return)

# 3. Call elabora_tutto
if "class_bytes" in code and "_classificazioni_bytes" not in code:
    target_call = """try:
    risultati = elabora_tutto(csv_bytes, _alias_bytes)
except ValueError as e:"""
    replacement_call = """
_classificazioni_data = classificazione.load_classificazioni()
_classificazioni_bytes = json.dumps(_classificazioni_data, sort_keys=True).encode()

try:
    risultati = elabora_tutto(csv_bytes, _alias_bytes, _classificazioni_bytes)
except ValueError as e:"""
    code = code.replace(target_call, replacement_call)

# 4. Insert UI for classificazione right after alias UI or before it (around line 430)
if "Sezione classificazione:" not in code:
    target_orphan = """_config_alias = load_config()
_anno_alias_from = _config_alias.get("isin_alias_check_from_anno", 2025)
_df_eq_prescan = _prescan_equity(csv_bytes)"""
    replacement_orphan = """_config_alias = load_config()
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
                _new_class = dict(_classificazioni_data)
                _new_class.update(scelte_class)
                classificazione.save_classificazioni(_new_class)
                st.rerun()
"""
    code = code.replace(target_orphan, replacement_orphan)

# 5. Add TAB 4
if "tab4 =" not in code:
    target_tabs = """tab1, tab2, tab3 = st.tabs([
    "📋 Anno Selezionato",
    "📈 Storico Multi-Anno",
    "📉 CFD / Derivati",
])"""
    replacement_tabs = """tab1, tab2, tab3, tab4 = st.tabs([
    "📋 Anno Selezionato",
    "📈 Storico Multi-Anno",
    "📉 CFD / Derivati",
    "🔄 Trading vs Investing",
])"""
    code = code.replace(target_tabs, replacement_tabs)


# 6. Tab 4 logic at the bottom of the file
if "TAB 4 — Trading vs Investing" not in code:
    append_tab4 = """

# ============================================================
# TAB 4 — Trading vs Investing
# ============================================================
with tab4:
    st.header("🔄 Analisi Trading vs Investing")
    st.write("Confronto performance tra operatività a breve termine (Trading) e lungo termine (Investing). I titoli azionari non ancora classificati verranno richiesti all'apertura dell'app, mentre le operazioni CFD ricadono nativamente nel comparto Trading.")
    
    # Costruiamo un dataset combinato per gli anni disponibili
    righe_ti = []
    df_eq_all = risultati.get("df_equity_all", pd.DataFrame())
    for anno in anni_disponibili:
        # Equity
        if not df_eq_all.empty and "Classe" in df_eq_all.columns:
            df_eq_anno = df_eq_all[df_eq_all["Anno"] == anno]
            
            pnl_eq_inv = df_eq_anno[df_eq_anno["Classe"] == "Investing"][f"Plus/Minus (€) {metodo}"].sum()
            pnl_eq_trad = df_eq_anno[df_eq_anno["Classe"] == "Trading"][f"Plus/Minus (€) {metodo}"].sum()
        else:
            pnl_eq_inv = 0.0
            pnl_eq_trad = 0.0
            
        # CFD (tutto trading)
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
"""
    code += append_tab4

with open("app.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Done editing app.py")
