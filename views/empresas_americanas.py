"""
views/empresas_americanas.py
Seção Empresas Americanas (NYSE/Nasdaq/AMEX) — inspirada em Empresas B3 / FIIs.

OFFLINE-FIRST: lê SÓ o warehouse local (core.us_data). Nunca chama API externa
(SEC EDGAR/yfinance são só de ingestão). Sem dados, mostra estado vazio e
instruções de sincronização — a UI não quebra.

Módulo alinhado ao contrato de navegação de Empresas B3: empresas por setor,
análise individual, criação/simulação de portfólio e avaliação de carteira. A
inteligência usa SEC/GAAP e contexto macroeconômico americano.
"""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

import core.us_data as us
from core.busca_empresa import buscar_empresas
from core.crescimento import sem_ano_em_curso, ultimo_preco_nao_positivo
from core.inteligencia_ativos import veredito
from core.llm_context_ativo import build_us_ativo_context
from core.macro_data.database import descrever_fonte_macro, get_macro_source
from core.macro_data.portfolio_context import load_portfolio_macro_snapshot
from core.market_companies import (
    filter_market_companies,
    localize_us_company_frame,
    normalize_us_companies,
    translate_us_industry,
    translate_us_sector,
    us_logo_url,
)
from core.severidade_flags import (
    SEVERIDADE_COBERTURA,
    SEVERIDADE_CONTEXTO,
    SEVERIDADE_RISCO,
    TITULO_SEVERIDADE,
    agrupa_flags_por_severidade,
)
from core.us_company_analysis import (
    annual_dividends,
    annual_price_returns,
    derive_metric_history,
    last_value,
    numeric_financials,
    regression_growth,
)
from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION
from core.validacao_motor import validacao_us
from data_pipeline.utils.date_utils import fmt_datetime_br
from design import portao_inteligencia as _portao_ui
from design.chat_ativo import render_chat_ativo
from design.componentes import (
    aviso_cobertura_do_universo,
    aviso_escala_do_score,
    badge_status,
    card_metrica,
    container_pagina,
    cor_token,
    estado_vazio,
    frescor_da_vitrine,
    secao_titulo,
    selo_de_frescor,
)
from design.lacunas import aviso_lacuna, detalhe_tecnico, falha_de_acao
from design.market_companies import (
    render_company_logo,
    render_company_search,
    render_market_css,
    render_market_tabs,
    render_sector_grid,
)
from design.relatorio_analista import secao_relatorio

_PLOT_LAYOUT = dict(
    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#CBD5E0"),
    margin=dict(l=18, r=18, t=42, b=18),
)

# Valores exatos da paleta do app, e não variantes próximas: é o que faz
# `cor_token` reconhecê-los e trocá-los pelo token do tema. Antes desta tela
# usava #FF5C7C, #FFC914 e #718096 — a um passo dos canônicos, perto demais
# para alguém notar a diferença e longe demais para o mapa casar, o que
# deixava o vermelho e o âmbar do tema escuro vazarem para a página clara.
_COR_POS = "#00C896"
_COR_NEG = "#FC5C7D"
_COR_INFO = "#4A9EFF"
_COR_ALT = "#F6C90E"
_COR_NEU = "#9CA3AF"

# "Inverso da volatilidade" saiu das opções: nenhum código calcula a coluna
# `volatility` dos candidatos, e `_base_target`/`build_portfolio` caíam em
# silêncio para o peso por pontuação -- a tela prometia um método e entregava
# outro. Volta quando houver volatilidade medida na vitrine.
_WEIGHTING_LABELS = {
    "score": "Pontuação fundamentalista",
    "equal": "Pesos iguais",
}

# Toda evidência histórica desta tela (Rank-IC, curva do backtest, auditoria por
# indústria) sai de `market_us.score_vintages`, cujo universo é montado a partir
# de `market_us.assets` com `analysis_status='eligible'`. Em 27/08/2026
# `delisted_date` era NULL nos 7.654 registros de `assets`: nenhuma empresa que
# deixou de ser negociada entrava no painel, e o texto de Metodologia afirmava o
# contrário.
#
# Em 31/08/2026 isso deixou de ser verdade -- 1.603 empresas mortas foram
# ingeridas e o painel passou a registrar 702 saídas em 16 safras. O aviso NÃO
# some por isso: o viés mudou de lugar. O universo do ranking está corrigido; a
# medição de retorno não, porque não há cotação de ticker morto em fonte
# acessível, e sem retorno futuro a empresa que morreu entra no ranking e sai da
# apuração de excesso. A probabilidade de perda permanente de capital continua
# subobservada.
#
# Por isso o aviso é derivado da medição em `core.us_survivorship`, não escrito
# aqui: frase fixa sobre estado de dado envelhece continuando a soar como rigor.


# Avisos do motor de criação que são retorno de solver, não decisão sobre a
# carteira: vão ao log do administrador, não à tela de uso.
_PREFIXOS_AVISO_SOLVER = ("Restrições incompatíveis", "Otimização indisponível",
                          "Projeção por score não convergiu")


def _motivo_sem_painel(painel) -> str:
    """Por que não há painel PIT -- a causa medida, não a causa presumida.

    A mensagem antiga afirmava sempre "a vitrine publicada não contém
    score_vintages". No warehouse local, com 55 mil safras gravadas, isso era
    falso: faltava a versão CORRENTE da metodologia, e a tela mandava rodar um
    comando que o usuário já tinha rodado. `core.us_read` carimba a causa real
    em `attrs["motivo"]`.

    O modo de operação (vitrine ou warehouse) deixou de entrar aqui: ele
    respondia a pergunta antes de a consulta ser feita, e a resposta ficou
    errada no dia em que a vitrine passou a carregar a safra.
    """
    motivo = None
    try:
        motivo = (painel.attrs or {}).get("motivo")
    except AttributeError:
        motivo = None
    return str(motivo) + "." if motivo else "causa não registrada pela camada de leitura."


def _aviso_sobrevivencia() -> str:
    """O aviso com o tamanho do viés medido, quando há medição.

    Avisar que o viés existe não diz ao usuário se ele é pequeno ou se invalida
    a evidência. `core.us_survivorship` mede isso por dois caminhos que se
    completam: o painel diz quantas empresas saíram dele (zero), e o índice de
    arquivamentos da SEC diz quantas saíram do mercado real no mesmo período
    (70%). O primeiro mostra que a amostra é sobrevivente; só o segundo dá o
    tamanho do que ficou de fora, que é o que permite descontar.

    O terceiro trecho responde o que os dois primeiros deixam em aberto: o
    ranking exibido teria evitado quem sumiu? Isso é medido fora do painel, na
    coorte de 2012 da SEC, porque dentro dele ninguém some.

    Sem medição disponível o texto volta ao aviso qualitativo -- número que
    ninguém apurou não se afirma.
    """
    from core.us_survivorship import (
        frase_mortalidade,
        frase_score_vs_morte,
        frase_turnover,
        frase_universo,
    )

    partes = [frase_universo(), frase_turnover(), frase_mortalidade(),
              frase_score_vs_morte(), _frase_regra_pit()]
    return " ".join(p for p in partes if p)


@st.cache_data(ttl=900, show_spinner=False)
def _frase_regra_pit() -> str:
    """Qual regra de visibilidade produziu a safra -- medida, nao declarada.

    A regra antiga carimbava a linha com o arquivamento MAIS TARDIO entre seus
    campos, o que faz a visibilidade depender do futuro da empresa: um campo que
    so estreou anos depois apagava o exercicio inteiro de toda safra anterior, e
    so quem sobreviveu teve anos seguintes em que estrear tag nova. A regra por
    campo corrige isso, mas so vale para a linha que tem procedencia gravada --
    e a que nao tem cai no fallback, em silencio. Por isso a frase sai de uma
    contagem no banco: afirmar rigor que ninguem apurou e o defeito que a propria
    tela ja documenta no viés de sobrevivência.
    """
    try:
        from core.database import get_engine
        from core.us_pit import cobertura_procedencia
        c = cobertura_procedencia(get_engine())
    except Exception:  # noqa: BLE001
        return ""
    if c["regra"] == "indisponivel" or not c["linhas"]:
        return ""
    if c["regra"] == "campo":
        return ("A visibilidade point-in-time e por campo: cada dado entra na "
                "safra a partir do proprio arquivamento na SEC.")
    if c["regra"] == "mista":
        return (f"A visibilidade point-in-time e por campo em "
                f"{c['fracao']:.0%} das linhas anuais; o restante ainda usa a "
                "regra por linha, que espera o campo mais tardio e por isso "
                "esconde exercicios de quem tem historico mais longo.")
    return ("A visibilidade point-in-time ainda e por linha: o exercicio so "
            "entra na safra quando o ultimo de seus campos foi arquivado, o "
            "que subestima o dado disponivel de quem seguiu arquivando.")


def _render_company_analysis_css() -> None:
    st.markdown("""
    <style>
    .us-ind-card{background:var(--app-surface);border:1px solid var(--app-border);
      border-radius:12px;padding:14px 16px;min-height:96px;margin-bottom:6px}
    .us-ind-label{font-size:.64rem;font-weight:800;letter-spacing:.10em;
      text-transform:uppercase;color:var(--app-subtle)}
    .us-ind-value{font-size:1.45rem;font-weight:800;margin:5px 0 2px}
    .us-ind-sub{font-size:.66rem;color:var(--app-subtle)}
    </style>
    """, unsafe_allow_html=True)


def render() -> None:
    render_market_css()
    # A idade da vitrine entra no cabeçalho junto com o mercado: o ranking desta
    # tela sai de um snapshot publicado do armazém local, e até 31/08/2026 nada
    # aqui dizia de quando ele era. Score sobre preço de três semanas atrás tem a
    # mesma aparência de score sobre preço de ontem.
    frescor = frescor_da_vitrine("us")
    from core.frescor import resumo_curto

    container_pagina(
        "Empresas Americanas",
        "Análise fundamentalista e portfólios com dados corporativos SEC/GAAP.",
        "🌎",
        metadados=[("Mercado", "NYSE · Nasdaq · AMEX"),
                   ("Vitrine", resumo_curto(frescor) if frescor else "")],
    )
    selo_de_frescor("us", frescor)

    status = us.data_status()
    active = render_market_tabs(state_key="us_active_tab", key_prefix="us")

    if active == 0:
        _tab_empresas_setor(status)
    elif active == 1:
        _tab_empresa(status)
    elif active == 2:
        _tab_criacao_portfolio(status)
    else:
        _tab_avaliacao_portfolio(status)


def _empty_if_offline(status: dict, message: str, icon: str) -> bool:
    if status.get("offline"):
        estado_vazio(message, icon)
        return True
    return False


def _score_label(value) -> tuple[str, str]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "Sem classificação", "neutro"
    if v >= 75:
        return "Excelente", "sucesso"
    if v >= 65:
        return "Forte", "info"
    if v >= 50:
        return "Neutra", "neutro"
    if v >= 35:
        return "Fraca", "alerta"
    return "Crítica", "erro"


def _fmt_pct(value, decimals: int = 1) -> str:
    try:
        v = float(value)
        return "—" if not np.isfinite(v) else f"{v * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_ratio(value, decimals: int = 1) -> str:
    try:
        v = float(value)
        return "—" if not np.isfinite(v) else f"{v:.{decimals}f}×"
    except (TypeError, ValueError):
        return "—"


def _fmt_usd(value) -> str:
    try:
        v = float(value)
        if not np.isfinite(v):
            return "—"
    except (TypeError, ValueError):
        return "—"
    sign = "-" if v < 0 else ""
    n = abs(v)
    if n >= 1e12:
        return f"{sign}US$ {n / 1e12:.2f} tri"
    if n >= 1e9:
        return f"{sign}US$ {n / 1e9:.2f} bi"
    if n >= 1e6:
        return f"{sign}US$ {n / 1e6:.2f} mi"
    return f"{sign}US$ {n:,.2f}"


def _fmt_growth(value) -> str:
    try:
        v = float(value)
        return "—" if not np.isfinite(v) else f"{v:+.2%}"
    except (TypeError, ValueError):
        return "—"


def _value_color(value, *, invert: bool = False) -> str:
    try:
        v = float(value)
        if not np.isfinite(v):
            return _COR_NEU
        positive = v > 0
        if invert:
            positive = not positive
        return _COR_POS if positive else _COR_NEG
    except (TypeError, ValueError):
        return _COR_NEU


def _analysis_card(label: str, value: str, subtitle: str,
                   color: str = _COR_POS) -> str:
    return (
        '<div class="us-ind-card">'
        f'<div class="us-ind-label">{html.escape(label)}</div>'
        f'<div class="us-ind-value" style="color:{cor_token(color)}">'
        f'{html.escape(value)}</div>'
        f'<div class="us-ind-sub">{html.escape(subtitle)}</div></div>'
    )


def _analysis_header(title: str) -> None:
    st.markdown(
        f'<div style="font-size:.75rem;font-weight:700;color:var(--app-text);'
        f'margin:18px 0 8px">{title}</div>', unsafe_allow_html=True)


def _analysis_cards(items: list[tuple[str, str, str, str]], columns: int = 4) -> None:
    for start in range(0, len(items), columns):
        row = items[start:start + columns]
        cols = st.columns(columns, gap="small")
        for idx, (label, value, subtitle, color) in enumerate(row):
            with cols[idx]:
                st.markdown(_analysis_card(label, value, subtitle, color),
                            unsafe_allow_html=True)


def _company_plot_layout(height: int = 320) -> dict:
    return dict(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#CBD5E0"), height=height,
        margin=dict(l=8, r=18, t=10, b=22),
        legend=dict(orientation="h", y=-.18, bgcolor="rgba(0,0,0,0)"),
        yaxis=dict(showgrid=True, gridcolor="#1E2533"),
        xaxis=dict(showgrid=False),
    )


def _tab_empresas_setor(status: dict) -> None:
    if _empty_if_offline(status, "Sem dados locais para listar as empresas.", "🏢"):
        aviso_lacuna("Sem dados locais: a listagem de empresas depende da "
                     "sincronização do armazém americano.",
                     codigo="tela.eua.sem_dados_locais")
        from core.user_context import is_admin
        if is_admin():
            _tab_sincronizacao(status)
        return
    companies = normalize_us_companies(us.companies(limit=5000))
    if companies is None or companies.empty:
        estado_vazio("Nenhuma ação americana válida encontrada na vitrine.", "🌎")
        return
    query = render_company_search(
        label="🔍 Buscar por ticker, nome ou setor (ex.: AAPL, Microsoft)",
        placeholder="Digite e pressione Enter", key="us_company_search",
    )
    if query:
        # Ticker exato ou nome que aponta uma única empresa abre a análise
        # direto. O filtro de cards também casa setor/indústria; só pula para a
        # análise quando ele concorda que sobrou uma empresa só — "software" não
        # pode sumir com o setor inteiro por casar o nome de uma empresa.
        candidatos = buscar_empresas(query, companies, col_ticker="ticker",
                                     col_nome="company_name")
        filtradas = filter_market_companies(companies, query)
        exato = query.strip().upper() in set(companies["ticker"])
        if exato or (len(candidatos) == 1 and len(filtradas) == 1
                     and filtradas["ticker"].iloc[0] == candidatos[0][0]):
            st.session_state["us_selected_ticker"] = candidatos[0][0]
            st.session_state["us_active_tab"] = 1
            st.rerun()
        if filtradas.empty and candidatos:
            # Nada casou literalmente, mas a busca aproximada achou (erro de
            # digitação no nome): mostra esses cards em vez de grade vazia.
            alvo = {tk for tk, _ in candidatos}
            filtradas = companies[companies["ticker"].isin(alvo)].reset_index(drop=True)
            st.caption("Nenhuma correspondência exata — mostrando nomes parecidos.")
        companies = filtradas
    render_sector_grid(
        companies, key_prefix="us", selected_ticker=st.session_state.get("us_selected_ticker"),
        selected_state_key="us_selected_ticker", active_state_key="us_active_tab",
    )


def _render_score_dashboard(row: pd.Series) -> None:
    label, tipo = _score_label(row.get("score"))
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Pontuação fundamentalista", f"{row.get('score', 0):.1f}/100")
        badge_status(label, tipo)
    with c2:
        card_metrica("Cobertura", f"{row.get('coverage', 0):.0f}%")
    with c3:
        card_metrica("ROIC", _fmt_pct(row.get("roic")))
    with c4:
        card_metrica("Retorno do fluxo de caixa livre", _fmt_pct(row.get("fcf_yield")))
    aviso_escala_do_score()
    # SBC e diluição entraram no score v0.5.0 (auditoria 2026-07): a margem de
    # caixa GAAP soma a remuneração em ações de volta, e recompra só cria valor
    # se a base acionária realmente encolher.
    s1, s2, s3 = st.columns(3)
    with s1:
        card_metrica("Remuneração em ações / receita", _fmt_pct(row.get("sbc_to_revenue")))
    with s2:
        card_metrica("Margem de FCL ex-SBC", _fmt_pct(row.get("fcf_ex_sbc_margin")))
    with s3:
        variacao = row.get("share_count_cagr_3y")
        card_metrica("Variação da base acionária (3a)", _fmt_pct(variacao))
        if variacao is not None and float(variacao) > 0.02:
            st.caption("⚠️ Diluição: a emissão de ações supera as recompras.")
    tracks = [(label_, float(row.get(col, 50))) for col, label_ in _TRACK_LABELS.items()]
    fig = go.Figure(go.Scatterpolar(
        r=[x[1] for x in tracks] + [tracks[0][1]],
        theta=[x[0] for x in tracks] + [tracks[0][0]], fill="toself",
        line=dict(color="#4A9EFF"), fillcolor="rgba(74,158,255,.22)",
    ))
    fig.update_layout(**_PLOT_LAYOUT, height=360,
                      polar=dict(radialaxis=dict(range=[0, 100], gridcolor="#2D3748"),
                                 bgcolor="rgba(0,0,0,0)"), showlegend=False)
    st.plotly_chart(fig, width="stretch", key=f"us_radar_{row.get('symbol')}")


def _tab_empresa(status: dict) -> None:
    _render_company_analysis_css()
    if _empty_if_offline(status, "Sem dados locais para analisar empresas.", "🔍"):
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        estado_vazio("Sem empresas com demonstrações suficientes para análise.", "🔍")
        return
    scored = scored.copy()
    scored["symbol"] = scored["symbol"].astype(str).str.upper()
    valid_symbols = set(scored["symbol"])
    selected = str(st.session_state.get("us_selected_ticker", "") or "").upper()
    if "us_company_ticker_input" not in st.session_state:
        st.session_state["us_company_ticker_input"] = selected

    input_col, button_col = st.columns([4, 1])
    with input_col:
        ticker_raw = st.text_input(
            "Ticker ou nome da empresa", key="us_company_ticker_input",
            placeholder="Ex.: AAPL, Microsoft, NVDA",
        )
    with button_col:
        st.markdown("<br>", unsafe_allow_html=True)
        analyze = st.button("Analisar", type="primary", width="stretch",
                            key="us_btn_analisar_empresa")
    if analyze and ticker_raw.strip():
        # Mesma regra da aba B3 (core.busca_empresa): um candidato abre direto,
        # vários viram botões de escolha, nenhum vira aviso.
        candidatos = buscar_empresas(ticker_raw, scored, valid_symbols,
                                     col_ticker="symbol", col_nome="name")
        if len(candidatos) == 1:
            st.session_state["us_selected_ticker"] = candidatos[0][0]
            st.session_state.pop("us_busca_candidatos", None)
            st.rerun()
        elif candidatos:
            st.session_state["us_busca_candidatos"] = candidatos
            st.session_state["us_selected_ticker"] = ""
        else:
            st.session_state.pop("us_busca_candidatos", None)
            st.warning("Nenhuma empresa com esse ticker ou nome no universo "
                       "americano publicado.", icon="⚠️")

    # Seleção vinda de outro caminho (card do setor) descarta a escolha pendente.
    candidatos = st.session_state.get("us_busca_candidatos") or []
    if candidatos and st.session_state.get("us_selected_ticker"):
        st.session_state.pop("us_busca_candidatos", None)
        candidatos = []
    if candidatos:
        st.caption(f"{len(candidatos)} empresas encontradas — escolha uma:")
        cols = st.columns(min(len(candidatos), 4))
        for i, (c_tk, c_nome) in enumerate(candidatos):
            rotulo = c_tk if c_nome == c_tk else f"{c_tk} — {c_nome}"
            if cols[i % len(cols)].button(rotulo, key=f"us_cand_{c_tk}",
                                          width="stretch"):
                st.session_state["us_selected_ticker"] = c_tk
                st.session_state.pop("us_busca_candidatos", None)
                st.rerun()

    symbol = str(st.session_state.get("us_selected_ticker", "") or "").upper()
    if not symbol:
        if not candidatos:
            st.info("Digite um ticker ou o nome da empresa e clique em "
                    "**Analisar**.", icon="🔍")
        return
    match = scored[scored["symbol"] == symbol]
    if match.empty:
        st.warning("Ticker não encontrado no universo americano publicado.", icon="⚠️")
        return
    row = localize_us_company_frame(match).iloc[0]

    with st.spinner(f"Carregando dados de {symbol}…"):
        financials = numeric_financials(us.company_financials(symbol))
        market_data = us.company_market_data(symbol)
    prices = market_data.get("prices", pd.DataFrame()) if isinstance(market_data, dict) else pd.DataFrame()
    dividends = market_data.get("dividends", pd.DataFrame()) if isinstance(market_data, dict) else pd.DataFrame()
    if not prices.empty:
        prices = prices.copy()
        prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
        prices["price"] = pd.to_numeric(prices["price"], errors="coerce")
        prices = prices.dropna().sort_values("date")
        # Mesmo corte da Empresas B3: antes do último preço ≤ 0 o ajuste por
        # proventos quebrou e a série está noutra escala.
        corte_preco = ultimo_preco_nao_positivo(prices["date"], prices["price"])
        if corte_preco is not None:
            prices = prices[prices["date"] > corte_preco]
            aviso_lacuna(
                f"Preço ajustado chega a ≤ 0 até {corte_preco:%d/%m/%Y}: o histórico "
                "anterior foi omitido do gráfico, do retorno anual e dos múltiplos "
                "históricos.",
                codigo="tela.eua.preco_ajustado_quebrado", entidade=symbol)
    history = derive_metric_history(financials, prices, dividends)
    dividend_history = annual_dividends(financials, dividends)
    current_price = last_value(prices.rename(columns={"price": "value"}), "value") \
        if not prices.empty else None

    logo_col, identity_col, price_col = st.columns([1, 5, 2])
    with logo_col:
        # Sem <img> cru via unsafe_allow_html — ver achado A-012.
        render_company_logo(symbol, us_logo_url(symbol), size=64)
    with identity_col:
        st.markdown(
            f'<h2 style="font-size:1.60rem;font-weight:800;color:var(--app-text);margin:0 0 4px">'
            f'{html.escape(symbol)} — {html.escape(str(row.get("name", "")))}</h2>'
            f'<div style="font-size:.78rem;color:var(--app-muted)">'
            f'{html.escape(str(row.get("sector", "—")))} · '
            f'{html.escape(str(row.get("industry", "—")))}</div>', unsafe_allow_html=True)
    with price_col:
        price_text = f"US$ {current_price:,.2f}" if current_price is not None else "—"
        st.markdown(
            '<div style="text-align:right;padding-top:8px">'
            f'<div style="font-size:1.60rem;font-weight:800;color:{cor_token(_COR_POS)}">'
            f'{price_text}</div>'
            f'<div style="font-size:.68rem;color:var(--app-subtle)">'
            'Última cotação disponível</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    _analysis_header("📉 Preço da Ação")
    if not prices.empty:
        periods = {"1A": 365, "3A": 1095, "5A": 1825, "Máx": None}
        selected_period = st.radio(
            "Período", list(periods), index=3, horizontal=True,
            key=f"us_price_period_{symbol}")
        chart_prices = prices.copy()
        days = periods[selected_period]
        if days:
            chart_prices = chart_prices[
                chart_prices["date"] >= chart_prices["date"].max() - pd.Timedelta(days=days)]
        fig = px.line(chart_prices, x="date", y="price",
                      color_discrete_sequence=[_COR_INFO])
        fig.update_traces(line_width=1.5)
        fig.update_layout(**_company_plot_layout(280), showlegend=False,
                          yaxis_title="Preço (US$)", xaxis_title="")
        st.plotly_chart(fig, width="stretch",
                        config={"displayModeBar": False}, key=f"us_price_{symbol}_{selected_period}")
    else:
        aviso_lacuna("Histórico de preços ainda não publicado para este ticker.",
                     codigo="tela.eua.precos_sem_historico", entidade=symbol)

    _analysis_header("📊 Retorno Anual do Preço")
    annual_returns = annual_price_returns(prices)
    if not annual_returns.empty:
        annual_returns = annual_returns.sort_values("Ano")
        # "2026 (até 28/10)": o ano aberto é acumulado no ano, não o do ano.
        annual_returns["Ano"] = annual_returns["Rotulo"]
        annual_returns["Positivo"] = annual_returns["Retorno"] >= 0
        annual_returns["Texto"] = annual_returns["Retorno"].map(lambda value: f"{value:+.2f}%")
        fig = px.bar(
            annual_returns, x="Retorno", y="Ano", orientation="h", color="Positivo",
            color_discrete_map={True: _COR_POS, False: _COR_NEG}, text="Texto")
        fig.update_traces(textposition="outside", textfont_size=10)
        fig.update_layout(**_company_plot_layout(max(250, len(annual_returns) * 28)),
                          showlegend=False, xaxis_title="Retorno anual (%)", yaxis_title="Ano")
        # color= divide os dados em 2 traces (positivo/negativo); o eixo categórico
        # usa categoryorder="trace" por padrão e agruparia por SINAL, quebrando a
        # cronologia. Fixa a ordem dos anos explicitamente (mais recente no topo).
        fig.update_yaxes(categoryorder="array",
                         categoryarray=annual_returns["Ano"].tolist())
        st.plotly_chart(fig, width="stretch",
                        config={"displayModeBar": False}, key=f"us_returns_{symbol}")
    else:
        aviso_lacuna("Retornos anuais dependem da publicação do histórico de preços.",
                     codigo="tela.eua.retornos_anuais_sem_precos", entidade=symbol)

    _analysis_header("📈 Crescimento Médio Anual (CAGR)")
    cagr_items = []
    # Proventos somados por ano civil: o ano em curso ainda não acabou e
    # entraria no CAGR como queda. A série GAAP é por exercício fiscal, fechado.
    dividend_cagr_source = (sem_ano_em_curso(dividend_history, "fiscal_year")
                            if dividends is not None and not dividends.empty
                            else dividend_history)
    for label, field, source in (
        ("Receita Líquida", "revenue", financials),
        ("EBIT", "ebit", financials),
        ("Lucro Líquido", "net_income", financials),
        ("Dividendos", "dividends_per_share", dividend_cagr_source),
    ):
        growth = regression_growth(source, field)
        rate = growth.taxa if growth is not None else None
        caption = growth.legenda() if growth is not None else "Sem histórico"
        cagr_items.append((label, _fmt_growth(rate), caption, _value_color(rate)))
    _analysis_cards(cagr_items)

    _analysis_header("📋 Último Exercício Disponível")
    latest_items = []
    for label, field, row_field, invert in (
        ("Receita Líquida", "revenue", "_revenue", False),
        ("EBIT", "ebit", "_ebit", False),
        ("Lucro Líquido", "net_income", "_net_income", False),
        ("Dívida Líquida", "net_debt", "_net_debt", True),
    ):
        value = last_value(financials, field)
        if value is None:
            value = row.get(row_field)
        latest_items.append((label, _fmt_usd(value), "Último registro SEC/GAAP",
                             _value_color(value, invert=invert)))
    _analysis_cards(latest_items)

    _analysis_header("📊 Demonstrações Financeiras — Histórico")
    statement_map = {
        "revenue": "Receita Líquida", "ebit": "EBIT", "ebitda": "EBITDA",
        "net_income": "Lucro Líquido", "total_equity": "Patrimônio Líquido",
        "net_debt": "Dívida Líquida", "total_debt": "Dívida Total",
        "total_assets": "Ativo Total",
    }
    statement_cols = [column for column in statement_map if column in financials
                      and financials[column].notna().any()]
    if statement_cols:
        labels = [statement_map[column] for column in statement_cols]
        defaults = [label for label in ("Receita Líquida", "Lucro Líquido") if label in labels]
        selected_labels = st.multiselect(
            "Indicadores", labels, default=defaults or labels[:2],
            key=f"us_statements_{symbol}")
        if selected_labels:
            reverse = {label: column for column, label in statement_map.items()}
            selected_cols = [reverse[label] for label in selected_labels]
            long = financials[["fiscal_year", *selected_cols]].melt(
                "fiscal_year", var_name="Indicador", value_name="Valor")
            long["Indicador"] = long["Indicador"].map(statement_map)
            fig = px.line(long, x="fiscal_year", y="Valor", color="Indicador", markers=True,
                          color_discrete_sequence=[_COR_POS, _COR_INFO, _COR_ALT, _COR_NEG])
            fig.update_layout(**_company_plot_layout(340),
                              xaxis_title="Ano fiscal", yaxis_title="US$ (valores absolutos)")
            st.plotly_chart(fig, width="stretch",
                            config={"displayModeBar": False}, key=f"us_statements_chart_{symbol}")
    else:
        aviso_lacuna("Demonstrações históricas indisponíveis para este ticker.",
                     codigo="tela.eua.demonstracoes_sem_historico", entidade=symbol)

    _analysis_header("💰 Dividendos por ação")
    if not dividend_history.empty:
        fig = px.bar(dividend_history, x="fiscal_year", y="dividends_per_share",
                     color_discrete_sequence=[_COR_ALT])
        fig.update_layout(**_company_plot_layout(240),
                          xaxis_title="Ano fiscal", yaxis_title="US$/ação (soma anual)")
        st.plotly_chart(fig, width="stretch",
                        config={"displayModeBar": False}, key=f"us_dividends_{symbol}")
    else:
        aviso_lacuna("Sem histórico de dividendos por ação publicado.",
                     codigo="tela.eua.dividendos_sem_historico", entidade=symbol)

    _analysis_header("📊 Gráfico de Múltiplos — Histórico")
    metric_labels = {
        "net_margin": "Margem Líquida", "operating_margin": "Margem Operacional",
        "roe": "ROE", "roa": "ROA", "roic": "ROIC", "fcf_margin": "Margem FCL",
        "dividend_yield": "Dividend Yield", "payout": "Payout",
    }
    metric_cols = [column for column in metric_labels if column in history
                   and history[column].notna().any()]
    if metric_cols:
        options = [metric_labels[column] for column in metric_cols]
        selected_metrics = st.multiselect(
            "Indicadores (%)", options, default=options[:2], key=f"us_metrics_history_{symbol}")
        if selected_metrics:
            reverse = {label: column for column, label in metric_labels.items()}
            selected_cols = [reverse[label] for label in selected_metrics]
            plot = history[["fiscal_year", *selected_cols]].copy()
            plot[selected_cols] = plot[selected_cols] * 100
            long = plot.melt("fiscal_year", var_name="Indicador", value_name="Valor (%)")
            long["Indicador"] = long["Indicador"].map(metric_labels)
            fig = px.bar(long, x="fiscal_year", y="Valor (%)", color="Indicador",
                         barmode="group", color_discrete_sequence=[_COR_POS, _COR_INFO,
                                                                    _COR_ALT, _COR_NEG])
            fig.update_layout(**_company_plot_layout(300), xaxis_title="Ano fiscal")
            st.plotly_chart(fig, width="stretch",
                            config={"displayModeBar": False}, key=f"us_metrics_chart_{symbol}")
    else:
        aviso_lacuna("Histórico de margens e retornos ainda indisponível.",
                     codigo="tela.eua.margens_sem_historico", entidade=symbol)

    _analysis_header("💰 Fluxo de Caixa")
    cashflow_map = {
        "operating_cash_flow": "FCO (Operacional)",
        "investing_cash_flow": "FCI (Investimento)",
        "free_cash_flow": "FCL (Livre)",
    }
    cash_items = []
    for field, label in cashflow_map.items():
        value = last_value(financials, field)
        cash_items.append((label, _fmt_usd(value), "Fonte: SEC/GAAP",
                           _value_color(value)))
    _analysis_cards(cash_items, columns=3)
    cash_cols = [field for field in cashflow_map if field in financials
                 and financials[field].notna().any()]
    if cash_cols:
        long = financials[["fiscal_year", *cash_cols]].melt(
            "fiscal_year", var_name="Fluxo", value_name="Valor")
        long["Fluxo"] = long["Fluxo"].map(cashflow_map)
        fig = px.bar(long, x="fiscal_year", y="Valor", color="Fluxo", barmode="group",
                     color_discrete_sequence=[_COR_POS, _COR_NEG, _COR_INFO])
        fig.update_layout(**_company_plot_layout(300),
                          xaxis_title="Ano fiscal", yaxis_title="Valor (US$)")
        st.plotly_chart(fig, width="stretch",
                        config={"displayModeBar": False}, key=f"us_cashflow_{symbol}")

    _analysis_header("🏛️ Estrutura de Capital e Dívida")
    capital_map = (
        ("Caixa", "cash_and_equivalents", False), ("Dívida CP", "short_term_debt", True),
        ("Dívida LP", "long_term_debt", True), ("Dívida Total", "total_debt", True),
        ("Dívida Líquida", "net_debt", True), ("Patrimônio Líquido", "total_equity", False),
    )
    capital_items = []
    for label, field, invert in capital_map:
        value = last_value(financials, field)
        capital_items.append((label, _fmt_usd(value), "Último período disponível",
                              _value_color(value, invert=invert)))
    _analysis_cards(capital_items, columns=3)
    capital_chart_cols = [field for _, field, _ in capital_map[:3]
                          if field in financials and financials[field].notna().any()]
    if capital_chart_cols:
        label_by_field = {field: label for label, field, _ in capital_map}
        long = financials[["fiscal_year", *capital_chart_cols]].melt(
            "fiscal_year", var_name="Item", value_name="Valor")
        long["Item"] = long["Item"].map(label_by_field)
        fig = px.bar(long, x="fiscal_year", y="Valor", color="Item", barmode="group",
                     color_discrete_sequence=[_COR_POS, _COR_ALT, _COR_NEG])
        fig.update_layout(**_company_plot_layout(300),
                          xaxis_title="Ano fiscal", yaxis_title="Valor (US$)")
        st.plotly_chart(fig, width="stretch",
                        config={"displayModeBar": False}, key=f"us_capital_{symbol}")

    def latest_metric(name: str, row_name: str | None = None):
        value = last_value(history, name)
        if value is None and row_name:
            value = row.get(row_name)
        return value

    _analysis_header("📐 Rentabilidade")
    profitability = []
    for label, metric, row_metric, subtitle in (
        ("Margem Líquida", "net_margin", "net_margin", "% Lucro/Receita"),
        ("Margem Operacional", "operating_margin", "operating_margin", "% EBIT/Receita"),
        ("ROE", "roe", "roe", "Retorno s/ PL"), ("ROA", "roa", "roa", "Retorno s/ Ativos"),
        ("ROIC", "roic", "roic", "Retorno s/ Capital"),
        ("Dividend Yield", "dividend_yield", None, "Dividendos/Preço"),
    ):
        value = latest_metric(metric, row_metric)
        profitability.append((label, _fmt_pct(value, 2), subtitle, _value_color(value)))
    _analysis_cards(profitability, columns=3)

    _analysis_header("💹 Valuation")
    valuation = []
    for label, metric, row_metric, subtitle in (
        ("P/VP", "p_b", None, "Preço/Valor Patrimonial"),
        ("P/L", "pe", "pe", "Preço/Lucro"),
        ("EV/EBIT", "ev_ebit", "ev_ebit", "Valor Empresa/EBIT"),
        ("P/FCL", "p_fcf", "p_fcf", "Preço/Fluxo de Caixa Livre"),
    ):
        value = latest_metric(metric, row_metric)
        valuation.append((label, _fmt_ratio(value, 2), subtitle, _value_color(value)))
    payout = latest_metric("payout")
    valuation.append(("Payout", _fmt_pct(payout, 2), "% Lucro distribuído",
                      _value_color(payout)))
    _analysis_cards(valuation)

    _analysis_header("🏗️ Estrutura de Capital")
    structure = []
    for label, metric, row_metric, subtitle, invert in (
        ("Endividamento", "debt_to_equity", "debt_to_equity", "Dívida Total/PL", True),
        ("Alavancagem Fin.", "financial_leverage", None, "Ativos/PL", True),
        ("Liquidez Corrente", "current_ratio", "current_ratio",
         "Ativo Circ./Passivo Circ.", False),
    ):
        value = latest_metric(metric, row_metric)
        structure.append((label, _fmt_ratio(value, 2), subtitle,
                          _value_color(value, invert=invert)))
    _analysis_cards(structure, columns=3)

    _analysis_header("🏆 Score e critérios de avaliação")
    _render_score_dashboard(row)
    with st.expander("📄 Dossiê, classificação e critérios avançados"):
        _render_dossie_for(symbol)

    # Sem setor, como o portão da Inteligência dos Ativos lê o ativo americano:
    # o relatório e o portão chegam ao mesmo veredito.
    _analysis_header("🧭 Relatório do analista")
    relatorio = secao_relatorio(symbol, str(row.get("name", "") or "") or None,
                                None, mercado="us")

    render_chat_ativo(
        mercado="us", ticker=symbol, nome=str(row.get("name", "") or ""),
        accent="#00C896",
        build_context=lambda pergunta: build_us_ativo_context(
            symbol, user_question=pergunta, row=row, financials=financials,
            current_price=current_price, relatorio=relatorio,
        ),
    )


def _render_dossie_for(symbol: str) -> None:
    d = us.dossie(symbol)
    if d.get("erro"):
        st.info("Esta análise não está disponível no momento.")
        aviso_lacuna(f"Dossiê indisponível para {symbol}: {d['erro']}",
                     codigo="tela.eua.dossie_indisponivel", entidade=symbol)
        return
    label, tipo = _CLASS_BADGE.get(d.get("classification"), ("—", "neutro"))
    badge_status(label, tipo)
    st.caption(d.get("classification_reason", ""))
    # `red_flags` carrega três coisas distintas; o componente tem de
    # distinguir. `st.warning` em todas punha 2.419 das 3.737 empresas
    # (65%) em amarelo — inclusive as que o próprio texto declara não
    # serem padrão. A classificação vem de core.severidade_flags (fonte
    # única) — não repetir o teste de prefixo aqui.
    _grupos = agrupa_flags_por_severidade(d.get("red_flags"))
    for flag in _grupos[SEVERIDADE_RISCO]:
        st.warning(flag)
    for flag in _grupos[SEVERIDADE_CONTEXTO]:
        st.info(flag)
    for flag in _grupos[SEVERIDADE_COBERTURA]:
        aviso_lacuna(flag, codigo="tela.eua.dossie_limitacao_cobertura",
                     entidade=symbol)
    notes = d.get("notes", {})
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Tese / pontos fortes**")
        for item in notes.get("tese", []):
            st.markdown(f"- {item}")
    with c2:
        st.markdown("**Condições de invalidação**")
        for item in notes.get("condicoes_invalidacao", []):
            st.markdown(f"- {item}")


def _resiliencia_do_frame(frame: pd.DataFrame) -> dict:
    """Vereditos de travessia de recessão a partir das colunas do cross-section."""
    from core.us_cycle_evidence import montar

    exigidas = ("crise_razao", "crise_anos_2008", "crise_anos_covid")
    if frame is None or frame.empty or not all(c in frame.columns for c in exigidas):
        return {}
    bruto = {}
    for _, linha in frame.iterrows():
        razao = pd.to_numeric(linha.get("crise_razao"), errors="coerce")
        if pd.isna(razao):
            continue
        bruto[str(linha.get("symbol", "")).upper()] = {
            "razao": float(razao),
            "margem_normal": pd.to_numeric(linha.get("crise_margem_normal"),
                                           errors="coerce"),
            "margem_crise": pd.to_numeric(linha.get("crise_margem_crise"),
                                          errors="coerce"),
            "anos_2008": pd.to_numeric(linha.get("crise_anos_2008"), errors="coerce"),
            "anos_covid": pd.to_numeric(linha.get("crise_anos_covid"), errors="coerce"),
        }
    return montar(bruto)


def _render_us_ciclo(entry: pd.DataFrame) -> None:
    """Como as empresas do recorte atravessaram a última recessão de demanda."""
    from core.us_cycle_evidence import (
        FRAGIL,
        INTERMEDIARIO,
        RESILIENTE,
        cobertura,
    )

    if entry is None or entry.empty:
        return
    medidas = _resiliencia_do_frame(entry)
    st.markdown("**Travessia de recessão — medida, não inferida do setor**")
    if not medidas:
        st.caption("Esta análise não está disponível no momento.")
        aviso_lacuna("Sem série anual suficiente neste recorte para medir margem de "
                     "crise. No app publicado, exige vitrine republicada.",
                     codigo="tela.eua.ciclo_sem_serie_anual")
        return

    cob = cobertura(medidas)
    rotulos = {RESILIENTE: "Resilientes", INTERMEDIARIO: "Intermediárias",
               FRAGIL: "Frágeis"}
    contagem = {k: 0 for k in rotulos}
    for v in medidas.values():
        if v.confiavel:
            contagem[v.classe] = contagem.get(v.classe, 0) + 1

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Com veredito", str(cob["com_veredito"]),
                     delta=f"de {cob['total']} medidas")
    with c2:
        card_metrica(rotulos[RESILIENTE], str(contagem[RESILIENTE]))
    with c3:
        card_metrica(rotulos[INTERMEDIARIO], str(contagem[INTERMEDIARIO]))
    with c4:
        card_metrica(rotulos[FRAGIL], str(contagem[FRAGIL]))

    st.caption(
        f"Só a crise de 2008-09 sustenta veredito: foi recessão de demanda. "
        f"{cob['so_covid']} empresa(s) deste recorte só alcançam 2020 — choque de "
        "oferta, curto e com estímulo fiscal — e ficam **sem veredito** em vez de "
        "receberem um número frágil. A razão é margem operacional na crise "
        "dividida pela margem em anos normais."
    )

    linhas = [{
        "Ticker": v.symbol,
        "Veredito": rotulos.get(v.classe, "—"),
        "Razão de margem": round(v.razao, 2),
        # Frações no motor, pontos percentuais na tela — sem isto a coluna
        # formatada como "%.1f%%" mostraria 0,0% para uma margem de 8%.
        "Margem normal": v.margem_normal * 100,
        "Margem na crise": v.margem_crise * 100,
        "Crises medidas": v.crises_medidas,
    } for v in medidas.values() if v.confiavel]
    if linhas:
        tabela = pd.DataFrame(linhas).sort_values("Razão de margem")
        st.dataframe(tabela.head(50), hide_index=True, width="stretch",
                     column_config={
                         "Margem normal": st.column_config.NumberColumn(format="%.1f%%"),
                         "Margem na crise": st.column_config.NumberColumn(format="%.1f%%"),
                     })
        st.caption("Ordenado da pior para a melhor travessia. Razão abaixo de 0,40 "
                   "é frágil; a partir de 0,80, resiliente.")


def _texto_premissas_liquido_us(premissas: dict) -> str:
    """Premissas do retorno líquido em reais, na tela (auditoria app4, EUA-J)."""
    def _pct(chave: str, casas: int = 0) -> str:
        return f"{float(premissas.get(chave) or 0) * 100:.{casas}f}%".replace(".", ",")
    dividendo = ("o dividendo observado de cada ação"
                 if premissas.get("dividendo") == "observado no painel" else
                 f"um dividend yield declarado de {_pct('dividend_yield_premissa', 1)} "
                 "a.a. (o painel não separa preço de provento)")
    return (
        "Líquido em reais: custo de giro de "
        f"{float(premissas.get('custo_transacao_bps') or 0):.0f} bps + slippage de "
        f"{float(premissas.get('slippage_bps') or 0):.0f} bps por unidade de "
        f"turnover; retenção de {_pct('retencao_dividendos')} na fonte sobre "
        f"{dividendo}; conversão pelo USDBRL de fim de mês; IR de "
        f"{_pct('ir_ganho_capital')} sobre o ganho realizado em reais no "
        "rebalanceamento (Lei 14.754, com compensação de prejuízo); IOF de "
        f"{_pct('iof_remessa', 1)} na remessa e {_pct('iof_ingresso', 2)} no "
        "ingresso. Sem IR na liquidação final. Os pesos iguais de comparação "
        "saem em reais, brutos: são o universo, não um investidor."
    )


def _bloco_liquido_us(res: dict) -> None:
    """Líquido em reais como padrão, bruto ao lado (auditoria app4, EUA-J)."""
    liquido = res.get("liquido") or {}
    if not liquido.get("ok"):
        aviso_lacuna(
            "Retorno líquido em reais indisponível ("
            + str(liquido.get("motivo") or "sem série de USDBRL")
            + "). Os números abaixo são brutos, em dólar, sem custo nem imposto.",
            codigo="tela.eua.backtest_liquido_indisponivel", nivel="warning")
        return

    def _p(x):
        return "—" if x is None else f"{x * 100:.2f}%"

    secao_titulo("Retorno líquido em reais (padrão)", "🇧🇷")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Retorno anual líquido (R$)",
                     _p(liquido["portfolio_brl_liquido"].get("ann_return")),
                     ajuda="Depois de custo, retenção, IR e IOF.")
    with c2:
        card_metrica("Retorno anual bruto (R$)",
                     _p(liquido["portfolio_brl_bruto"].get("ann_return")),
                     ajuda="Só a conversão cambial, sem custo nem imposto.")
    with c3:
        card_metrica("Excesso líquido sobre pesos iguais",
                     _p(liquido.get("excess_ann_vs_ew_liquido")))
    with c4:
        card_metrica("Excesso bruto sobre pesos iguais",
                     _p(liquido.get("excess_ann_vs_ew_brl_bruto")),
                     ajuda="Em reais, sem custo nem imposto. Em dólar, o "
                           "excesso bruto está na seção seguinte.")
    st.caption(
        f"Por período, em média: custo de giro {_p(liquido.get('custo_medio'))}, "
        f"retenção {_p(liquido.get('retencao_media'))}, IR "
        f"{_p(liquido.get('ir_medio'))}; IOF de ida e volta "
        f"{_p(liquido.get('iof_total'))} no total. "
        + _texto_premissas_liquido_us(liquido.get("premissas") or {}))


def _tab_comparacao_empresas(status: dict) -> None:
    if _empty_if_offline(status, "Sem dados locais para comparar.", "⚖️"):
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        return
    options = scored["symbol"].astype(str).tolist()
    selected = st.multiselect("Selecione de 2 a 6 empresas", options,
                              default=options[:min(3, len(options))], max_selections=6,
                              key="us_compare_symbols")
    if len(selected) < 2:
        st.info("Selecione ao menos duas empresas.")
        return
    peers = localize_us_company_frame(scored[scored["symbol"].isin(selected)])
    # "Cresc. receita" é a INCLINAÇÃO da regressão log-linear de 5 anos, não o
    # CAGR de ponta a ponta: duas receitas com a mesma ponta e meios opostos
    # recebiam a mesma taxa, e a tabela de pares existe justamente para separá-las.
    table_cols = [c for c in ("symbol", "name", "sector", "industry", "score",
        "gross_margin", "operating_margin", "roic", "revenue_trend_5y",
        "net_debt_ebitda", "pe", "ev_ebitda", "fcf_yield", "shareholder_yield",
        "dividend_yield") if c in peers]
    st.dataframe(peers[table_cols].rename(columns={
        "symbol": "Ticker", "name": "Nome", "sector": "Setor", "industry": "Indústria",
        "score": "Pontuação", "gross_margin": "Margem bruta", "operating_margin": "Margem op.",
        "roic": "ROIC", "revenue_trend_5y": "Cresc. receita 5a (regressão)",
        "net_debt_ebitda": "DL/EBITDA",
        "pe": "P/L", "ev_ebitda": "EV/EBITDA", "fcf_yield": "Retorno do FCL",
        "shareholder_yield": "Retorno ao acionista",
        "dividend_yield": "DY (últ. exercício)"}), hide_index=True, width="stretch")
    tracks = peers[["symbol", *_TRACK_LABELS]].melt(
        "symbol", var_name="Trilha", value_name="Pontuação")
    tracks["Trilha"] = tracks["Trilha"].map(_TRACK_LABELS)
    fig = px.bar(
        tracks, x="Trilha", y="Pontuação", color="symbol", barmode="group",
        labels={"symbol": "Ticker"},
    )
    fig.update_layout(**_PLOT_LAYOUT, height=420, yaxis_range=[0, 100])
    st.plotly_chart(fig, width="stretch", key="us_compare_tracks")
    with st.expander("Comparação por indústria"):
        _tab_comparacao_industria(status)


# ── Análise Fundamentalista (score por setor/indústria) ───────────────────────
_TRACK_LABELS = {
    "score_quality": "Qualidade", "score_growth": "Crescimento",
    "score_solidity": "Solidez", "score_capital_efficiency": "Efic. Capital",
    "score_valuation": "Avaliação", "score_shareholder": "Retorno ao acionista",
}


def _tab_analise_fundamentalista(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para calcular a pontuação.", "📊")
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        estado_vazio("Sem empresas com demonstrações suficientes para a pontuação.", "📊")
        return
    secao_titulo("Pontuação fundamentalista — relativa por indústria", "🏆")
    detalhe_tecnico("Winsorização + percentil intra-indústria nas 6 trilhas de fatores. "
                    "Ausência = neutro.", codigo="eua.pontuacao_metodologia")
    aviso_escala_do_score()
    # A-152: a nota aparece aqui, entao a evidencia que a sustenta tambem
    # precisa aparecer aqui. Ficava so num expander colapsado da aba de
    # Criacao de Portfolio, e so sobre o painel de backtest.
    detalhe_tecnico(validacao_us().texto, codigo="eua.pontuacao_validacao")
    # A-154: por quantos ativos esta nota fala.
    aviso_cobertura_do_universo("us")
    scored = localize_us_company_frame(scored)
    setores = ["(todos)"] + sorted(x for x in scored["sector"].dropna().unique())
    sel = st.selectbox("Setor", setores, key="us_score_sector")
    view = scored if sel == "(todos)" else scored[scored["sector"] == sel]
    cols = ["symbol", "name", "sector", "industry", "score",
            *_TRACK_LABELS.keys(), "coverage"]
    cols = [c for c in cols if c in view.columns]
    show = view[cols].head(200).rename(columns={
        "symbol": "Ticker", "name": "Nome", "sector": "Setor",
        "industry": "Indústria", "score": "Pontuação", "coverage": "Cobertura %",
        **_TRACK_LABELS})
    st.dataframe(show, hide_index=True, width="stretch")


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
    ind = st.selectbox(
        "Indústria", industrias, key="us_cmp_industry",
        format_func=translate_us_industry,
    )
    import core.us_score as _score
    peers = _score.industry_comparison(scored, ind)
    if peers.empty:
        estado_vazio("Sem pares nesta indústria.", "🏭")
        return
    secao_titulo(f"{translate_us_industry(ind)} — {len(peers)} empresa(s)", "🏭")
    show_cols = ["symbol", "name", "score", "score_quality", "score_growth",
                 "score_valuation", "gross_margin", "roic", "net_debt_ebitda",
                 "revenue_trend_5y", "dividend_yield"]
    show_cols = [c for c in show_cols if c in peers.columns]
    st.dataframe(peers[show_cols].rename(columns={
        "symbol": "Ticker", "name": "Nome", "score": "Pontuação",
        "score_quality": "Qualidade", "score_growth": "Crescimento",
        "score_valuation": "Avaliação", "gross_margin": "Margem bruta",
        "roic": "ROIC", "net_debt_ebitda": "DL/EBITDA",
        "revenue_trend_5y": "Cresc.Rec 5a (reg.)",
        "dividend_yield": "DY (últ. exerc.)"}),
        hide_index=True, width="stretch")


# ── Dossiê determinístico ─────────────────────────────────────────────────────
_CLASS_BADGE = {
    "consolidada": ("Consolidada", "sucesso"), "crescimento": ("Crescimento", "info"),
    "assimetrica": ("Assimétrica", "alerta"), "turnaround": ("Recuperação", "alerta"),
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
        estado_vazio("Esta análise não está disponível no momento.", "📄")
        aviso_lacuna(f"{symbol}: {d['erro']}", codigo="tela.eua.dossie_indisponivel",
                     entidade=symbol)
        return

    label, tipo = _CLASS_BADGE.get(d.get("classification"), ("—", "neutro"))
    secao_titulo(
        f"{symbol} — {d.get('name') or ''}", "📄",
        f"{translate_us_sector(d.get('sector'), d.get('industry'))} / "
        f"{translate_us_industry(d.get('industry') or d.get('sector'))}",
    )
    cb1, cb2, *_ = st.columns([1, 1, 4])
    with cb1:
        badge_status(label, tipo)
    with cb2:
        if d.get("score") is not None:
            badge_status(f"Pontuação {d['score']}", "info")
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
        # O R² acompanha a taxa: inclinação sem aderência é reta ajustada a
        # ruído, e o cartão sem ele apresentaria as duas como a mesma coisa.
        _r2 = m.get("revenue_trend_r2_5y")
        card_metrica(
            "Cresc. receita 5a (regressão)", _p(m.get("revenue_trend_5y")),
            None if _r2 is None else f"R² {_r2:.2f}")

    # Só o bloco de risco confirmado é "sinal de alerta"; os outros dois
    # saem sob o próprio título, porque a bandeira que aparece em tudo
    # deixa de informar qualquer coisa.
    _grupos = agrupa_flags_por_severidade(d.get("red_flags"))
    if _grupos[SEVERIDADE_RISCO]:
        secao_titulo("Sinais de alerta", "🚩")
        st.caption(TITULO_SEVERIDADE[SEVERIDADE_RISCO])
        for f in _grupos[SEVERIDADE_RISCO]:
            st.markdown(f"- {f}")
    if _grupos[SEVERIDADE_CONTEXTO]:
        secao_titulo("Observações de contexto", "ℹ️")
        st.caption(TITULO_SEVERIDADE[SEVERIDADE_CONTEXTO])
        for f in _grupos[SEVERIDADE_CONTEXTO]:
            st.markdown(f"- {f}")
    for f in _grupos[SEVERIDADE_COBERTURA]:
        aviso_lacuna(f, codigo="tela.eua.dossie_limitacao_cobertura", entidade=symbol)

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


def _intel_avaliar_us(tk: str):
    """Avaliação por regras da Inteligência dos Ativos (EUA), com cache de
    sessão. Falha não é guardada: o portão é fail-open e tenta de novo no rerun."""
    cache = st.session_state.setdefault("us_intel_cache", {})
    chave = str(tk).upper()
    if chave not in cache:
        cache[chave] = veredito.avaliar_ativo(chave, mercado="us")
    return cache[chave]


def _render_us_portfolio_creation_css() -> None:
    st.markdown("""
    <style>
    .us-pf-card{background:var(--app-surface);border:1px solid var(--app-border);
      border-radius:12px;padding:14px 16px;min-height:196px;margin-bottom:8px}
    .us-pf-top{display:flex;align-items:center;gap:10px;margin-bottom:9px}
    .us-pf-logo{width:44px;height:44px;border-radius:10px;object-fit:contain;
      background:var(--app-surface-raised);padding:5px}
    .us-pf-ticker{font-size:1rem;font-weight:800;color:var(--app-text)}
    .us-pf-name{font-size:.70rem;color:var(--app-muted);white-space:nowrap;
      overflow:hidden;text-overflow:ellipsis;max-width:210px}
    .us-pf-meta{font-size:.67rem;color:var(--app-muted);margin:4px 0 10px;
      min-height:32px}
    .us-pf-row{display:flex;justify-content:space-between;font-size:.70rem;
      color:var(--app-muted);margin-top:5px}
    .us-pf-row strong{color:var(--app-primary)}
    .us-pf-industry{background:color-mix(in srgb, var(--app-primary) 7%, transparent);
      border-left:3px solid var(--app-primary);
      border-radius:6px;padding:11px 14px;margin:10px 0}
    </style>
    """, unsafe_allow_html=True)


def _render_us_portfolio_cards(holdings: pd.DataFrame) -> None:
    if holdings is None or holdings.empty:
        return
    for start in range(0, len(holdings), 4):
        columns = st.columns(4, gap="small")
        for column, (_, row) in zip(columns, holdings.iloc[start:start + 4].iterrows()):
            ticker_raw = str(row.get("symbol", "—"))
            ticker = html.escape(ticker_raw)
            name = html.escape(str(row.get("name", ticker)))
            sector = html.escape(str(row.get("sector_group", "Não classificado")))
            industry = html.escape(translate_us_industry(row.get("industry_group")))
            weight = float(row.get("weight", 0.0) or 0.0)
            entry = float(row.get("entry_score", 0.0) or 0.0)
            allocation = float(row.get("allocation_usd", 0.0) or 0.0)
            macro_impact = pd.to_numeric(row.get("macro_impact"), errors="coerce")
            giro = pd.to_numeric(row.get("giro_diario_usd"), errors="coerce")
            # "não medido" é diferente de "não negocia": o card diz qual dos dois.
            giro_txt = ("—" if pd.isna(giro) else
                        f"US$ {giro / 1e6:,.1f} mi" if giro >= 1e6 else
                        f"US$ {giro / 1e3:,.0f} mil")
            with column:
                # Sem <img> cru via unsafe_allow_html — ver achado A-012.
                # Container com borda nativa substitui o card CSS custom
                # (.us-pf-card) só na linha logo+nome; o restante do
                # conteúdo mantém o estilo original (.us-pf-meta/.us-pf-row).
                with st.container(border=True):
                    logo_col, nome_col = st.columns([1, 4], gap="small")
                    with logo_col:
                        render_company_logo(ticker_raw, us_logo_url(ticker_raw), size=44)
                    with nome_col:
                        st.markdown(
                            f'<div class="us-pf-ticker">{ticker}</div>'
                            f'<div class="us-pf-name">{name}</div>',
                            unsafe_allow_html=True,
                        )
                    st.markdown(
                        f'<div class="us-pf-meta">{sector} · {industry}</div>'
                        f'<div class="us-pf-row"><span>Score de entrada</span>'
                        f'<strong>{entry:.1f}</strong></div>'
                        f'<div class="us-pf-row"><span>Peso</span>'
                        f'<strong>{weight:.1%}</strong></div>'
                        + (f'<div class="us-pf-row"><span>Impacto macro</span>'
                           f'<strong>{macro_impact:+.1f}/100</strong></div>'
                           if pd.notna(macro_impact) else
                           '<div class="us-pf-row"><span>Impacto macro</span>'
                           '<strong>sem cobertura</strong></div>')
                        +
                        f'<div class="us-pf-row"><span>Giro diário</span>'
                        f'<strong>{giro_txt}</strong></div>'
                        f'<div class="us-pf-row"><span>Capital simulado</span>'
                        f'<strong>US$ {allocation:,.0f}</strong></div>',
                        unsafe_allow_html=True,
                    )


def _semear_perfil_us_padrao() -> None:
    """Primeira carga já nasce no perfil recomendado, não nos defaults soltos.

    Sem isto, cada controle nasce com o próprio default e a combinação inicial
    não corresponde a nenhum perfil medido: o usuário vê "Personalizada" sem ter
    mexido em nada e roda a carteira com valores que ninguém verificou. Aqui o
    custo seria concreto — os defaults dão 7 setores e 20 indústrias, contra 10
    e 30 do recomendado.

    ``setdefault`` e não atribuição: semear é para a PRIMEIRA carga; sobrescrever
    a cada rerun desfaria qualquer ajuste do usuário.
    """
    from core.us_portfolio_presets import PRESETS, RECOMENDADO

    for chave, valor in PRESETS[RECOMENDADO].valores.items():
        if chave not in st.session_state:
            st.session_state[chave] = valor


def _render_perfil_us() -> None:
    """Perfis medidos + alerta quando a configuração tem custo conhecido."""
    from core.us_portfolio_presets import (
        PERSONALIZADO,
        PRESETS,
        RECOMENDADO,
        avaliar_configuracao,
        identificar_perfil,
    )

    def _aplicar(nome: str):
        def _cb():
            for chave, valor in PRESETS[nome].valores.items():
                st.session_state[chave] = valor
        return _cb

    # Estado ATUAL dos controles — não o do perfil escolhido no seletor.
    atual = {chave: st.session_state.get(chave)
             for item in PRESETS.values() for chave in item.valores}
    ativo = identificar_perfil(atual)
    alertas = avaliar_configuracao(atual)

    cor = cor_token("#00C896" if ativo != PERSONALIZADO and not alertas
                    else "#F6C90E" if alertas else "#4A9EFF")
    selo = ativo if ativo != PERSONALIZADO else "Personalizada"
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin:4px 0 10px">'
        f'<span style="font-size:.78rem;font-weight:700;color:var(--app-text)">'
        f'Perfil de configuração</span>'
        f'<span style="font-size:.66rem;font-weight:700;letter-spacing:.04em;'
        f'color:{cor};border:1px solid color-mix(in srgb, {cor} 33%, transparent);'
        f'background:color-mix(in srgb, {cor} 8%, transparent);'
        f'border-radius:999px;padding:2px 9px">{html.escape(selo)}</span></div>',
        unsafe_allow_html=True)

    col_sel, col_btn = st.columns([3, 1], vertical_alignment="bottom")
    escolhido = col_sel.selectbox(
        # Rótulo colapsado, não removido: o título visível é o markdown acima,
        # mas leitor de tela precisa do nome do controle.
        "Perfil de configuração", list(PRESETS), key="us_create_perfil",
        label_visibility="collapsed",
        help="Combinações cujo efeito foi MEDIDO no universo americano real. "
             "Aplicar sobrescreve os parâmetros; depois disso ajuste o que "
             "quiser — o app avisa se a alteração tiver custo conhecido.")
    col_btn.button("Aplicar perfil", key="us_create_aplicar_perfil",
                   on_click=_aplicar(escolhido), width="stretch")

    preset = PRESETS[escolhido]
    with st.expander("Por que estes valores (evidência medida)", expanded=False):
        for evidencia in preset.evidencias:
            st.markdown(f"- {evidencia}")
        if preset.ressalva:
            detalhe_tecnico(preset.ressalva, codigo="eua.perfil_ressalva")

    if ativo == PERSONALIZADO:
        st.caption(
            "Os controles não correspondem a nenhum perfil medido. Para voltar "
            f"ao padrão, escolha “{RECOMENDADO}” e clique em Aplicar perfil.")
    for alerta in alertas:
        st.warning(alerta, icon="⚠️")


def _render_us_portfolio_version_history(key: str) -> None:
    """Histórico e restauração da carteira-modelo EUA (achado A-006).

    Espelha ``views/fiis.py::_render_portfolio_version_history`` e
    ``views/portfolio_b3.py::_render_b3_portfolio_version_history`` — mesmo
    padrão de UI sobre o núcleo já existente em ``core.us_portfolio_model``.
    """
    try:
        from core.us_portfolio_model import (
            list_us_portfolio_model_versions,
            restore_us_portfolio_model,
        )
        versions = list_us_portfolio_model_versions()
    except (RuntimeError, ValueError, SQLAlchemyError):
        return
    archived = [item for item in versions if item.get("status") == "archived"]
    if not archived:
        return
    with st.expander("🕘 Histórico e restauração do Portfólio EUA"):
        by_id = {str(item["id"]): item for item in archived}
        selected = st.selectbox(
            "Versão arquivada",
            options=list(by_id),
            format_func=lambda model_id: (
                f"{fmt_datetime_br(by_id[model_id].get('created_at'))} · "
                f"{by_id[model_id].get('item_count', 0)} ativos"
            ),
            key=f"{key}_restore_version",
        )
        confirmed = st.checkbox(
            "Confirmo que desejo substituir a versão ativa por esta versão arquivada.",
            key=f"{key}_restore_confirm",
        )
        if st.button(
            "Restaurar versão selecionada",
            key=f"{key}_restore_action",
            disabled=not confirmed,
        ):
            try:
                restore_us_portfolio_model(selected)
                st.success("Versão restaurada e verificada transacionalmente.")
                st.rerun()
            except (RuntimeError, ValueError) as exc:
                falha_de_acao("Restauração bloqueada: não foi possível restaurar "
                              "a versão selecionada.", exc)
            except SQLAlchemyError as exc:
                falha_de_acao("Restauração bloqueada; a versão ativa anterior "
                              "foi preservada.", exc)


def _veredito_validacao_us(res: dict) -> tuple[str, str] | None:
    """Resume o teste histórico em (nível, texto) para o topo da Criação.

    O resultado vivia só no expander do fim da aba: quem montava a carteira
    via o painel inteiro antes de saber que o excesso sobre pesos iguais não
    se distingue de zero. Nível ``"warning"`` sempre que o intervalo t de 95%
    do excesso cruza zero ou o excesso médio não é positivo.
    """
    if not res.get("ok"):
        return None
    ic = res.get("rank_ic") or {}
    excesso = res.get("excess_ann_vs_ew")
    periodos = [float(x) for x in (res.get("excess_periods") or [])]
    if excesso is None or len(periodos) < 3:
        return None
    serie = np.asarray(periodos)
    media = float(serie.mean())
    erro = float(serie.std(ddof=1) / np.sqrt(len(serie)))
    from scipy import stats as _stats
    meia = float(_stats.t.ppf(.975, len(serie) - 1)) * erro
    lo, hi = media - meia, media + meia
    ic_txt = ("—" if ic.get("mean") is None else
              f"{ic['mean']:.3f} (t = {ic.get('t_stat') or 0:.2f})")
    texto = (
        f"**Validação histórica ({res.get('n_periods')} safras anuais):** a "
        f"nota ordena as empresas — correlação média com o retorno seguinte "
        f"{ic_txt} — e o top 20 rendeu **{excesso:+.2%} ao ano** sobre pesos "
        f"iguais, com intervalo de 95% por safra de **{lo:+.2%} a {hi:+.2%}**. "
    )
    if lo <= 0 or media <= 0:
        texto += ("Ordenar bem **não se converteu em retorno distinguível de "
                  "zero**: não há evidência de que a carteira abaixo supere "
                  "dividir igualmente entre as elegíveis. ")
        nivel = "warning"
    else:
        nivel = "info"
    texto += ("O teste mede o top 20 pela nota pura; a carteira desta aba "
              "(tetos, penalidades, piso de qualidade) **não foi testada**. "
              "Detalhes em *Validação histórica e benchmarks*, no fim da aba.")
    return nivel, texto


def _render_veredito_validacao_us() -> None:
    try:
        res = us.backtest(top_n=20, weighting="score")
    except Exception:  # o veredito é resumo; a falha já aparece no expander
        return
    veredito = _veredito_validacao_us(res)
    if veredito is None:
        return
    nivel, texto = veredito
    (st.warning if nivel == "warning" else st.info)(texto, icon="🧪")


def _tab_criacao_portfolio(status: dict) -> None:
    """Aplicação em escala da metodologia americana."""
    if _empty_if_offline(status, "Sem dados locais para montar a carteira.", "🚀"):
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        estado_vazio("Sem empresas com pontuação elegível.", "🚀")
        return

    from core.us_portfolio_creation import (
        USPortfolioCreationParams,
        build_portfolio_creation,
        params_to_dict,
    )

    _render_us_portfolio_creation_css()
    st.markdown(
        '<div style="background:color-mix(in srgb, var(--app-info) 8%, transparent);'
        'border-left:3px solid var(--app-info);'
        'border-radius:6px;padding:12px 16px;margin-bottom:12px;font-size:.84rem;'
        'color:var(--app-text)"><strong>🚀 Aplicação em escala.</strong> '
        'Aplica a metodologia de seleção ao universo americano inteiro, '
        'audita as indústrias, seleciona seus líderes e monta uma carteira '
        'multissetorial. A carteira segue para <strong>Avaliação de Portfólio</strong>.'
        '</div>', unsafe_allow_html=True,
    )
    detalhe_tecnico(
        "A aplicação americana é feita em duas etapas para comportar milhares de "
        "empresas: filtro institucional vetorizado e revisão apenas dos líderes por "
        "indústria.", codigo="eua.criacao_duas_etapas")
    st.caption(
        "O universo é de **ações**: REITs, fundos, SPACs, preferenciais "
        "e units ficam de fora, e financeiras usam pesos setoriais próprios."
    )

    # Modo vitrine não implica ausência de histórico. Implicava enquanto a
    # vitrine só carregava `company_snapshots`; desde que a safra PIT e o preço
    # mensal passaram a ser publicados, este curto-circuito desligava o painel
    # justamente no ambiente para o qual ele foi publicado. Quem responde se há
    # histórico é a consulta -- que já devolve o motivo quando não há.
    score_panel = us.score_panel()
    history_available = score_panel is not None and not score_panel.empty
    if history_available:
        _render_veredito_validacao_us()

    # Semear ANTES de instanciar qualquer widget: depois de instanciado, o
    # Streamlit não deixa mais escrever a chave no session_state.
    _semear_perfil_us_padrao()
    _render_perfil_us()

    # Fechado por padrão: quem confia no perfil não precisa ver 18 controles, e
    # quem quer ajustar abre. O selo acima já diz qual configuração está ativa.
    with st.expander("⚙️ Parâmetros", expanded=False):
        st.caption(
            "Já preenchidos pelo perfil acima. Alterar aqui é opcional — o app "
            "avisa quando a mudança tem custo medido.")
        p1, p2, p3, p4 = st.columns(4)
        with p1:
            benchmark = st.selectbox(
                "Benchmark de mercado",
                ["S&P 500 (SPY)", "Russell 1000 (IWB)", "Nasdaq-100 (QQQ)",
                 "Pesos iguais do universo"],
                key="us_create_benchmark",
                help="Referência para a avaliação posterior. O Treasury é tratado "
                     "separadamente como custo de oportunidade.",
            )
        with p2:
            treasury = st.number_input(
                "Treasury 3 meses a.a. (%) — premissa", min_value=0.0,
                max_value=20.0, value=4.25, step=0.25, key="us_create_treasury",
                help="Equivalente econômico americano da taxa livre de risco. "
                     "É uma premissa ajustável, não uma cotação em tempo real.",
            )
        with p3:
            market_cap_label = st.selectbox(
                "Tamanho mínimo (valor de mercado)",
                ["≥ US$ 300 mi", "≥ US$ 1 bi", "≥ US$ 2 bi", "≥ US$ 5 bi",
                 "≥ US$ 10 bi"], index=1, key="us_create_market_cap",
                help="Tamanho não é negociabilidade: ADR, spin-off recente e "
                     "ação preferencial aparecem grandes e negociam pouco. O "
                     "piso de volume é o campo ao lado.",
            )
        with p4:
            capital = st.number_input(
                "Capital para simulação (USD)", min_value=100.0, value=10_000.0,
                step=500.0, key="us_create_capital",
            )

        q1, q2, q3, q4 = st.columns(4)
        with q1:
            min_coverage = st.slider(
                "Cobertura fundamentalista mínima (%)", 30, 100, 50, 5,
                key="us_create_coverage",
            )
        with q2:
            min_years = st.number_input(
                "Histórico SEC/GAAP mínimo", 3, 20, 5, 1,
                key="us_create_years",
            )
        with q3:
            min_group = st.number_input(
                "Mín. empresas por indústria", 2, 30, 4, 1,
                key="us_create_group",
            )
        with q4:
            top_n = st.slider(
                "Número de ativos", 10, 80, 30, 5, key="us_create_topn",
            )

        r1, r2, r3, r4 = st.columns(4)
        with r1:
            max_weight = st.slider(
                "Peso máximo por ativo (%)", 2, 20, 8, 1,
                key="us_create_max_weight",
            )
        with r2:
            max_industry = st.slider(
                "Peso máximo por indústria (%)", 5, 40, 15, 1,
                key="us_create_max_industry",
            )
        with r3:
            max_sector = st.slider(
                "Peso máximo por setor (%)", 10, 50, 25, 1,
                key="us_create_max_sector",
            )
        with r4:
            weighting = st.selectbox(
                "Ponderação", list(_WEIGHTING_LABELS),
                format_func=lambda value: _WEIGHTING_LABELS[value],
                key="us_create_weighting",
            )

        s1, s2, s3, s4 = st.columns(4)
        with s1:
            min_entry = st.slider(
                "Score de entrada mínimo", 30, 80, 55, 1,
                key="us_create_entry_score",
            )
        with s2:
            min_edge = st.number_input(
                "Vantagem mín. vs universo (pontos)", min_value=0.0,
                max_value=25.0, value=2.0, step=0.5, key="us_create_edge",
            )
        with s3:
            leaders_per_industry = st.number_input(
                "Líderes por indústria", 1, 5, 2, 1,
                key="us_create_leaders",
            )
        with s4:
            monthly = st.number_input(
                "Aporte mensal (USD)", min_value=0.0, value=500.0,
                step=100.0, key="us_create_monthly",
            )

        # Os dois pisos absolutos ficam juntos e fora das linhas de calibragem:
        # não são parâmetros a otimizar, são condições mínimas para uma empresa
        # entrar. Ver core/us_liquidity.py e core/us_quality_floor.py.
        t1, t2 = st.columns([1, 2])
        with t1:
            turnover_label = st.selectbox(
                "Negociabilidade mínima (volume/dia)",
                ["Sem piso", "≥ US$ 1 milhão", "≥ US$ 5 milhões",
                 "≥ US$ 20 milhões"], index=1, key="us_create_turnover",
                help="Mediana de preço × volume dos últimos 180 pregões. Com "
                     "piso ligado, quem não tem série medida NÃO entra: comprar "
                     "papel cuja negociabilidade nunca foi medida é assumir "
                     "risco não verificado. 'Sem piso' é modo exploratório e "
                     "admite ativo com liquidez não validada.",
            )
        with t2:
            apply_floor = st.checkbox(
                "Piso absoluto de qualidade (reprova as marcadas como 'Excluída')",
                value=True, key="us_create_quality_floor",
                help="Usa o veredito de qualidade contábil calculado em código — Altman em "
                     "aflição somado a outro alerta, margem líquida e FCF "
                     "negativos, dívida/EBITDA elevada. Quem é reprovado cede a "
                     "vaga ao próximo da MESMA indústria, para que exigir "
                     "qualidade não custe diversificação.",
            )

        require_resilience = st.checkbox(
            "Exigir resiliência (ROIC/ROE/FCF yield acima do Treasury)",
            value=False, key="us_create_resilience",
            help="Usa ROIC nas empresas operacionais e ROE em financeiras. "
                 "REITs não entram no universo (só ações).",
        )
        resilience_spread = st.number_input(
            "Spread mínimo sobre o Treasury (p.p.)", min_value=-10.0,
            max_value=30.0, value=0.0, step=0.5,
            disabled=not require_resilience, key="us_create_resilience_spread",
        )
        adaptive_caps = False
        st.caption("Quando os tetos não comportarem 100%, a composição mostrará "
                   "o capital não alocado e os ativos selecionados dentro dos limites.")
        require_history = st.checkbox(
            "Exigir validação histórica ponto-no-tempo por indústria",
            value=False, disabled=not history_available,
            key="us_create_require_history",
            help="Disponível somente no ambiente com score_vintages e preços "
                 "históricos. A vitrine publicada não inventa esse histórico.",
        )
        if not history_available:
            aviso_lacuna(
                "Painel histórico PIT indisponível: "
                + _motivo_sem_painel(score_panel)
                + " A aprovação usa o sinal institucional atual SEC/GAAP.",
                codigo="tela.eua.criacao_sem_painel_pit")

    market_caps = {
        "≥ US$ 300 mi": 300_000_000.0, "≥ US$ 1 bi": 1_000_000_000.0,
        "≥ US$ 2 bi": 2_000_000_000.0, "≥ US$ 5 bi": 5_000_000_000.0,
        "≥ US$ 10 bi": 10_000_000_000.0,
    }
    turnover_floors = {"Sem piso": 0.0, "≥ US$ 1 milhão": 1_000_000.0,
                       "≥ US$ 5 milhões": 5_000_000.0,
                       "≥ US$ 20 milhões": 20_000_000.0}
    params = USPortfolioCreationParams(
        min_daily_turnover_usd=turnover_floors[turnover_label],
        apply_quality_floor=bool(apply_floor),
        benchmark=benchmark,
        risk_free_rate=float(treasury) / 100,
        capital_usd=float(capital),
        monthly_contribution_usd=float(monthly),
        top_n=int(top_n),
        leaders_per_industry=int(leaders_per_industry),
        min_market_cap=market_caps[market_cap_label],
        min_coverage=float(min_coverage),
        min_years=int(min_years),
        min_companies_per_industry=int(min_group),
        min_entry_score=float(min_entry),
        min_score_edge=float(min_edge),
        require_resilience=bool(require_resilience),
        min_resilience_spread_pp=float(resilience_spread),
        max_weight=float(max_weight) / 100,
        max_industry_weight=float(max_industry) / 100,
        max_sector_weight=float(max_sector) / 100,
        weighting=weighting,
        adaptive_caps=bool(adaptive_caps),
        require_historical_signal=bool(require_history),
    )

    macro_labels = {
        "fundamental": "Somente fundamentos",
        "moderate": "Contextual moderado (recomendado)",
        "scenario": "Cenário ampliado",
    }
    macro_mode = st.selectbox(
        "Uso da camada macro nos pesos",
        list(macro_labels),
        index=1,
        format_func=macro_labels.get,
        key="us_create_macro_mode",
        help=("O cálculo é determinístico e usa o PostgreSQL do Docker local ou, sem ele, "
              "os insumos que o Docker publicou no repositório. "
              "Ausência de cobertura mantém o peso fundamental."),
    )

    if st.button("🚀 Rodar Criação de Portfólio", type="primary", key="us_create_run"):
        with st.spinner("Aplicando filtros, auditando indústrias e otimizando pesos…"):
            # O histórico anual é carregado somente depois dos filtros de
            # elegibilidade. A vitrine normal não lê esse JSON pesado; esta é
            # a única rota que recalcula o score com a evidência de renda.
            portfolio_scored = us.portfolio_candidates_with_renda_sustentavel(
                scored, params,
            )
            baseline = build_portfolio_creation(
                portfolio_scored, params, score_panel,
                avaliador=_intel_avaliar_us,
            )
            snapshot = None
            holdings_base = baseline.get("holdings", pd.DataFrame())
            macro_fonte_criacao = get_macro_source()
            if macro_fonte_criacao is not None and not holdings_base.empty:
                try:
                    snapshot = load_portfolio_macro_snapshot(
                        macro_fonte_criacao,
                        asset_class="us",
                        assets=dict(zip(
                            holdings_base["symbol"].astype(str),
                            holdings_base["sector_group"].astype(str),
                        )),
                    )
                except (SQLAlchemyError, ValueError):
                    snapshot = None
            result = build_portfolio_creation(
                portfolio_scored, params, score_panel,
                macro_impacts=(snapshot.impacts if snapshot else {}),
                macro_mode=macro_mode,
                avaliador=_intel_avaliar_us,
            )
            result["macro_snapshot"] = snapshot
            result["macro_fonte"] = descrever_fonte_macro(macro_fonte_criacao)
            st.session_state["us_portfolio_creation_result"] = result
            st.session_state["us_portfolio_creation_params"] = {
                **params_to_dict(params), "macro_mode": macro_mode,
            }

    result = st.session_state.get("us_portfolio_creation_result")
    if not result:
        st.caption(
            "Configure os parâmetros acima e clique **🚀 Rodar Criação de Portfólio**."
        )
        return
    if st.session_state.get("us_portfolio_creation_params") != {
        **params_to_dict(params), "macro_mode": macro_mode,
    }:
        st.warning(
            "Os parâmetros foram alterados depois da última execução. Os resultados "
            "abaixo ainda correspondem à configuração anterior; rode novamente para atualizar."
        )
        # Não misturar composição anterior com limites novos no histórico ou salvamento.
        return

    # Avisos do motor que são decisão sobre a carteira (teto ajustado, líder
    # reprovado, nenhuma indústria aprovada) ficam; retorno de solver vai ao log.
    for warning in result.get("warnings", []):
        if str(warning).startswith(_PREFIXOS_AVISO_SOLVER):
            detalhe_tecnico(str(warning), codigo="eua.criacao_solver")
        else:
            st.warning(warning)
    if result.get("review_portfolio") is not None:
        from design.portfolio_review import render_portfolio_review

        render_portfolio_review(result["review_portfolio"], key="us_review")
        return
    # Bloqueio antes de qualquer número: sem liquidez verificada não existe
    # carteira publicável, e mostrar auditoria de indústria abaixo faria parecer
    # que só faltou afrouxar um parâmetro.
    if result.get("blocking_error"):
        st.error(result["blocking_error"])
        return
    if result.get("history_required_unavailable"):
        st.error("A validação histórica exigida não está disponível no momento.")
        aviso_lacuna("A validação histórica foi exigida, mas o painel PIT não está "
                     "disponível.", codigo="tela.eua.criacao_validacao_sem_pit")
        return

    audit = result.get("industry_audit", pd.DataFrame())
    exclusions = result.get("exclusions", pd.DataFrame())
    holdings = result.get("holdings", pd.DataFrame())
    metrics = result.get("metrics", {})
    macro_info = result.get("macro") or {}
    snapshot = result.get("macro_snapshot")
    if snapshot is None:
        aviso_lacuna(
            "Camada macro indisponível nesta execução (sem Docker local e sem arquivo "
            "publicado recente); a composição mantém os pesos fundamentalistas.",
            codigo="tela.eua.criacao_macro_indisponivel")
    else:
        detalhe_tecnico(
            f"{result.get('macro_fonte') or 'Macro'} · corte {snapshot.as_of:%d/%m/%Y %H:%M UTC} · "
            f"cobertura {macro_info.get('coverage', 0):.0%} · "
            f"turnover atribuído ao macro {macro_info.get('turnover', 0):.1%}.",
            codigo="eua.criacao_macro_fonte")
        st.info("Impactos macro são contexto histórico, não previsão de retorno.")
        from functools import partial

        from core.us_portfolio_creation import apply_us_macro
        from design.macro_portfolio import render_historical_macro_path

        render_historical_macro_path(
            asset_class="us", holdings=holdings,
            symbol_column="symbol", sector_column="sector_group",
            score_column="entry_score", mode=str(macro_info.get("mode")),
            key="us_portfolio_macro_history",
            rebuild=partial(apply_us_macro, params=params),
            signature_context={"params": params_to_dict(params), "snapshot": snapshot.snapshot_id},
        )
    approved = int(audit["status"].eq("Aprovada").sum()) if not audit.empty else 0
    observation = int(audit["status"].eq("Observação").sum()) if not audit.empty else 0
    excluded = int(audit["status"].eq("Excluída").sum()) if not audit.empty else 0

    secao_titulo("Composição de Entrada — Mercado Americano", "🎯")
    nao_verificadas = len(result.get("liquidity_unverified") or [])
    piso_liquidez = float(result.get("liquidity_floor_usd") or 0.0)
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        card_metrica("Empresas elegíveis", f"{result.get('eligible_count', 0):,}".replace(",", "."))
    with c2:
        card_metrica("Indústrias aprovadas", str(approved))
    with c3:
        card_metrica("Em observação", str(observation))
    with c4:
        card_metrica("Indústrias excluídas", str(excluded))
    with c5:
        # O número precisa aparecer nos dois modos, com significados opostos:
        # com piso ligado ele conta quem FICOU DE FORA por falta de medição;
        # sem piso, quem ENTROU sem liquidez validada.
        card_metrica(
            "Liquidez não verificada", nao_verificadas,
            positivo=False if nao_verificadas else None,
            ajuda=("Sem série de volume — barradas pelo piso de negociabilidade."
                   if piso_liquidez > 0 else
                   "Sem série de volume — no modo exploratório (sem piso) elas "
                   "permanecem no universo com a liquidez NÃO validada."))

    with st.expander("🩺 Qualidade, saneamento e filtros do universo"):
        if not exclusions.empty:
            filtered = int(exclusions["count"].sum())
            st.caption(
                f"Universo inicial: {result.get('universe_count', 0):,} · "
                f"removidas sequencialmente: {filtered:,} · "
                f"elegíveis: {result.get('eligible_count', 0):,}"
            )
            st.dataframe(
                exclusions.rename(columns={"label": "Critério", "count": "Removidas"})[
                    ["Critério", "Removidas"]
                ], hide_index=True, width="stretch",
            )
        st.markdown(
            "**Tratamento setorial:** empresas operacionais usam ROIC e financeiras "
            "usam ROE. O universo é de ações — REIT, fundo, SPAC, preferencial, "
            "warrant e unit são excluídos, porque não se comparam a uma companhia "
            "operacional no mesmo ranking. Múltiplos e fundamentos seguem SEC/US GAAP."
        )

    if not audit.empty:
        secao_titulo("Auditoria por Indústria", "📋")
        detalhe_tecnico(
            "A aprovação atual combina tamanho de amostra, score dos líderes, "
            "vantagem relativa e resiliência opcional. Rank‑IC aparece somente "
            "quando existe histórico ponto-no-tempo — cujo universo inclui as "
            "empresas que saíram da bolsa, nas safras em que estavam vivas.",
            codigo="eua.criacao_auditoria_industria")
        audit_show = audit.copy()
        audit_show["Indústria"] = audit_show["industry_group"].map(translate_us_industry)
        audit_show["Setor"] = audit_show["sector_group"]
        audit_show["Score líderes"] = audit_show["avg_entry_score"].round(1)
        audit_show["Vantagem (pts)"] = audit_show["score_edge"].round(1)
        audit_show["Cobertura (%)"] = audit_show["avg_coverage"].round(0)
        audit_show["Spread resiliência (p.p.)"] = audit_show[
            "resilience_spread_pp"
        ].round(1)
        audit_show["Rank‑IC"] = audit_show["rank_ic"].round(3)
        st.dataframe(
            audit_show[["Indústria", "Setor", "status", "company_count", "leaders",
                        "Score líderes", "Vantagem (pts)", "Cobertura (%)",
                        "Spread resiliência (p.p.)", "Rank‑IC", "reason"]].rename(columns={
                            "status": "Status", "company_count": "Empresas",
                            "leaders": "Líderes", "reason": "Motivo",
                        }), hide_index=True, width="stretch",
        )

    if holdings is None or holdings.empty:
        estado_vazio(
            "Nenhuma carteira viável foi formada com os critérios atuais. "
            "Revise o score mínimo, a amostra por indústria ou os limites de peso.",
            "🚀",
        )
        return

    secao_titulo("Empresas Selecionadas", "🏢")
    st.caption(
        "Somente líderes de indústrias aprovadas. Os pesos são projetados em "
        "conjunto para respeitar concentração por ativo, indústria e setor."
    )
    _render_us_portfolio_cards(holdings)

    floor_log = result.get("quality_floor_log") or {}
    if any(floor_log.get(k) for k in
           ("reprovados", "afrouxado_por_viabilidade", "carteira_preservada")):
        with st.expander("⛔ Empresas barradas pelo piso de qualidade"):
            st.caption(
                "Eram as melhores da indústria pelo score, mas o veredito de "
                "qualidade contábil as marcou como 'Excluída'. Quando existe candidato "
                "aprovado na MESMA indústria, ele herda a vaga; quando não "
                "existe, a vaga fica vazia — declarar é melhor que rebaixar em "
                "silêncio para o segundo pior."
            )
            st.dataframe(
                pd.DataFrame(floor_log["reprovados"]).rename(columns={
                    "symbol": "Ticker", "grupo": "Indústria", "motivo": "Motivo"}),
                hide_index=True, width="stretch")
            if floor_log.get("substituicoes"):
                st.dataframe(
                    pd.DataFrame(floor_log["substituicoes"]).rename(columns={
                        "entra": "Entrou", "sai": "Saiu", "grupo": "Indústria"}),
                    hide_index=True, width="stretch")
            if floor_log.get("sem_substituto"):
                vazias = ", ".join(
                    f"{d['symbol']} ({d['grupo']})"
                    for d in floor_log["sem_substituto"][:12])
                st.caption(f"Sem substituto aprovado na indústria: {vazias}.")
            # A proteção que cede tem de dizer que cedeu. Sem estes dois
            # blocos, a guarda de viabilidade e a rede final devolveriam
            # empresa reprovada para a carteira sem deixar rastro na tela —
            # que é justamente o defeito que o piso veio corrigir.
            if floor_log.get("afrouxado_por_viabilidade"):
                st.warning(
                    "Guarda de viabilidade acionada: nenhum candidato destas "
                    "indústrias passou no piso, e a exclusão vinha só da SOMA "
                    "de sinais que o motor declara insuficientes sozinhos. O "
                    "líder foi readmitido para a indústria não sumir da "
                    "carteira — a reprovação segue registrada acima.")
                st.dataframe(
                    pd.DataFrame(floor_log["afrouxado_por_viabilidade"])
                    .rename(columns={"symbol": "Ticker", "grupo": "Indústria",
                                     "motivo": "Motivo", "cessao": "Cessão"}),
                    hide_index=True, width="stretch")
            if floor_log.get("carteira_preservada"):
                st.error(
                    "O piso de qualidade reprovaria TODAS as indústrias "
                    "aprovadas. A carteira foi remontada sem ele para não sair "
                    "vazia: nenhum nome abaixo passou no piso, e a leitura "
                    "desta carteira exige ler os motivos acima antes.")

    intel_log = result.get("inteligencia_log") or {}
    if _portao_ui.tem_conteudo(intel_log):
        with st.expander(_portao_ui.TITULO, expanded=bool(
                intel_log.get("vetados") or intel_log.get("persistentes"))):
            _portao_ui.render(
                intel_log, grupo=_portao_ui.GRUPO["us"],
                como_substitui=_portao_ui.COMO_SUBSTITUI["us"])

    secao_titulo("Travessia de Recessão", "🛡️")
    _render_us_ciclo(holdings)
    _render_us_estilos(holdings)

    secao_titulo("Resumo do Portfólio", "📊")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        card_metrica("Ativos", str(metrics.get("assets", 0)),
                     delta=f"{metrics.get('effective_assets', 0):.1f} efetivos")
    with m2:
        card_metrica("Score de entrada", f"{metrics.get('entry_score', 0):.1f}/100")
    with m3:
        card_metrica("Maior posição", f"{metrics.get('max_weight', 0):.1%}")
    with m4:
        card_metrica("Capital simulado", f"US$ {metrics.get('capital_usd', 0):,.2f}")
    m5, m6, m7, m8 = st.columns(4)
    with m5:
        card_metrica("Setores", str(metrics.get("sectors", 0)))
    with m6:
        card_metrica("Indústrias", str(metrics.get("industries", 0)))
    with m7:
        card_metrica("Cobertura média", f"{metrics.get('coverage', 0):.1f}%")
    with m8:
        card_metrica("Benchmark", str(metrics.get("benchmark", "—")))

    can_publish = bool(result.get("can_publish"))
    publication_blocking_error = result.get("publication_blocking_error")
    if publication_blocking_error:
        st.warning(publication_blocking_error)

    use_col, save_col, info_col = st.columns([1, 1, 2])
    with use_col:
        if st.button("💾 Usar na Avaliação de Portfólio", type="primary",
                     key="us_create_save_model", disabled=not can_publish):
            st.session_state["us_portfolio_model"] = holdings.copy()
            st.success("Carteira-modelo enviada para Avaliação de Portfólio.")
    with save_col:
        # Mesma decisão que "Salvar portfólio padrão" na B3: sem persistir, o
        # Dashboard Geral não tem como exibir a carteira americana entre reruns.
        if st.button("⭐ Salvar como carteira padrão", key="us_create_persist_model",
                     disabled=not can_publish):
            try:
                from core.us_portfolio_model import save_us_portfolio_model
                model_id = save_us_portfolio_model(
                    holdings.to_dict("records"),
                    params={
                        **(result.get("params") or {}),
                        "macro_mode": macro_info.get("mode"),
                        "macro_snapshot": snapshot.to_payload() if snapshot else None,
                        "macro_as_of": (
                            snapshot.as_of.isoformat() if snapshot else None
                        ),
                        "macro_coverage": macro_info.get("coverage"),
                        **veredito.log_para_payload(
                            result.get("inteligencia_log")),
                    },
                    metrics={**metrics, "macro_turnover": macro_info.get("turnover")},
                    name=f"Portfolio EUA Modelo {pd.Timestamp.today().year}",
                )
                st.success(
                    "Carteira padrão americana salva — aparece no Dashboard Geral. "
                    f"ID: {model_id[:8]}"
                )
            except Exception as exc:  # noqa: BLE001 - fronteira de persistência
                falha_de_acao("Não foi possível salvar a carteira padrão.", exc)
        _render_us_portfolio_version_history("us_create_persist_model")
    with info_col:
        st.info(
            f"{len(holdings)} ativos · aporte mensal simulado de "
            f"US$ {metrics.get('monthly_contribution_usd', 0):,.2f} · "
            f"Treasury de referência {metrics.get('risk_free_rate', 0):.2%}."
        )

    chart1, chart2 = st.columns(2)
    with chart1:
        fig = px.pie(
            holdings, names="symbol", values="weight", hole=.48,
            title="Alocação por ativo", labels={"symbol": "Ticker", "weight": "Peso"},
        )
        fig.update_layout(**_PLOT_LAYOUT, height=410)
        st.plotly_chart(fig, width="stretch", key="us_create_pie")
    with chart2:
        sector_weights = holdings.groupby("sector_group", dropna=False)["weight"].sum().reset_index()
        fig = px.bar(
            sector_weights.sort_values("weight"), x="weight", y="sector_group",
            orientation="h", title="Alocação por setor",
            labels={"sector_group": "Setor", "weight": "Peso"},
        )
        fig.update_layout(**_PLOT_LAYOUT, height=410, xaxis_tickformat=".0%")
        st.plotly_chart(fig, width="stretch", key="us_create_sector")

    secao_titulo("Relação entre Score e Cobertura", "🔭")
    fig = px.scatter(
        holdings, x="coverage", y="entry_score", color="sector_group",
        size="weight", hover_name="symbol",
        labels={"coverage": "Cobertura fundamentalista (%)",
                "entry_score": "Score de entrada", "sector_group": "Setor",
                "weight": "Peso"},
    )
    fig.update_layout(**_PLOT_LAYOUT, height=430)
    st.plotly_chart(fig, width="stretch", key="us_create_scatter")

    with st.expander("🧪 Validação histórica e benchmarks"):
        if history_available:
            detalhe_tecnico(
                "O painel PIT está disponível. A auditoria acima exibe o Rank‑IC "
                "por indústria, sem usar demonstrações publicadas após a data-base.",
                codigo="eua.criacao_painel_pit_disponivel")
            _tab_backtests(status)
        else:
            st.info("Esta análise não está disponível no momento.")
            aviso_lacuna(
                "Nenhum retorno histórico foi simulado, por integridade "
                "metodológica. " + _motivo_sem_painel(score_panel),
                codigo="tela.eua.criacao_validacao_sem_painel")

    with st.expander("📚 Metodologia e equivalências B3 × Estados Unidos"):
        st.markdown("""
**Elementos preservados da B3**

- aplicação da metodologia em escala, sobre todas as indústrias de uma vez;
- filtros de universo, auditoria dos grupos, seleção de líderes e carteira multissetorial;
- score de entrada, controles de concentração, aporte mensal, gráficos e envio para avaliação;
- separação entre evidência atual e validação histórica ponto-no-tempo.

**Adaptações para os Estados Unidos**

- Tesouro Selic → Treasury de 3 meses como custo de oportunidade;
- Ibovespa/mercado brasileiro → S&P 500, Russell 1000, Nasdaq‑100 ou pesos iguais;
- segmentos B3 → indústrias SEC/SIC consolidadas em setores americanos;
- liquidez em reais → valor de mercado em dólares;
- DRE societária brasileira → demonstrações anuais SEC/US GAAP;
- ROIC único → ROIC para operacionais e ROE para financeiras (REIT não entra no universo);
- seleção pequena → pré-filtro vetorizado e revisão apenas dos líderes por indústria;
- limite por setor → limites simultâneos por ativo, indústria e setor, com cap adaptativo auditável.

O score não incorpora opinião política nem previsão macro como fato. Fed, inflação,
PIB e emprego entram como cenário explícito na avaliação posterior da carteira.
        """)


def _tab_avaliacao_portfolio(status: dict) -> None:
    """Delega para a tela dedicada, espelho da Avaliação B3.

    A avaliação quantitativa rápida que vivia aqui foi absorvida: o relatório
    institucional cobre diversificação, concentração e adequação com o mesmo
    contrato da B3, sobre a carteira SALVA, não sobre uma seleção improvisada.
    """
    if _empty_if_offline(status, "Sem dados locais para avaliar a carteira.", "🧠"):
        return
    from views import analise_portfolio_us

    analise_portfolio_us.render(show_header=False)


def _render_us_estilos(holdings: pd.DataFrame) -> None:
    """Momento e P&D de cada ação escolhida: as duas trilhas de estilo da nota."""
    cols = [c for c in ("symbol", "name", "momento_12_1", "score_momentum",
                        "pd_valor_mercado", "score_pd") if c in holdings]
    if "symbol" not in cols or not {"score_momentum", "score_pd"} & set(cols):
        return
    show = holdings[cols].copy()
    for coluna in ("momento_12_1", "pd_valor_mercado"):
        if coluna in show:
            show[coluna] = pd.to_numeric(show[coluna], errors="coerce") * 100
    with st.expander("🚀 Momento e pesquisa e desenvolvimento das escolhidas"):
        st.dataframe(
            localize_us_company_frame(show).rename(columns={
                "symbol": "Ticker", "name": "Nome", "momento_12_1": "Momento 12m (%)",
                "score_momentum": "Nota momento", "pd_valor_mercado": "P&D / valor de mercado (%)",
                "score_pd": "Nota P&D"}),
            hide_index=True, width="stretch",
            column_config={c: st.column_config.NumberColumn(format="%.1f")
                           for c in ("Momento 12m (%)", "Nota momento",
                                     "P&D / valor de mercado (%)", "Nota P&D")})
        st.caption(
            "Momento: retorno total de 12 meses até 1 mês antes do último pregão. P&D: "
            "despesa de pesquisa e desenvolvimento do último exercício sobre o valor de "
            "mercado. As notas são o percentil no universo elegível; vazio é neutro (50) "
            "na nota de entrada -- a empresa não declara P&D ou não tem 12 meses de preço. "
            "Nos EUA, de 1927 a 2025, o terço com mais momento rendeu 4,1% a.a. acima do "
            "mercado e o terço com mais P&D, 3,7% a.a. (base acadêmica sem viés de "
            "sobrevivência, antes de custos).")


# ── Criação de Portfólio ──────────────────────────────────────────────────────
def _tab_portfolio(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para montar a carteira.", "📦")
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        estado_vazio("Sem empresas com pontuação para compor a carteira.", "📦")
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
        wmode = st.selectbox(
            "Ponderação", ["score", "equal"],
            format_func=lambda value: _WEIGHTING_LABELS[value], key="us_pf_wmode",
        )

    holdings = build_portfolio(scored, PortfolioConstraints(
        top_n=top_n, max_weight=maxw, max_sector_weight=maxs, weighting=wmode,
        max_assets=top_n, min_assets=min(5, top_n)))
    if holdings.empty:
        estado_vazio("Nenhum ativo elegível com as restrições atuais.", "📦")
        return

    holdings = localize_us_company_frame(holdings)
    show = holdings.copy()
    show["weight"] = (show["weight"] * 100).round(2)
    st.dataframe(show.rename(columns={
        "symbol": "Ticker", "name": "Nome", "sector": "Setor",
        "industry": "Indústria", "score": "Pontuação", "weight": "Peso %"}),
        hide_index=True, width="stretch")

    if "sector" in holdings.columns:
        secao_titulo("Alocação por setor", "🧩")
        alloc = (holdings.groupby("sector")["weight"].sum() * 100).round(1) \
            .sort_values(ascending=False)
        st.dataframe(alloc.rename("Peso %").reset_index().rename(
            columns={"sector": "Setor"}), hide_index=True, width="stretch")
    detalhe_tecnico(
        "Limites iterativos por posição e setor (heurística de projeção, não "
        "otimizador de média-variância). Índices de referência (S&P 500, Nasdaq-100 "
        "e Russell 2000) e a carteira de pesos iguais entram no teste histórico "
        "quando houver dados suficientes.", codigo="eua.carteira_modelo_heuristica")


# ── Testes históricos sem viés temporal ───────────────────────────────────────
def _tab_backtests(status: dict) -> None:
    secao_titulo("Teste histórico com janela móvel — ponto no tempo", "🧪")
    st.caption("Pontuações recalculadas em cada data usando apenas informações já "
               "disponíveis, evitando antecipação indevida.")
    detalhe_tecnico("Requer histórico PIT: python run_us_ingest.py score-history "
                    "--warehouse.", codigo="eua.backtests_requer_pit")
    c1, c2, c3 = st.columns(3)
    with c1:
        top_n = st.slider("Top N por período", 5, 40, 20, key="us_bt_topn")
    with c2:
        wmode = st.selectbox(
            "Ponderação", ["score", "equal"],
            format_func=lambda value: _WEIGHTING_LABELS[value], key="us_bt_wmode",
        )
    with c3:
        benchmark = st.selectbox("Benchmark de mercado", us.benchmark_options(),
                                 key="us_bt_benchmark")

    res = us.backtest(top_n=top_n, weighting=wmode, benchmark=benchmark)
    if not res.get("ok"):
        estado_vazio("Esta análise não está disponível no momento.", "🧪")
        aviso_lacuna(res.get("reason", "teste histórico indisponível"),
                     codigo="tela.eua.backtests_indisponivel")
        return

    ic = res["rank_ic"]
    p = res["portfolio"]

    def _p(x, mult=100, suf="%"):
        return "—" if x is None else f"{x*mult:.2f}{suf}"

    secao_titulo(f"Correlação entre classificação e retorno ({res['n_periods']} períodos)", "📐")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Correlação média", "—" if ic["mean"] is None else f"{ic['mean']:.3f}")
    with c2:
        card_metrica("Estatística t", "—" if ic["t_stat"] is None else f"{ic['t_stat']:.2f}")
    with c3:
        card_metrica("p-valor", "—" if ic["p_value"] is None else f"{ic['p_value']:.3f}")
    with c4:
        card_metrica("Taxa de acerto", _p(ic["hit_rate"]))
    detalhe_tecnico(_aviso_sobrevivencia(), codigo="eua.backtests_vies_sobrevivencia")

    _bloco_liquido_us(res)

    secao_titulo("Desempenho bruto em dólar versus pesos iguais", "📈")
    st.caption("Retorno total em USD, sem custo, imposto nem câmbio: é a "
               "régua da ordenação (Rank-IC e o excesso guardado na medição "
               "fora da amostra), não o que sobra no bolso.")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Retorno anual.", _p(p["ann_return"]))
    with c2:
        card_metrica("Excesso sobre pesos iguais", _p(res.get("excess_ann_vs_ew")))
    with c3:
        # `performance_stats` e chamada sem `rf`, entao a taxa livre de risco e
        # ZERO: o numero e retorno sobre volatilidade, nao excesso sobre
        # volatilidade. Com Treasury em digito unico isso ja desloca o valor de
        # forma material, e o rotulo "Sharpe" promete a outra coisa. Enquanto
        # nao houver serie de taxa livre de risco americana no pipeline, o
        # cartao diz o que de fato calcula.
        card_metrica(
            "Sharpe (rf = 0)",
            "—" if p["sharpe"] is None else f"{p['sharpe']:.2f}",
            ajuda="Retorno anualizado dividido pela volatilidade anualizada. "
                  "A taxa livre de risco não entra: sem série de Treasury no "
                  "pipeline, descontá-la seria inventar o número. Um Sharpe "
                  "com rf > 0 seria menor que este.",
        )
    with c4:
        card_metrica("Queda máxima", _p(p["max_drawdown"]))
    c5, c6, c7, c8 = st.columns(4)
    with c5:
        card_metrica("Sortino", "—" if p["sortino"] is None else f"{p['sortino']:.2f}")
    with c6:
        card_metrica("Calmar", "—" if p["calmar"] is None else f"{p['calmar']:.2f}")
    with c7:
        card_metrica("Volatilidade", _p(p["volatility"]))
    with c8:
        card_metrica("Giro médio da carteira", _p(res.get("avg_turnover")))
    from core.us_survivorship import frase_medicao_de_retorno
    _frase_retorno = frase_medicao_de_retorno()
    if _frase_retorno:
        detalhe_tecnico(_frase_retorno, codigo="eua.backtests_medicao_retorno")

    benchmark_state = res.get("benchmark") or {}
    if benchmark_state.get("modo") == "indice" and benchmark_state.get("ok"):
        secao_titulo(f"Comparação contra {benchmark_state['simbolo']}", "📊")
        card_metrica("Excesso anualizado", _p(res.get("excess_ann_vs_benchmark")))
        detalhe_tecnico(f"Retorno total em USD, nas mesmas janelas de "
                        f"{benchmark_state.get('horizonte_meses')} meses do painel PIT.",
                        codigo="eua.backtests_benchmark_janelas")
    elif benchmark_state and not benchmark_state.get("ok"):
        aviso_lacuna(benchmark_state.get("mensagem", "Benchmark indisponível; "
                     "o excesso contra índice não foi calculado."),
                     codigo="tela.eua.benchmark_pit_indisponivel", nivel="warning")
    elif benchmark_state.get("modo") == "sem_indice":
        st.caption("Comparação contra índice desativada explicitamente; pesos iguais "
                   "do universo continuam como baseline interno.")

    # A-116: a fracao do painel que saiu na ultima cotacao porque a acao
    # deslistou. Sem isso o leitor nao tem como saber quanto do resultado
    # repousa sobre saida forcada -- e a versao anterior simplesmente apagava
    # essas acoes, o que fazia o backtest parecer melhor do que foi.
    censura = res.get("censura") or {}
    if censura.get("sem_saida"):
        # A-161: o silencio de antes era lido como "nao houve deslistagem".
        detalhe_tecnico(
            f"Nenhuma das {censura.get('n_observacoes', 0)} observações, ao "
            f"longo de {censura.get('anos', 0)} anos, é de ação que parou de "
            "negociar. Isso não é ausência de deslistagem no mercado: é "
            "ausência dela neste painel, cujo universo é montado a partir "
            "de quem sobreviveu até hoje. O resultado é o de uma "
            "carteira que nunca teve uma posição quebrar. "
            + _aviso_sobrevivencia(),
            codigo="eua.backtests_censura_sem_saida")
    if censura.get("n_censurado"):
        # "Elas entram pela última cotação" era o fecho FIXO desta frase. Com a
        # convenção de retorno de deslistagem ligada, parte destas linhas não
        # entra mais assim -- e uma frase fixa sobre o estado do dado envelhece
        # na direção de continuar soando como rigor depois de virar falsa.
        convencionado = int(censura.get("n_convencionado") or 0)
        texto = (
            f"{censura['n_censurado']} de {censura['n_observacoes']} observações "
            f"({censura.get('fracao_censurada', 0.0):.1%}) são de ações que "
            "pararam de negociar antes do fim do horizonte. ")
        if convencionado:
            from core.us_convencao_saida import frase_convencao
            texto += (
                f"Destas, {convencionado} receberam retorno por convenção "
                "declarada em vez da última cotação, porque a causa da saída "
                f"é conhecida. {frase_convencao()} As demais entram pela última "
                "cotação, que para uma falência ainda tende a ficar otimista.")
        else:
            texto += (
                "Elas entram pela última cotação, como se a posição tivesse "
                "sido vendida ali. Deslistagem real costuma liquidar mais perto "
                "de zero, então o retorno destas ainda tende a ficar otimista.")
            if not censura.get("n_desfechos"):
                texto += (
                    " A causa de cada saída ainda não foi publicada na vitrine "
                    "(`scripts/classificar_saidas_us.py` e republicação), então "
                    "a convenção por desfecho não está valendo aqui.")
        detalhe_tecnico(texto, codigo="eua.backtests_censura_deslistagem")
    if censura.get("n_inobservavel"):
        detalhe_tecnico(
            f"{censura['n_inobservavel']} pares (data, ação) ficaram de fora por "
            "não haver preço no horizonte nem cotação de saída — retorno "
            "inobservável, não retorno zero.",
            codigo="eua.backtests_censura_inobservavel")

    concentration = res.get("concentration") or {}
    if concentration and not concentration.get("eligible_for_conclusion", True):
        st.warning(
            "Resultado teórico sem teto de posição: o maior peso observado "
            f"({concentration.get('max_weight', 0.0):.1%}) excede a política "
            f"de {concentration.get('policy_max_weight', 0.0):.1%} "
            f"(HHI máximo {concentration.get('max_hhi', 0.0):.3f}). "
            "Não use este backtest para concluir sobre uma carteira investível."
        )

    if res.get("equity_curve"):
        secao_titulo("Curva de capital", "📉")
        curve = pd.DataFrame({"Bruta (US$)": res["equity_curve"]},
                             index=res.get("dates"))
        liquido = res.get("liquido") or {}
        if liquido.get("ok") and liquido.get("dates") == res.get("dates"):
            curve["Líquida (R$)"] = liquido["equity_curve_liquido"]
        st.line_chart(curve)


# ── Qualidade dos Dados ───────────────────────────────────────────────────────
def _tab_qualidade() -> None:
    if not us.schema_ready():
        estado_vazio("Esta análise não está disponível no momento.", "🔌")
        aviso_lacuna("Estrutura de dados market_us ainda não aplicada.",
                     codigo="tela.eua.qualidade_sem_schema")
        return
    secao_titulo("Auditoria de qualidade", "🩺")
    df = us.quality_audit(limit=200)
    if df is None or df.empty:
        st.info("Esta análise não está disponível no momento.")
        aviso_lacuna("Nenhum registro de auditoria ainda. Rode python "
                     "run_us_ingest.py validate --warehouse após a carga.",
                     codigo="tela.eua.qualidade_sem_auditoria")
        return
    show = df.rename(columns={
        "created_at": "Data", "symbol": "Ticker", "table_name": "Tabela",
        "check_name": "Verificação", "severity": "Severidade",
        "passed": "Aprovado", "detail": "Detalhes",
    })
    if "Severidade" in show:
        show["Severidade"] = show["Severidade"].replace({
            "info": "Informação", "warning": "Aviso", "error": "Erro", "critical": "Crítica",
        })
    if "Aprovado" in show:
        show["Aprovado"] = show["Aprovado"].map(
            lambda value: "Sim" if isinstance(value, (bool, np.bool_)) and bool(value)
            else "Não" if isinstance(value, (bool, np.bool_)) else value
        )
    st.dataframe(show, hide_index=True, width="stretch")


# ── Sincronização ─────────────────────────────────────────────────────────────
def _tab_sincronizacao(status: dict) -> None:
    secao_titulo("Sincronização de Dados Americanos", "🔄")
    st.markdown(
        "A ingestão roda **fora da interface**, por linha de comando, gravando no "
        "**armazém de dados local** (Postgres em `127.0.0.1:5433`). Fonte padrão: "
        "**SEC EDGAR** (fundamentos, gratuita — exige `SEC_USER_AGENT` no `.env` "
        "com nome e e-mail) + **yfinance** (preços). Credenciais/identificação são "
        "usadas **apenas** pela CLI — nunca pela interface.")

    runs = us.ingestion_runs()
    if runs is not None and not runs.empty:
        st.markdown("**Execuções recentes**")
        show_runs = runs.rename(columns={
            "run_key": "Execução", "domain": "Domínio", "status": "Situação",
            "calls_made": "Chamadas realizadas", "rows_written": "Linhas gravadas",
            "started_at": "Início", "finished_at": "Término",
        })
        if "Situação" in show_runs:
            show_runs["Situação"] = show_runs["Situação"].replace({
                "running": "Em execução", "success": "Concluída", "completed": "Concluída",
                "failed": "Falhou", "error": "Erro", "partial": "Parcial",
            })
        if "Domínio" in show_runs:
            show_runs["Domínio"] = show_runs["Domínio"].replace({
                "universe": "Universo", "fundamentals": "Fundamentos", "prices": "Preços",
                "bootstrap": "Carga inicial", "daily": "Atualização diária",
                "snapshot": "Vitrine", "validation": "Validação",
            })
        st.dataframe(show_runs, hide_index=True, width="stretch")
    else:
        st.caption("Nenhuma execução de ingestão registrada ainda.")

    st.markdown("**Comandos** (rodar no terminal, na raiz do projeto):")
    st.code(
        "# 1) aplicar a estrutura local (idempotente)\n"
        "python run_us_ingest.py init-schema --warehouse\n\n"
        "# 2) testar chave + conexão\n"
        "python run_us_ingest.py test --warehouse --json\n\n"
        "# 3) estimar a carga ANTES de baixar (simulação, sem rede)\n"
        "python run_us_ingest.py estimate --tickers AAPL MSFT NVDA\n\n"
        "# 4) carregar o cadastro do universo (NYSE/Nasdaq/AMEX)\n"
        "python run_us_ingest.py universe --warehouse --limit 200\n\n"
        "# 5) carga histórica de um lote pequeno\n"
        "python run_us_ingest.py bootstrap --warehouse --tickers AAPL MSFT --years 20 --json\n\n"
        "# 6) retomar após falha / atualizar\n"
        "python run_us_ingest.py resume --warehouse\n"
        "python run_us_ingest.py daily  --warehouse --tickers AAPL MSFT\n\n"
        "# 7) auditar qualidade\n"
        "python run_us_ingest.py validate --warehouse --json",
        language="bash")

    st.markdown("**Publicar a vitrine** (para o deploy no Streamlit Cloud mostrar "
                "os dados). Só a vitrine compacta vai para o Supabase; os "
                "históricos pesados ficam no armazém de dados local.")
    st.code(
        "# 8) construir a vitrine no armazém de dados local (sem rede)\n"
        "python run_us_ingest.py snapshot --warehouse --json\n\n"
        "# 9) publicar a vitrine no Supabase (conexão direta, não o pooler)\n"
        "python scripts/publish_us_snapshot.py `\n"
        '  --source-url "postgresql://postgres:<senha>@127.0.0.1:5433/postgres" `\n'
        '  --target-url "<SUPABASE_UNIFICADO_URL>" --dry-run   # confira, depois sem --dry-run',
        language="powershell")
    st.caption("Testes históricos e a sincronização rodam **só localmente** — a vitrine "
               "traz pontuação, dossiê, assimetria e Piotroski/Altman/Sloan já calculados, "
               "mas o teste sem viés temporal precisa do histórico completo no armazém local.")

    st.markdown("**Ou rodar a aplicação localmente** contra o armazém de dados (mostra tudo, "
                "sem publicar):")
    st.code(
        '$env:SUPABASE_UNIFICADO_URL = "postgresql://postgres:<senha>@127.0.0.1:5433/postgres"\n'
        "streamlit run app.py",
        language="powershell")
    if not status.get("schema_ready"):
        if status.get("db_is_local"):
            st.warning("A estrutura `market_us` ainda não existe no armazém local. Rode o passo 1.")
        else:
            st.info("Este deploy lê o Supabase e **ainda não tem a vitrine publicada**. "
                    "Rode os passos 8–9 na sua máquina para popular a nuvem, ou rode "
                    "a aplicação localmente (regra do projeto: o histórico pesado fica "
                    "exclusivamente no armazenamento local; só a vitrine compacta vai ao Supabase).",
                    icon="☁️")


# ── Metodologia ───────────────────────────────────────────────────────────────
def _tab_metodologia() -> None:
    secao_titulo("Metodologia — Empresas Americanas", "📚")
    st.markdown(f"""
**Fonte e armazenamento.** Fundamentos: **SEC EDGAR** (relatórios 10-K em XBRL —
dados públicos e de domínio público; a data de protocolo vira `available_at`, o
padrão-ouro para análises em cada ponto do tempo). Preços: **yfinance**. Ambas acessadas só na
ingestão. Todo histórico vive no **armazém de dados local** (`market_us.*`), isolado do
B3/FII. A interface prioriza a operação local: lê o banco e funciona sem rede
após a carga.

**Identidade.** A empresa é identificada por **CIK** (não pelo ticker, que é
reutilizado/renomeado). O histórico de símbolos fica em `market_us.ticker_aliases`;
o histórico de uma empresa **não é apagado** ao trocar de ticker.

**Ponto no tempo.** Cada fato financeiro guarda `reference_date` (fim do período),
`published_date` (protocolo) e `available_at` (quando era conhecível). Os testes
históricos filtram por `available_at` — nunca por data de ingestão — para evitar
antecipação indevida. **Limite conhecido:** o universo histórico já inclui empresas
deslistadas (1.603 marcadas com `delisted_date`, 702 saídas ao longo das safras), e
cada uma entra apenas nas safras anteriores à própria saída. O que ainda falta é o
RETORNO delas: não há fonte acessível de cotação para ticker morto, então essas
linhas saem da apuração de excesso. O Rank-IC já é medido em universo com mortes; o
retorno realizado ainda não observa a perda permanente de capital.

**Normalização.** Ausência nunca vira zero; unidades e períodos (anual/trimestral/
TTM) são rotulados explicitamente; divergência entre ticker solicitado e retornado
é rejeitada, não gravada sob o símbolo errado.

**Pontuação.** Fundamentalista v{US_FUNDAMENTAL_SCORE_VERSION}, relativa por
setor/indústria, versionada por ponto no tempo em `market_us.score_vintages`.

**Macro EUA.** O cenário separa a pontuação fundamentalista do ajuste de regime e
considera juros básicos do Fed, inflação ao consumidor (CPI), PIB real, desemprego,
curva dos títulos do Tesouro de 10 e 2 anos e spread de crédito de alto rendimento.
As fontes oficiais recomendadas para o processo de dados são Federal Reserve/FRED,
BLS e BEA. Valores digitados na interface são premissas de
simulação, nunca apresentados como cotações oficiais em tempo real.

**Regulação, contabilidade e tributação.** A interpretação segue US GAAP e as
divulgações à SEC, incluindo relevância de recompras, remuneração baseada em ações,
diluição, redução ao valor recuperável e ativos intangíveis. Instituições
financeiras usam pesos setoriais próprios: alavancagem contábil é o negócio de um
banco, não um risco a punir. Fundos imobiliários americanos (REITs) não são
analisados aqui — a seção cobre ações, e o REIT distribui por obrigação legal e
deprecia imóvel contra o lucro, o que o faz parecer barato e alavancado pelos dois
motivos errados dentro de um ranking relativo.
Retenção de imposto na fonte, residência fiscal, imposto sucessório e tratados
dependem do investidor e não alteram automaticamente a pontuação da companhia. Risco
político, fiscal, antitruste e mudanças regulatórias entram como contexto de
cenário, sem conclusão jurídica ou tributária individual.

**Escopo desta seção.** Aqui mora a análise fundamentalista e a carteira-modelo:
empresas avaliadas pelo que já entregam (qualidade, crescimento, solidez,
eficiência de capital, avaliação e retorno ao acionista).

> ℹ️ A fonte padrão é a SEC EDGAR porque os dados são de **domínio público** —
> sem licença restritiva sobre o armazenamento local. Os Termos da FMP (fonte
> opcional, desativada por padrão) proíbem cópia/armazenamento sem autorização
> escrita e exigem apagar os dados ao encerrar a assinatura; só a use com
> licença compatível.
""")
