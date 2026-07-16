"""
views/empresas_americanas.py
Seção Empresas Americanas (NYSE/Nasdaq/AMEX) — inspirada em Empresas B3 / FIIs.

OFFLINE-FIRST: lê SÓ o warehouse local (core.us_data). Nunca chama a FMP. Sem
dados, mostra estado vazio e instruções de sincronização — a UI não quebra.

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
        "🇺🇸",
    )

    status = us.data_status()
    _status_badges(status)

    if status.get("reason"):
        st.caption(f"ℹ️ {status['reason']}")
    st.markdown("<br>", unsafe_allow_html=True)

    abas = st.tabs([
        "Visão Geral", "Explorar", "Análise Fundamentalista", "Análise Avançada",
        "Comparação por Indústria", "Criação de Portfólio", "Backtests",
        "Dossiê", "Fora da Curva", "Qualidade dos Dados", "Sincronização",
        "Metodologia",
    ])

    with abas[0]:
        _tab_visao_geral()
    with abas[1]:
        _tab_explorar()
    with abas[2]:
        em_construcao("Fase 5 — Análise",
                      "Score fundamentalista por setor (Qualidade, Crescimento, "
                      "Solidez, Eficiência de Capital, Valuation, Retorno ao acionista).")
    with abas[3]:
        em_construcao("Fase 5 — Análise Avançada",
                      "Piotroski F-Score, Altman Z-Score, accruals de Sloan, "
                      "retorno incremental sobre capital.")
    with abas[4]:
        em_construcao("Fase 5 — Comparação por Indústria",
                      "Ranking e percentis dentro da mesma indústria (evita comparar "
                      "setores estruturalmente incompatíveis).")
    with abas[5]:
        em_construcao("Fase 6 — Portfólio",
                      "Carteira-modelo americana: restrições por setor/posição, pesos "
                      "por score, benchmarks (S&P 500, Nasdaq-100, Russell 2000).")
    with abas[6]:
        em_construcao("Fase 6 — Backtests",
                      "Walk-forward point-in-time, Rank-IC, t-stat, hit rate, "
                      "excesso sobre equal-weight, Sharpe/Sortino/Calmar.")
    with abas[7]:
        _tab_dossie(status)
    with abas[8]:
        em_construcao("Fase 7 — Empresas Fora da Curva",
                      "Score de assimetria: crescimento persistente, reinvestimento "
                      "produtivo, baixa diluição, sinais negativos e condições de "
                      "invalidação. NÃO é recomendação automática.")
    with abas[9]:
        _tab_qualidade()
    with abas[10]:
        _tab_sincronizacao(status)
    with abas[11]:
        _tab_metodologia()


# ── Visão Geral ───────────────────────────────────────────────────────────────
def _tab_visao_geral() -> None:
    ov = us.overview()
    if ov.get("companies", 0) == 0:
        estado_vazio(
            "Nenhuma empresa americana no warehouse local ainda. Rode a carga "
            "inicial (aba Sincronização) para popular o banco local.", "🇺🇸")
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


# ── Dossiê (esqueleto offline; parecer LLM vem na Fase 5) ─────────────────────
def _tab_dossie(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para montar o dossiê.", "📄")
        return
    symbol = st.text_input("Ticker (ex.: AAPL)", key="us_dossie_symbol").strip().upper()
    if not symbol:
        st.info("Digite um ticker para ver a série financeira anual (offline).")
        return
    df = us.company_financials(symbol)
    if df is None or df.empty:
        estado_vazio(f"Sem histórico local para {symbol}.", "📄")
        return
    secao_titulo(f"{symbol} — histórico anual", "📈")
    st.dataframe(df, hide_index=True, use_container_width=True)
    em_construcao("Fase 5 — Dossiê completo",
                  "Modelo de negócios, margens, ROIC, dívida, valuation, pares, "
                  "riscos, tese e parecer narrado por LLM (sem inventar números).")


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
        "**warehouse local** (Postgres em `127.0.0.1:5433`). A chave `FMP_API_KEY` "
        "é usada **apenas** pela CLI — nunca pela interface, nunca é exibida aqui.")

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
    if not status.get("schema_ready"):
        st.warning("Schema `market_us` ainda não existe no banco atual. Rode o passo 1.")


# ── Metodologia ───────────────────────────────────────────────────────────────
def _tab_metodologia() -> None:
    secao_titulo("Metodologia — Empresas Americanas", "📚")
    st.markdown(f"""
**Fonte e armazenamento.** Fonte primária: **Financial Modeling Prep (FMP)**,
acessada só na ingestão. Todo histórico pesado vive no **warehouse local**
(`market_us.*`), isolado do B3/FII. A interface é **offline-first**: lê o banco
local e funciona sem a chave após a carga.

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

**Scores (próximas fases).** Fundamentalista v{US_FUNDAMENTAL_SCORE_VERSION} por
setor/indústria; assimetria (Fora da Curva) v{US_ASYMMETRY_SCORE_VERSION}. Scores
são versionados point-in-time em `market_us.score_vintages` e **não** são garantia
de retorno.

> ⚠️ Verifique os termos de licença da FMP quanto ao armazenamento dos dados. O
> projeto implementa o armazenamento técnico local; a conformidade com a licença
> é responsabilidade do usuário.
""")
