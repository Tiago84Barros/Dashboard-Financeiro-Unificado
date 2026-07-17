"""
views/empresas_americanas.py
Seção Empresas Americanas (NYSE/Nasdaq/AMEX) — inspirada em Empresas B3 / FIIs.

OFFLINE-FIRST: lê SÓ o warehouse local (core.us_data). Nunca chama API externa
(SEC EDGAR/yfinance são só de ingestão). Sem dados, mostra estado vazio e
instruções de sincronização — a UI não quebra.

Fase atual (F2–F4): Visão Geral, Explorar, Qualidade dos Dados, Sincronização e
Metodologia estão funcionais. As abas analíticas (score, comparação, portfólio,
backtests, dossiê, fora da curva) entram nas fases seguintes.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import core.us_data as us
from core.us_methodology import (
    US_ASYMMETRY_SCORE_VERSION,
    US_FUNDAMENTAL_SCORE_VERSION,
    US_SCHEMA_VERSION,
)
from design.componentes import (
    badge_status,
    card_metrica,
    container_pagina,
    em_construcao,
    estado_vazio,
    secao_titulo,
)


def _fmt_dt(value) -> str:
    if value is None:
        return "—"
    try:
        return pd.to_datetime(value).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(value)


def _status_badges(status: dict) -> None:
    col1, col2, col3, *_ = st.columns([1.2, 1.4, 4])
    with col1:
        if not status.get("schema_ready"):
            badge_status("Schema ausente", "erro")
        elif status.get("offline"):
            badge_status("Sem dados locais", "alerta")
        else:
            badge_status("Dados locais", "sucesso")
    with col2:
        badge_status(f"{status.get('companies', 0)} empresas", "info")
    if status.get("last_update"):
        with col3:
            badge_status(f"Última atualização: {_fmt_dt(status['last_update'])}", "neutro")


def render() -> None:
    container_pagina(
        "Empresas Americanas",
        "Análise fundamentalista de empresas dos EUA — NYSE, Nasdaq e NYSE American",
        "🌎",   # não usar 🇺🇸: Windows não renderiza bandeiras (vira "US")
    )

    status = us.data_status()
    _status_badges(status)

    if status.get("reason"):
        st.caption(f"ℹ️ {status['reason']}")
    st.markdown("<br>", unsafe_allow_html=True)

    # "Empresas Fora da Curva" NÃO é aba daqui: é seção própria no menu
    # (views/empresas_fora_da_curva.py). Propósitos distintos — esta seção é a
    # análise fundamentalista/carteira; aquela é a trilha assimétrica experimental.
    abas = st.tabs([
        "Visão Geral", "Explorar", "Análise Fundamentalista", "Análise Avançada",
        "Comparação por Indústria", "Criação de Portfólio", "Backtests",
        "Dossiê", "Qualidade dos Dados", "Sincronização", "Metodologia",
    ])

    with abas[0]:
        _tab_visao_geral()
    with abas[1]:
        _tab_explorar()
    with abas[2]:
        _tab_analise_fundamentalista(status)
    with abas[3]:
        _tab_analise_avancada(status)
    with abas[4]:
        _tab_comparacao_industria(status)
    with abas[5]:
        _tab_portfolio(status)
    with abas[6]:
        _tab_backtests(status)
    with abas[7]:
        _tab_dossie(status)
    with abas[8]:
        _tab_qualidade()
    with abas[9]:
        _tab_sincronizacao(status)
    with abas[10]:
        _tab_metodologia()


# ── Visão Geral ───────────────────────────────────────────────────────────────
def _tab_visao_geral() -> None:
    ov = us.overview()
    if ov.get("companies", 0) == 0:
        estado_vazio(
            "Nenhuma empresa americana no warehouse local ainda. Rode a carga "
            "inicial (aba Sincronização) para popular o banco local.", "🌎")
        return
    secao_titulo("Cobertura do warehouse local", "📊")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Empresas", f"{ov['companies']:,}".replace(",", "."))
    with c2:
        card_metrica("Ativos (tickers)", f"{ov['assets']:,}".replace(",", "."))
    with c3:
        card_metrica("Setores", str(ov["sectors"]))
    with c4:
        card_metrica("Com demonstrações", f"{ov['with_statements']:,}".replace(",", "."))
    c5, c6, c7, c8 = st.columns(4)
    with c5:
        card_metrica("REITs", str(ov["reits"]), ajuda="Tratamento específico (FFO/AFFO)")
    with c6:
        card_metrica("Deslistadas", str(ov["delisted"]),
                     ajuda="Mantidas no universo histórico (anti-survivorship)")
    with c7:
        card_metrica("Última atualização", _fmt_dt(ov["last_update"]))
    with c8:
        card_metrica("Schema", f"market_us v{US_SCHEMA_VERSION}")


# ── Explorar ──────────────────────────────────────────────────────────────────
def _tab_explorar() -> None:
    if not us.schema_ready():
        estado_vazio("Schema market_us ainda não aplicado.", "🔌")
        return
    col1, col2 = st.columns([2, 3])
    with col1:
        search = st.text_input("Buscar por ticker ou nome", key="us_explore_search")
    df = us.companies(search=search or None, limit=500)
    if df is None or df.empty:
        estado_vazio("Nenhuma empresa encontrada para o filtro atual.", "🔎")
        return
    st.caption(f"{len(df)} empresa(s) — leitura local, offline.")
    st.dataframe(
        df.rename(columns={
            "symbol": "Ticker", "name": "Nome", "sector": "Setor",
            "industry": "Indústria", "exchange": "Bolsa",
            "security_type": "Tipo", "is_reit": "REIT", "is_active": "Ativa",
            "cik": "CIK",
        }),
        hide_index=True, use_container_width=True,
    )


# ── Análise Fundamentalista (score por setor/indústria) ───────────────────────
_TRACK_LABELS = {
    "score_quality": "Qualidade", "score_growth": "Crescimento",
    "score_solidity": "Solidez", "score_capital_efficiency": "Efic. Capital",
    "score_valuation": "Valuation", "score_shareholder": "Retorno acionista",
}


def _tab_analise_fundamentalista(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para calcular o score.", "📊")
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        estado_vazio("Sem empresas com demonstrações suficientes para o score.", "📊")
        return
    secao_titulo("Score fundamentalista — relativo por indústria", "🏆")
    st.caption("Winsorização + percentil intra-indústria nas 6 trilhas de fatores. "
               "Ausência = neutro. Score não é garantia de retorno.")
    setores = ["(todos)"] + sorted(x for x in scored["sector"].dropna().unique())
    sel = st.selectbox("Setor", setores, key="us_score_sector")
    view = scored if sel == "(todos)" else scored[scored["sector"] == sel]
    cols = ["symbol", "name", "sector", "industry", "score",
            *_TRACK_LABELS.keys(), "coverage"]
    cols = [c for c in cols if c in view.columns]
    show = view[cols].head(200).rename(columns={
        "symbol": "Ticker", "name": "Nome", "sector": "Setor",
        "industry": "Indústria", "score": "Score", "coverage": "Cobertura %",
        **_TRACK_LABELS})
    st.dataframe(show, hide_index=True, use_container_width=True)


# ── Análise Avançada (Piotroski / Altman / Sloan / ROIC incremental) ──────────
_F_LABEL = {
    "roa_positivo": "ROA positivo", "cfo_positivo": "Caixa operacional positivo",
    "roa_crescente": "ROA crescente", "accruals_saudaveis": "CFO > lucro (accruals)",
    "alavancagem_caiu": "Alavancagem de longo prazo caiu",
    "liquidez_subiu": "Liquidez corrente subiu",
    "sem_emissao_acoes": "Sem emissão de ações",
    "margem_bruta_subiu": "Margem bruta subiu", "giro_ativos_subiu": "Giro de ativos subiu",
}
_ZONE_TIPO = {"segura": "sucesso", "cinzenta": "alerta", "aflição": "erro"}


def _tab_analise_avancada(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para a análise avançada.", "🔬")
        return
    symbol = st.text_input("Ticker (ex.: AAPL)", key="us_adv_symbol").strip().upper()
    if not symbol:
        st.info("Digite um ticker para Piotroski F-Score, Altman Z-Score, accruals "
                "de Sloan e retorno incremental sobre capital (offline).")
        return
    snap = us.advanced_snapshot(symbol)
    if not snap:
        estado_vazio(f"Sem histórico local para {symbol}.", "🔬")
        return

    secao_titulo(f"{symbol} — {snap.get('name') or ''}", "🔬", snap.get("sector") or "—")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        f = snap.get("f_score")
        card_metrica("Piotroski F-Score",
                     "—" if f is None else f"{f}/{snap.get('f_evaluable', 9)}",
                     ajuda="Critérios atendidos entre os avaliáveis (máx. 9)")
    with c2:
        z = snap.get("z_score")
        card_metrica("Altman Z-Score", "—" if z is None else f"{z:.2f}")
    with c3:
        a = snap.get("sloan_accruals")
        card_metrica("Accruals (Sloan)", "—" if a is None else f"{a:.3f}",
                     ajuda="(Lucro − CFO)/ativos médios. Menor é melhor.")
    with c4:
        ir = snap.get("incremental_roic")
        card_metrica("ROIC incremental", "—" if ir is None else f"{ir*100:.1f}%",
                     ajuda="ΔNOPAT / Δcapital investido — retorno do capital novo")

    if snap.get("z_zone"):
        badge_status(f"Zona {snap['z_zone']}", _ZONE_TIPO.get(snap["z_zone"], "neutro"))
    elif snap.get("z_score") is None:
        st.caption("Z-Score indisponível: exige `retained_earnings` e market cap no "
                   "warehouse (rode um refresh de fundamentos após a migration 043).")

    if snap.get("f_partial"):
        st.caption(f"⚠️ F-Score parcial: {snap.get('f_evaluable')} de 9 critérios "
                   "puderam ser avaliados. Critérios sem dado **não** contam como "
                   "atendidos.")

    secao_titulo("Critérios de Piotroski", "✅")
    for key, val in (snap.get("f_signals") or {}).items():
        icon = "✅" if val is True else "❌" if val is False else "➖"
        st.markdown(f"{icon} {_F_LABEL.get(key, key)}"
                    + ("  *(sem dado)*" if val is None else ""))


# ── Comparação por Indústria ──────────────────────────────────────────────────
def _tab_comparacao_industria(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para comparar.", "🏭")
        return
    scored = us.scored_universe()
    if scored is None or scored.empty or "industry" not in scored.columns:
        estado_vazio("Sem empresas suficientes para comparação por indústria.", "🏭")
        return
    industrias = sorted(x for x in scored["industry"].dropna().unique())
    if not industrias:
        estado_vazio("Nenhuma indústria classificada nos dados locais.", "🏭")
        return
    ind = st.selectbox("Indústria", industrias, key="us_cmp_industry")
    import core.us_score as _score
    peers = _score.industry_comparison(scored, ind)
    if peers.empty:
        estado_vazio("Sem pares nesta indústria.", "🏭")
        return
    secao_titulo(f"{ind} — {len(peers)} empresa(s)", "🏭")
    show_cols = ["symbol", "name", "score", "score_quality", "score_growth",
                 "score_valuation", "gross_margin", "roic", "net_debt_ebitda",
                 "revenue_cagr_3y"]
    show_cols = [c for c in show_cols if c in peers.columns]
    st.dataframe(peers[show_cols].rename(columns={
        "symbol": "Ticker", "name": "Nome", "score": "Score",
        "score_quality": "Qualidade", "score_growth": "Crescimento",
        "score_valuation": "Valuation", "gross_margin": "Mrg.Bruta",
        "roic": "ROIC", "net_debt_ebitda": "DL/EBITDA",
        "revenue_cagr_3y": "Cresc.Rec 3a"}),
        hide_index=True, use_container_width=True)


# ── Dossiê determinístico ─────────────────────────────────────────────────────
_CLASS_BADGE = {
    "consolidada": ("Consolidada", "sucesso"), "crescimento": ("Crescimento", "info"),
    "assimetrica": ("Assimétrica", "alerta"), "turnaround": ("Turnaround", "alerta"),
    "ciclica": ("Cíclica", "neutro"), "inadequada": ("Inadequada", "erro"),
}


def _tab_dossie(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para montar o dossiê.", "📄")
        return
    symbol = st.text_input("Ticker (ex.: AAPL)", key="us_dossie_symbol").strip().upper()
    if not symbol:
        st.info("Digite um ticker para o dossiê determinístico (offline).")
        return
    d = us.dossie(symbol)
    if d.get("erro"):
        estado_vazio(f"{symbol}: {d['erro']}", "📄")
        return

    label, tipo = _CLASS_BADGE.get(d.get("classification"), ("—", "neutro"))
    secao_titulo(f"{symbol} — {d.get('name') or ''}", "📄",
                 f"{d.get('sector') or '—'} / {d.get('industry') or '—'}")
    cb1, cb2, *_ = st.columns([1, 1, 4])
    with cb1:
        badge_status(label, tipo)
    with cb2:
        if d.get("score") is not None:
            badge_status(f"Score {d['score']}", "info")
    st.caption(d.get("classification_reason", ""))

    m = d.get("metrics", {})

    def _p(x):
        return "—" if x is None else f"{x*100:.1f}%"

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Margem líquida", _p(m.get("net_margin")))
    with c2:
        card_metrica("ROIC", _p(m.get("roic")))
    with c3:
        v = m.get("net_debt_ebitda")
        card_metrica("Dív.líq/EBITDA", "—" if v is None else f"{v:.1f}×")
    with c4:
        card_metrica("Cresc. receita 3a", _p(m.get("revenue_cagr_3y")))

    if d.get("red_flags"):
        secao_titulo("Sinais de alerta", "🚩")
        for f in d["red_flags"]:
            st.markdown(f"- {f}")

    notes = d.get("notes", {})
    if notes.get("tese") or notes.get("condicoes_invalidacao"):
        colt, coli = st.columns(2)
        with colt:
            st.markdown("**Tese**")
            for t in notes.get("tese", []):
                st.markdown(f"- {t}")
        with coli:
            st.markdown("**Condições de invalidação**")
            for c in notes.get("condicoes_invalidacao", []):
                st.markdown(f"- {c}")

    with st.expander("Dossiê completo (texto determinístico)"):
        import core.us_dossie as _dos
        st.code(_dos.dossie_to_text(d), language="text")


# ── Criação de Portfólio ──────────────────────────────────────────────────────
def _tab_portfolio(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para montar a carteira.", "📦")
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        estado_vazio("Sem empresas com score para compor a carteira.", "📦")
        return
    from core.us_portfolio import PortfolioConstraints, build_portfolio

    secao_titulo("Carteira-modelo americana", "📦")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        top_n = st.slider("Nº de ativos", 5, 40, 20, key="us_pf_topn")
    with c2:
        maxw = st.slider("Peso máx/ativo %", 5, 50, 10, key="us_pf_maxw") / 100
    with c3:
        maxs = st.slider("Peso máx/setor %", 15, 60, 30, key="us_pf_maxs") / 100
    with c4:
        wmode = st.selectbox("Ponderação", ["score", "equal"], key="us_pf_wmode")

    holdings = build_portfolio(scored, PortfolioConstraints(
        top_n=top_n, max_weight=maxw, max_sector_weight=maxs, weighting=wmode,
        max_assets=top_n, min_assets=min(5, top_n)))
    if holdings.empty:
        estado_vazio("Nenhum ativo elegível com as restrições atuais.", "📦")
        return

    show = holdings.copy()
    show["weight"] = (show["weight"] * 100).round(2)
    st.dataframe(show.rename(columns={
        "symbol": "Ticker", "name": "Nome", "sector": "Setor",
        "industry": "Indústria", "score": "Score", "weight": "Peso %"}),
        hide_index=True, use_container_width=True)

    if "sector" in holdings.columns:
        secao_titulo("Alocação por setor", "🧩")
        alloc = (holdings.groupby("sector")["weight"].sum() * 100).round(1) \
            .sort_values(ascending=False)
        st.dataframe(alloc.rename("Peso %").reset_index().rename(
            columns={"sector": "Setor"}), hide_index=True, use_container_width=True)
    st.caption("Capping iterativo por posição/setor (heurística de projeção, não "
               "otimizador de média-variância). Benchmarks (S&P 500 / Nasdaq-100 / "
               "Russell 2000 / equal-weight) entram no backtest quando houver histórico.")


# ── Backtests (point-in-time) ─────────────────────────────────────────────────
def _tab_backtests(status: dict) -> None:
    secao_titulo("Backtest walk-forward — point-in-time", "🧪")
    st.caption("Scores recomputados a cada data com available_at ≤ data (sem "
               "look-ahead). Requer histórico PIT: `python run_us_ingest.py "
               "score-history --warehouse`.")
    c1, c2 = st.columns(2)
    with c1:
        top_n = st.slider("Top N por período", 5, 40, 20, key="us_bt_topn")
    with c2:
        wmode = st.selectbox("Ponderação", ["score", "equal"], key="us_bt_wmode")

    res = us.backtest(top_n=top_n, weighting=wmode)
    if not res.get("ok"):
        estado_vazio(res.get("reason", "backtest indisponível"), "🧪")
        return

    ic = res["rank_ic"]
    p = res["portfolio"]

    def _p(x, mult=100, suf="%"):
        return "—" if x is None else f"{x*mult:.2f}{suf}"

    secao_titulo(f"Rank-IC ({res['n_periods']} períodos)", "📐")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Rank-IC médio", "—" if ic["mean"] is None else f"{ic['mean']:.3f}")
    with c2:
        card_metrica("t-stat", "—" if ic["t_stat"] is None else f"{ic['t_stat']:.2f}")
    with c3:
        card_metrica("p-valor", "—" if ic["p_value"] is None else f"{ic['p_value']:.3f}")
    with c4:
        card_metrica("Hit rate", _p(ic["hit_rate"]))

    secao_titulo("Desempenho da carteira vs equal-weight", "📈")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Retorno anual.", _p(p["ann_return"]))
    with c2:
        card_metrica("Excesso vs EW", _p(res.get("excess_ann_vs_ew")))
    with c3:
        card_metrica("Sharpe", "—" if p["sharpe"] is None else f"{p['sharpe']:.2f}")
    with c4:
        card_metrica("Máx. drawdown", _p(p["max_drawdown"]))
    c5, c6, c7, c8 = st.columns(4)
    with c5:
        card_metrica("Sortino", "—" if p["sortino"] is None else f"{p['sortino']:.2f}")
    with c6:
        card_metrica("Calmar", "—" if p["calmar"] is None else f"{p['calmar']:.2f}")
    with c7:
        card_metrica("Volatilidade", _p(p["volatility"]))
    with c8:
        card_metrica("Turnover médio", _p(res.get("avg_turnover")))

    if res.get("equity_curve"):
        secao_titulo("Curva de capital", "📉")
        curve = pd.DataFrame({"Curva": res["equity_curve"]},
                             index=res.get("dates"))
        st.line_chart(curve)


# ── Qualidade dos Dados ───────────────────────────────────────────────────────
def _tab_qualidade() -> None:
    if not us.schema_ready():
        estado_vazio("Schema market_us ainda não aplicado.", "🔌")
        return
    secao_titulo("Auditoria de qualidade", "🩺")
    df = us.quality_audit(limit=200)
    if df is None or df.empty:
        st.info("Nenhum registro de auditoria ainda. Rode `python run_us_ingest.py "
                "validate --warehouse` após a carga.")
        return
    st.dataframe(df, hide_index=True, use_container_width=True)


# ── Sincronização ─────────────────────────────────────────────────────────────
def _tab_sincronizacao(status: dict) -> None:
    secao_titulo("Sincronização de Dados Americanos", "🔄")
    st.markdown(
        "A ingestão roda **fora da interface**, por linha de comando, gravando no "
        "**warehouse local** (Postgres em `127.0.0.1:5433`). Fonte padrão: "
        "**SEC EDGAR** (fundamentos, gratuita — exige `SEC_USER_AGENT` no `.env` "
        "com nome e e-mail) + **yfinance** (preços). Credenciais/identificação são "
        "usadas **apenas** pela CLI — nunca pela interface.")

    runs = us.ingestion_runs()
    if runs is not None and not runs.empty:
        st.markdown("**Execuções recentes**")
        st.dataframe(runs, hide_index=True, use_container_width=True)
    else:
        st.caption("Nenhuma execução de ingestão registrada ainda.")

    st.markdown("**Comandos** (rodar no terminal, na raiz do projeto):")
    st.code(
        "# 1) aplicar o schema local (idempotente)\n"
        "python run_us_ingest.py init-schema --warehouse\n\n"
        "# 2) testar chave + conexão\n"
        "python run_us_ingest.py test --warehouse --json\n\n"
        "# 3) estimar a carga ANTES de baixar (dry-run, sem rede)\n"
        "python run_us_ingest.py estimate --tickers AAPL MSFT NVDA\n\n"
        "# 4) seedar o universo (NYSE/Nasdaq/AMEX)\n"
        "python run_us_ingest.py universe --warehouse --limit 200\n\n"
        "# 5) carga histórica de um lote pequeno\n"
        "python run_us_ingest.py bootstrap --warehouse --tickers AAPL MSFT --years 20 --json\n\n"
        "# 6) retomar após falha / atualizar\n"
        "python run_us_ingest.py resume --warehouse\n"
        "python run_us_ingest.py daily  --warehouse --tickers AAPL MSFT\n\n"
        "# 7) auditar qualidade\n"
        "python run_us_ingest.py validate --warehouse --json",
        language="bash")
    st.markdown("**Ver os dados na interface** (os dados dos EUA ficam SÓ no "
                "warehouse local — não são publicados no Supabase):")
    st.code(
        "# rode o app na sua máquina apontando para o warehouse local\n"
        '$env:SUPABASE_UNIFICADO_URL = "postgresql://postgres:<senha>@127.0.0.1:5433/postgres"\n'
        "streamlit run app.py",
        language="powershell")
    if not status.get("schema_ready"):
        if status.get("db_is_local"):
            st.warning("Schema `market_us` ainda não existe no warehouse. Rode o passo 1.")
        else:
            st.info("Este deploy lê o Supabase, que **não** guarda os dados dos EUA "
                    "(regra do projeto: histórico pesado é warehouse-only). A seção "
                    "funciona rodando o app localmente, como acima.", icon="☁️")


# ── Metodologia ───────────────────────────────────────────────────────────────
def _tab_metodologia() -> None:
    secao_titulo("Metodologia — Empresas Americanas", "📚")
    st.markdown(f"""
**Fonte e armazenamento.** Fundamentos: **SEC EDGAR** (filings 10-K em XBRL —
dados públicos e de domínio público; a *filing date* vira `available_at`, o
padrão-ouro para point-in-time). Preços: **yfinance**. Ambas acessadas só na
ingestão. Todo histórico vive no **warehouse local** (`market_us.*`), isolado do
B3/FII. A interface é **offline-first**: lê o banco local e funciona sem rede
após a carga.

**Identidade.** A empresa é identificada por **CIK** (não pelo ticker, que é
reutilizado/renomeado). O histórico de símbolos fica em `market_us.ticker_aliases`;
o histórico de uma empresa **não é apagado** ao trocar de ticker.

**Point-in-time.** Cada fato financeiro guarda `reference_date` (fim do período),
`published_date` (filing) e `available_at` (quando era conhecível). Backtests
filtram por `available_at` — nunca por data de ingestão — para evitar *look-ahead*.
Empresas **deslistadas** permanecem no universo histórico (anti-*survivorship*).

**Normalização.** Ausência nunca vira zero; unidades e períodos (anual/trimestral/
TTM) são rotulados explicitamente; divergência entre ticker solicitado e retornado
é rejeitada, não gravada sob o símbolo errado.

**Score.** Fundamentalista v{US_FUNDAMENTAL_SCORE_VERSION}, relativo por
setor/indústria, versionado point-in-time em `market_us.score_vintages`. **Não** é
garantia de retorno.

**Escopo desta seção.** Aqui mora a análise fundamentalista e a carteira-modelo:
empresas avaliadas pelo que já entregam (qualidade, crescimento, solidez,
eficiência de capital, valuation, retorno ao acionista). A trilha de **retorno
assimétrico** (score v{US_ASYMMETRY_SCORE_VERSION}) vive numa **seção própria no
menu — "Empresas Fora da Curva"** — porque tem propósito, tolerância a erro e
tamanho de posição diferentes. Não misture as duas leituras.

> ℹ️ A fonte padrão é a SEC EDGAR porque os dados são de **domínio público** —
> sem licença restritiva sobre o armazenamento local. Os Termos da FMP (fonte
> opcional, desativada por padrão) proíbem cópia/armazenamento sem autorização
> escrita e exigem apagar os dados ao encerrar a assinatura; só a use com
> licença compatível.
""")
