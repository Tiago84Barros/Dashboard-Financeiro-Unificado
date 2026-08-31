"""
views/analise_portfolio_us.py
Avaliação de Portfólio das Empresas Americanas — etapa 3 de 3.

Espelho de ``views/analise_portfolio_b3.py``: mesmas seções, mesma ordem, mesmo
CSS (importado de lá, não copiado) e mesmo contrato de relatório. O que difere é
o que é intrínseco ao mercado americano:

* a carteira vem de ``us_portfolio_models`` (Criação de Portfólio dos EUA);
* o macro é o do Fed — juros, CPI, PIB real, desemprego, curva e crédito;
* os pares são por indústria SEC e o benchmark é o S&P 500;
* não há RAG documental (sem equivalente de CVM/IPE): a camada de evidência é o
  dossiê determinístico mais o laboratório avançado, calculados em código;
* não há segunda fonte web: o módulo americano é offline-first por contrato.
"""
from __future__ import annotations

import json
from html import escape

import numpy as np
import pandas as pd
import streamlit as st

import core.us_data as us
from core.llm_b3 import (
    chat_com_portfolio,
    llm_disponivel,
    provedores_disponiveis,
    redistribuir_pesos,
)
from core.llm_context_us import build_llm_context_for_us_portfolio_chat
from core.market_companies import (
    translate_us_industry,
    translate_us_sector,
    us_logo_url,
)
from core.portfolio_report_us import (
    GRAU_LABEL,
    MARCA_LABEL,
    analyze_us_portfolio_report,
    generate_company_us_report,
    grau_de_confianca,
    motivo_do_grau,
)
from core.us_macro import (
    FONTE_OBSERVADO,
    FONTE_PREMISSA,
    USMacroSnapshot,
    evaluate_macro,
)
from core.us_portfolio_model import load_active_us_portfolio_model
from design.market_companies import render_company_logo

# CSS compartilhado com a aba B3: o visual das duas telas é o mesmo contrato,
# e duplicar a folha garantiria que uma divergisse da outra na primeira mudança.
from views.analise_portfolio_b3 import (
    _CSS,
    _delta_str,
    _kpi_card,
    _macro_card,
    _persp_badge,
    _score_mod,
)

_STATE = "apus_state"
_CHAT = "apus_chat_history"


# ─────────────────────────────────────────────────────────────────────────────
# Seção 1 — Portfólio salvo
# ─────────────────────────────────────────────────────────────────────────────

def _render_portfolio_salvo(model: dict, pesos_novos: dict[str, float] | None) -> None:
    items = model.get("items", [])
    metrics = model.get("metrics_json") or {}
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except Exception:
            metrics = {}

    entrada = metrics.get("entry_score")
    if entrada is None and items:
        notas = [float(it.get("entry_score") or 0) for it in items]
        entrada = sum(notas) / len(notas) if notas else None
    setores = {str(it.get("setor") or "—") for it in items}
    nome = model.get("name") or "Portfólio EUA Modelo"

    st.markdown(f'<div class="apb3-section-title">📂 {nome}</div>', unsafe_allow_html=True)

    cards = "".join([
        _kpi_card("Empresas", str(len(items)), "na carteira"),
        _kpi_card("Ano-base", str(model.get("ano_compra") or "—"), "ciclo de referência"),
        _kpi_card(
            "Score de entrada",
            f"{float(entrada):.1f}" if entrada is not None else "—",
            "média das selecionadas",
            _score_mod(entrada, 60, 40),
        ),
        _kpi_card("Setores", str(len(setores)), "diversificação por setor"),
    ])
    st.markdown(f'<div class="apb3-kpi-row">{cards}</div>', unsafe_allow_html=True)

    # Colunas fixas — sem <img onerror> cru, achado A-012.
    itens_ordenados = sorted(items, key=lambda x: -float(x.get("weight") or 0))
    _LOGO_GRID_COLS = 6
    for start in range(0, len(itens_ordenados), _LOGO_GRID_COLS):
        cols_logo = st.columns(_LOGO_GRID_COLS, gap="small")
        linha = itens_ordenados[start:start + _LOGO_GRID_COLS]
        for col, it in zip(cols_logo, linha):
            tk = str(it.get("ticker") or it.get("symbol") or "")
            peso_original = float(it.get("weight") or 0)
            peso = (pesos_novos.get(tk, peso_original) if pesos_novos else peso_original)
            with col:
                st.markdown('<div class="apb3-logo-item">', unsafe_allow_html=True)
                render_company_logo(tk, us_logo_url(tk), size=38)
                st.markdown(
                    f'<div class="apb3-logo-ticker">{tk}</div>'
                    f'<div class="apb3-logo-weight">{peso*100:.1f}%</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )


def _avaliacao_quantitativa(model: dict, scored: pd.DataFrame, macro: dict) -> dict:
    """Roda a avaliação determinística sobre a carteira SALVA."""
    items = model.get("items", [])
    if not items or scored is None or scored.empty:
        return {}
    try:
        from core.us_portfolio_analysis import evaluate_portfolio
        holdings = pd.DataFrame([
            {"symbol": str(it.get("ticker") or it.get("symbol") or "").upper(),
             "weight": float(it.get("weight") or 0) * 100.0}
            for it in items
        ])
        return evaluate_portfolio(holdings, scored, macro) or {}
    except Exception:  # noqa: BLE001 - camada determinística não bloqueia a aba
        return {}


def _render_avaliacao_quantitativa(avaliacao: dict) -> None:
    """Números fechados em código, antes de qualquer leitura da LLM.

    A B3 mostra alpha e score gravados na criação; aqui o equivalente é a
    avaliação quantitativa da carteira — concentração, ativos efetivos e
    cobertura —, que já existia no módulo americano e não tinha onde aparecer
    depois que esta aba passou a avaliar a carteira salva.
    """
    if not avaliacao or not avaliacao.get("ok"):
        return
    st.markdown('<div class="apb3-section-title">📐 Avaliação Quantitativa '
                '(determinística)</div>', unsafe_allow_html=True)
    ajuste = avaliacao.get("macro_adjustment", 0)
    cards = "".join([
        _kpi_card("Pontuação consolidada", f"{avaliacao.get('adjusted_score', 0):.1f}/100",
                  f"macro {ajuste:+.1f} · {avaliacao.get('classification', '—')}",
                  _score_mod(avaliacao.get("adjusted_score"), 60, 45)),
        _kpi_card("Diversificação", f"{avaliacao.get('diversification_score', 0):.0f}/100",
                  f"{avaliacao.get('effective_assets', 0):.1f} ativos efetivos",
                  _score_mod(avaliacao.get("diversification_score"), 60, 40)),
        _kpi_card("Maior setor", f"{avaliacao.get('max_sector_weight', 0):.1f}%",
                  "concentração setorial máxima",
                  "neg" if avaliacao.get("max_sector_weight", 0) > 35 else "neu"),
        _kpi_card("Cobertura pontuada", f"{avaliacao.get('coverage_weight', 0):.1f}%",
                  "do peso com score válido",
                  "pos" if avaliacao.get("coverage_weight", 0) >= 90 else "neg"),
    ])
    st.markdown(f'<div class="apb3-kpi-row">{cards}</div>', unsafe_allow_html=True)
    for alerta in avaliacao.get("alerts", []):
        st.warning(alerta, icon="⚠️")
    ausentes = avaliacao.get("missing") or []
    if ausentes:
        st.caption(
            "Sem pontuação no universo: " + ", ".join(map(str, ausentes))
            + " — é lacuna de cobertura do warehouse, não qualidade ruim."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Seção 2 — Cenário macro americano
# ─────────────────────────────────────────────────────────────────────────────

def _render_macro(macro: dict) -> None:
    """Painel do regime macro. Os controles ficam num expander: o padrão é o
    cenário corrente, e mexer nos parâmetros é exceção, não fluxo normal."""
    if not macro:
        st.caption("Cenário macro americano indisponível.")
        return
    entradas = macro.get("inputs") or {}
    observado = bool(macro.get("observado"))
    selo = ('<span class="apb3-tag-pill" style="color:#34D399;">📡 observado'
            + (f' · {macro.get("as_of")}' if macro.get("as_of") else "") + '</span>'
            if observado else
            '<span class="apb3-tag-pill" style="color:#FBBF24;">📐 premissa de simulação</span>')
    st.markdown(
        '<div class="apb3-section-title">🌐 Cenário Macroeconômico — Estados Unidos '
        f'{selo}</div>',
        unsafe_allow_html=True,
    )
    tom = macro.get("tone", "neutro")
    cor_regime = {"favorável": True, "adverso": False}.get(tom)
    cards = "".join([
        _macro_card("Fed funds", f"{entradas.get('fed_funds', 0):.2f}%",
                    "taxa de política monetária", None),
        _macro_card("CPI a/a", f"{entradas.get('cpi_yoy', 0):.2f}%",
                    "inflação ao consumidor", None),
        _macro_card("PIB real a/a", f"{entradas.get('real_gdp_yoy', 0):.2f}%",
                    "atividade", None),
        _macro_card("Desemprego", f"{entradas.get('unemployment', 0):.2f}%",
                    "mercado de trabalho", None),
        _macro_card("Curva 10a-2a", f"{entradas.get('yield_curve_10y_2y', 0):+.2f} p.p.",
                    "inclinação da curva", None),
        _macro_card("Regime", f"{macro.get('score', 0):.0f}/100",
                    str(macro.get("regime", "—")), cor_regime),
    ])
    st.markdown(f'<div class="apb3-macro-row">{cards}</div>', unsafe_allow_html=True)

    impactos = macro.get("sector_impacts") or {}
    if impactos:
        ordenados = sorted(impactos.items(), key=lambda kv: -kv[1])
        chips = "".join(
            f'<span class="apb3-tag-pill">{translate_us_sector(setor)} {valor:+.1f}</span>'
            for setor, valor in ordenados
        )
        st.markdown(
            '<div style="font-size:.72rem;color:#718096;margin:-8px 0 6px;">'
            'Impulso do regime por setor (-10 a +10)</div>' + chips,
            unsafe_allow_html=True,
        )


def _controles_macro() -> dict:
    """Regime macro: prefere série observada; premissa só quando não há dado.

    A distinção não é cosmética. Sem ela a tela exibia "Fed funds 4,25%" sob o
    título "Cenário Macroeconômico" e mandava o mesmo número para a LLM, que
    escrevia "com o Fed em 4,25%" num relatório institucional — afirmação sobre
    o mundo vinda de um literal de código.
    """
    observado = {}
    try:
        observado = us.macro_observado() or {}
    except Exception:  # noqa: BLE001 - leitura opcional
        observado = {}

    padrao = USMacroSnapshot()
    base = {
        "fed_funds": observado.get("fed_funds", padrao.fed_funds),
        "cpi_yoy": observado.get("cpi_yoy", padrao.cpi_yoy),
        "real_gdp_yoy": observado.get("real_gdp_yoy", padrao.real_gdp_yoy),
        "unemployment": observado.get("unemployment", padrao.unemployment),
        "yield_curve_10y_2y": observado.get("yield_curve_10y_2y", padrao.yield_curve_10y_2y),
        "high_yield_spread": observado.get("high_yield_spread", padrao.high_yield_spread),
    }
    tem_observado = bool(observado)

    if tem_observado:
        st.caption(
            f"📡 Regime macro com **séries oficiais (FRED)** ingeridas no "
            f"warehouse · data-base {observado.get('as_of') or 'não informada'}."
        )
    else:
        st.warning(
            "**O cenário macro abaixo é premissa de simulação, não leitura de "
            "mercado.** Nenhuma série do FRED foi ingerida, então os valores são "
            "parâmetros de partida. O relatório é instruído a tratá-los de forma "
            "condicional (“sob a premissa de…”). Para usar dado observado, rode "
            "`python run_us_ingest.py macro --warehouse`.",
            icon="📐",
        )

    with st.expander("⚙️ Ajustar o cenário macro usado na análise", expanded=False):
        st.caption(
            "Alterar qualquer valor transforma a leitura em cenário hipotético: "
            "o relatório passa a responder “e se”, não “como está” — e é marcado "
            "como premissa mesmo que haja série observada."
        )
        c1, c2, c3 = st.columns(3)
        fed = c1.number_input("Fed funds (%)", 0.0, 12.0,
                              float(base["fed_funds"]), 0.25, key="apus_fed")
        cpi = c2.number_input("CPI a/a (%)", -2.0, 15.0,
                              float(base["cpi_yoy"]), 0.1, key="apus_cpi")
        pib = c3.number_input("PIB real a/a (%)", -5.0, 8.0,
                              float(base["real_gdp_yoy"]), 0.1, key="apus_pib")
        c4, c5, c6 = st.columns(3)
        desemp = c4.number_input("Desemprego (%)", 2.0, 15.0,
                                 float(base["unemployment"]), 0.1, key="apus_unemp")
        curva = c5.number_input("Curva 10a-2a (p.p.)", -3.0, 3.0,
                                float(base["yield_curve_10y_2y"]), 0.05, key="apus_curve")
        spread = c6.number_input("Spread high yield (p.p.)", 1.0, 20.0,
                                 float(base["high_yield_spread"]), 0.1, key="apus_hy")

    escolhido = {
        "fed_funds": fed, "cpi_yoy": cpi, "real_gdp_yoy": pib,
        "unemployment": desemp, "yield_curve_10y_2y": curva,
        "high_yield_spread": spread,
    }
    # Mexeu num controle → deixa de ser observação e vira cenário. Manter o
    # rótulo "observado" depois de o usuário alterar o número seria pior que
    # não ter rótulo nenhum.
    intacto = all(abs(escolhido[k] - float(base[k])) < 1e-9 for k in escolhido)
    fonte = FONTE_OBSERVADO if (tem_observado and intacto) else FONTE_PREMISSA
    return evaluate_macro(USMacroSnapshot(
        **escolhido, fonte=fonte,
        as_of=observado.get("as_of") if fonte == FONTE_OBSERVADO else None,
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Seção 3 — Relatório consolidado
# ─────────────────────────────────────────────────────────────────────────────

def _render_relatorio_consolidado(port_analise: dict) -> None:
    if not port_analise:
        return

    qual = port_analise.get("qualidade_carteira", "—")
    persp = port_analise.get("perspectiva_12m", "—")
    conf = port_analise.get("confianca_media", 0)
    score = port_analise.get("score_medio", 0)

    qual_mod = {"alta": "pos", "media": "neu", "baixa": "neg"}.get(qual, "neu")
    persp_mod = {"construtiva": "pos", "equilibrada": "neu", "cautelosa": "neg"}.get(persp, "neu")

    st.markdown('<div class="apb3-section-title">📊 Relatório Consolidado do Portfólio</div>',
                unsafe_allow_html=True)
    cards = "".join([
        _kpi_card("Qualidade", str(qual).upper(), "visão LLM da carteira", qual_mod),
        _kpi_card("Perspectiva 12m", str(persp).upper(), "horizonte de médio prazo", persp_mod),
        _kpi_card("Confiança", f"{conf}", "índice 0–100 da análise"),
        _kpi_card("Score LLM", f"{score}", "nota qualitativa média",
                  "pos" if score >= 60 else ("neg" if score < 40 else "neu")),
    ])
    st.markdown(f'<div class="apb3-kpi-row">{cards}</div>', unsafe_allow_html=True)

    with st.expander("📝 Resumo Executivo + Papel dos Ativos", expanded=True):
        for chave, rotulo in (("resumo_executivo", "Resumo Executivo"),
                              ("papel_dos_ativos", "Papel dos Ativos na Carteira")):
            texto = port_analise.get(chave, "")
            if texto:
                st.markdown(
                    f'<div class="apb3-report-qual"><div class="apb3-report-label">'
                    f'{rotulo}</div>{texto}</div>',
                    unsafe_allow_html=True,
                )

    with st.expander("💪 Pontos Fortes / Fracos", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Forças**")
            for f in port_analise.get("pontos_fortes", []):
                st.markdown(f"✅ {f}")
        with c2:
            st.markdown("**Pontos de atenção**")
            for f in port_analise.get("pontos_fracos", []):
                st.markdown(f"⚠️ {f}")

    with st.expander("🔭 Relatório Estratégico Completo", expanded=False):
        for chave, rotulo in (
            ("relatorio_estrategico", None),
            ("sintese_alocacao", "Leitura da Alocação do Modelo"),
            ("diagnostico_causal", "Diagnóstico causal"),
        ):
            texto = port_analise.get(chave, "")
            if not texto:
                continue
            cabecalho = (f'<div class="apb3-report-label">{rotulo}</div>' if rotulo else "")
            st.markdown(
                f'<div class="apb3-report-qual">{cabecalho}{texto}</div>',
                unsafe_allow_html=True,
            )

        riscos = port_analise.get("riscos_transmissao") or []
        catalisadores = port_analise.get("catalisadores_portfolio") or []
        if riscos or catalisadores:
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Riscos e transmissão**")
                for risco in riscos:
                    if isinstance(risco, dict):
                        expostos = ", ".join(risco.get("ativos_expostos") or [])
                        st.markdown(
                            f"⚠️ **{risco.get('risco', 'Risco')}** — {risco.get('mecanismo', '')}"
                            + (f" ({expostos})" if expostos else "")
                        )
                        if risco.get("monitoramento"):
                            st.caption(f"Monitorar: {risco['monitoramento']}")
            with c2:
                st.markdown("**Catalisadores do conjunto**")
                for cat in catalisadores:
                    if isinstance(cat, dict):
                        expostos = ", ".join(cat.get("ativos_expostos") or [])
                        st.markdown(
                            f"🚀 **{cat.get('catalisador', 'Catalisador')}** — "
                            f"{cat.get('mecanismo', '')}"
                            + (f" ({expostos})" if expostos else "")
                        )

        fit = port_analise.get("adequacao_carteira") or {}
        texto_fit = " · ".join(
            str(fit.get(k) or "") for k in ("perfil", "horizonte", "volatilidade", "condicoes")
            if fit.get(k)
        )
        if texto_fit:
            st.markdown(
                '<div class="apb3-report-qual"><div class="apb3-report-label">'
                f'Adequação da carteira</div>{texto_fit}</div>',
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Seção 4 — Alocação sugerida
# ─────────────────────────────────────────────────────────────────────────────

def _render_alocacao(items_analisados: list[dict], pesos_novos: dict[str, float]) -> None:
    if not items_analisados or not pesos_novos:
        return

    st.markdown('<div class="apb3-section-title">🎯 Alocação do Modelo (Quanti + Quali)</div>',
                unsafe_allow_html=True)
    st.caption(
        "Modelo único: score quantitativo (60%) e qualitativo da LLM (40%), "
        "ajustados por perspectiva e convicção declarada. Piso de 2% e teto de "
        "25% por ativo — nenhuma empresa é excluída aqui; quem decide entrada é "
        "a Criação de Portfólio."
    )

    # Colunas fixas — sem <img onerror> cru, achado A-012.
    itens_alocacao = sorted(
        items_analisados, key=lambda x: -pesos_novos.get(x.get("ticker", ""), 0)
    )
    _ALLOC_GRID_COLS = 4
    for start in range(0, len(itens_alocacao), _ALLOC_GRID_COLS):
        cols_alloc = st.columns(_ALLOC_GRID_COLS, gap="small")
        linha = itens_alocacao[start:start + _ALLOC_GRID_COLS]
        for col, it in zip(cols_alloc, linha):
            tk = it.get("ticker", "")
            nome = (it.get("nome") or tk)[:24]
            persp = (it.get("analise", {}) or {}).get("perspectiva", "moderada")
            w_novo = pesos_novos.get(tk, 0.0)
            w_antigo = float(it.get("peso_pct", 0.0)) / 100.0
            with col:
                st.markdown('<div class="apb3-alloc-card">', unsafe_allow_html=True)
                render_company_logo(tk, us_logo_url(tk), size=36)
                st.markdown(
                    f'<div class="apb3-alloc-ticker">{tk}</div>'
                    f'<div class="apb3-alloc-nome" title="{nome}">{nome}</div>'
                    f'<div class="apb3-alloc-pct">{w_novo*100:.1f}%</div>'
                    f'<div class="apb3-alloc-delta">{_delta_str(w_novo, w_antigo)}</div>'
                    + _persp_badge(persp) +
                    '</div>',
                    unsafe_allow_html=True,
                )


# ─────────────────────────────────────────────────────────────────────────────
# Seção 5 — Relatórios por empresa
# ─────────────────────────────────────────────────────────────────────────────

def _render_empresa_expander(it: dict, pesos_novos: dict[str, float]) -> None:
    tk = it.get("ticker", "")
    an = it.get("analise", {}) or {}
    persp = an.get("perspectiva", "moderada")
    w_novo = pesos_novos.get(tk, float(it.get("peso_pct", 0.0)) / 100.0)
    conclusao = an.get("conclusao") or {}
    faixa = str(conclusao.get("faixa_valor") or "indeterminada").upper()
    icone = {"forte": "🟢", "moderada": "🟡", "fraca": "🔴"}.get(persp, "⚪")
    # O grau vai no TÍTULO do expander: quem só bate o olho na lista precisa
    # saber que aquele veredicto veio de cobertura de triagem antes de abrir.
    grau = str(it.get("score_status") or "")
    motivo = motivo_do_grau(it)
    # O selo do título muda com o MOTIVO, não só com o grau: chamar de
    # "cobertura parcial" uma empresa de dados completos e balanço quebrado
    # anuncia dúvida onde a análise tem veredito.
    #
    # Desde 0.8.0 a marca de balanço não derruba mais o selo, e por isso ela
    # aparece no título INDEPENDENTE do grau -- inclusive em `decision_grade`.
    # Quem bate o olho na lista precisa saber que o P/VP daquela linha não
    # significa nada, e isso continua verdadeiro quando a opinião é firme.
    if motivo["marcas"]:
        selo_grau = " · 🩹 balanço quebrado"
    else:
        selo_grau = {"research_grade": " · ⚠️ cobertura parcial",
                     "screen_grade": " · ⛔ só triagem"}.get(grau, "")

    with st.expander(
        f"{icone} {tk}  —  {faixa}  •  {w_novo*100:.1f}%  [{persp.upper()}]{selo_grau}",
        expanded=False,
    ):
        if motivo["marcas"]:
            legiveis = ", ".join(MARCA_LABEL.get(m, m) for m in motivo["marcas"])
            st.warning(
                f"**Balanço estruturalmente quebrado:** {legiveis}. O dado está "
                "completo — isto é veredito sobre a empresa, não lacuna. "
                "Múltiplo sobre base negativa não significa nada aqui.",
                icon="🩹")
        if grau and grau != "decision_grade":
            st.warning(
                f"**Cobertura de dados: {GRAU_LABEL.get(grau, grau)}.** "
                + motivo["texto"], icon="⚠️")

        resumo = an.get("resumo", "")
        if resumo:
            st.markdown(
                f'<div class="apb3-report-qual"><div class="apb3-report-label">'
                f'Síntese do Parecer</div>{resumo}</div>',
                unsafe_allow_html=True,
            )

        # KPIs determinísticos do dossiê — calculados em código, não pela LLM.
        dossie = it.get("dossie") or {}
        metricas = dossie.get("metrics") or {}
        if metricas:
            def _pct(chave):
                v = metricas.get(chave)
                return f"{v*100:.1f}%" if isinstance(v, (int, float)) else "—"

            def _mult(chave):
                v = metricas.get(chave)
                return f"{v:.2f}x" if isinstance(v, (int, float)) else "—"

            cards = "".join([
                _kpi_card("P/L · EV/EBIT", f"{_mult('pe')} · {_mult('ev_ebit')}",
                          "múltiplos calculados", "neu"),
                _kpi_card("Retorno do FCL", _pct("fcf_yield"),
                          "fluxo de caixa livre sobre valor", "neu"),
                _kpi_card("ROIC · ROE", f"{_pct('roic')} · {_pct('roe')}",
                          "retorno sobre capital", "neu"),
                _kpi_card("Dív. líq/EBITDA", _mult("net_debt_ebitda"),
                          str(dossie.get("classification") or "—"), "neu"),
            ])
            st.markdown(f'<div class="apb3-kpi-row">{cards}</div>', unsafe_allow_html=True)

        rel = an.get("relatorio") or {}
        for chave, rotulo in (
            ("empresa_hoje", "O que a empresa é hoje"),
            ("analise_pares", "Análise por pares da mesma indústria"),
            ("valuation_interpretado", "Valuation interpretado"),
            ("tendencias", "Tendências operacionais e financeiras"),
            ("qualidade_resultados", "Qualidade dos Resultados"),
            ("governanca_controlador", "Governança e alocação de capital"),
            ("eventos_relevantes", "Eventos relevantes e percepção de mercado"),
            ("qualidade_dados", "Qualidade dos dados"),
        ):
            texto = rel.get(chave)
            if texto:
                st.markdown(
                    f'<div class="apb3-report-qual"><div class="apb3-report-label">'
                    f'{rotulo}</div>{texto}</div>',
                    unsafe_allow_html=True,
                )

        flags = dossie.get("red_flags") or []
        if flags:
            st.markdown("**Sinais de alerta determinísticos (verificados em código)**")
            for f in flags:
                st.markdown(f"🚩 {f}")

        c1, c2 = st.columns(2)
        with c1:
            riscos = an.get("riscos", [])
            if riscos:
                st.markdown("**Riscos**")
                for r in riscos:
                    if isinstance(r, dict):
                        st.markdown(f"⚠️ **{r.get('risco', 'Risco')}** — {r.get('mecanismo', '')}")
                        if r.get("indicador_monitorado"):
                            st.caption(f"Monitorar: {r['indicador_monitorado']}")
                    else:
                        st.markdown(f"⚠️ {r}")
            macro_s = an.get("sensibilidade_macro", [])
            if macro_s:
                st.markdown("**Sensibilidade macro**")
                st.markdown(
                    "".join(f'<span class="apb3-tag-pill">{m}</span>' for m in macro_s),
                    unsafe_allow_html=True,
                )
        with c2:
            cats = an.get("catalisadores", [])
            if cats:
                st.markdown("**Catalisadores**")
                for c in cats:
                    if isinstance(c, dict):
                        st.markdown(
                            f"🚀 **{c.get('catalisador', 'Catalisador')}** — "
                            f"{c.get('mecanismo', '')}"
                        )
                        if c.get("janela_ou_gatilho"):
                            st.caption(f"Janela/gatilho: {c['janela_ou_gatilho']}")
                    else:
                        st.markdown(f"🚀 {c}")
            if an.get("proxima_acao"):
                st.markdown(f"**Monitoramento:** {an['proxima_acao']}")

        cenarios = an.get("cenarios") or []
        if cenarios:
            st.markdown("**Análise probabilística**")
            st.dataframe(
                pd.DataFrame([{
                    "Cenário": c.get("cenario", "—"),
                    "Probabilidade": f"{float(c.get('probabilidade_pct') or 0):.1f}%",
                    "Impacto esperado": c.get("impacto_esperado", ""),
                    "Fundamentação": c.get("fundamentacao", ""),
                } for c in cenarios if isinstance(c, dict)]),
                width="stretch", hide_index=True,
            )

        detalhe = an.get("score_qualitativo_detalhado") or {}
        if detalhe:
            st.markdown("**Score Qualitativo — composição ponderada**")
            st.dataframe(
                pd.DataFrame([{
                    "Critério": d.get("label", "—"),
                    "Nota (0–10)": d.get("nota", "—"),
                    "Peso": f"{d.get('peso_pct', 0)}%",
                    "Justificativa": d.get("justificativa", ""),
                    "Evidência ou lacuna": d.get("evidencia_ou_lacuna", ""),
                } for d in detalhe.values() if isinstance(d, dict)]),
                width="stretch", hide_index=True,
            )

        fit = an.get("adequacao_investidor") or {}
        texto_fit = " · ".join(
            str(fit.get(k) or "") for k in
            ("perfil", "horizonte", "tolerancia_volatilidade", "condicoes") if fit.get(k)
        )
        if texto_fit:
            st.markdown(
                '<div class="apb3-report-qual"><div class="apb3-report-label">'
                f'Adequação ao perfil do investidor</div>{texto_fit}</div>',
                unsafe_allow_html=True,
            )

        if an.get("alerta_principal"):
            st.warning(f"⚡ {an['alerta_principal']}")

        linhas_conclusao = [
            f"**{rotulo}:** {conclusao[chave]}"
            for rotulo, chave in (
                ("Faixa de valor", "faixa_valor"),
                ("O desconto é justificável?", "desconto_justificavel"),
                ("Percepção implícita", "percepcao_mercado"),
                ("Risco-retorno", "risco_retorno"),
                ("Principal positivo", "principal_positivo"),
                ("Principal risco", "principal_risco"),
            ) if conclusao.get(chave)
        ]
        tese = conclusao.get("resumo_executivo") or an.get("tese_final", "")
        if linhas_conclusao or tese:
            corpo = "<br>".join(linhas_conclusao + ([f"<br>{tese}"] if tese else []))
            st.markdown(
                f'<div class="apb3-report-qual"><div class="apb3-report-label">'
                f'Conclusão objetiva</div>{corpo}</div>',
                unsafe_allow_html=True,
            )

        sc, cf = an.get("score_qualitativo"), an.get("confianca")
        ponderado = an.get("score_qualitativo_ponderado")
        avancado = it.get("avancado") or {}
        f_score = avancado.get("f_score")
        cards = "".join([
            _kpi_card("Score qualitativo", f"{sc}/100" if sc is not None else "—",
                      f"ponderado {ponderado}/10" if ponderado is not None else "nota ponderada",
                      _score_mod(sc, 60, 40)),
            _kpi_card("Confiança", f"{cf}/100" if cf is not None else "—",
                      "convicção da análise", _score_mod(cf, 70, 50)),
            _kpi_card("Valuation", str(conclusao.get("faixa_valor") or "indeterminada").upper(),
                      str(conclusao.get("percepcao_mercado") or "leitura não disponível")),
            _kpi_card("Piotroski F", f"{f_score:.0f}/9" if f_score is not None else "—",
                      "qualidade contábil calculada",
                      "pos" if (f_score or 0) >= 7 else ("neg" if (f_score or 9) <= 3 else "neu")),
        ])
        st.markdown(f'<div class="apb3-kpi-row">{cards}</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Seção 6 — Conclusão estratégica
# ─────────────────────────────────────────────────────────────────────────────

def _render_conclusao(port_analise: dict) -> None:
    conclusao = port_analise.get("conclusao_estrategica", "")
    if not conclusao:
        return
    st.markdown('<div class="apb3-section-title">🏁 Conclusão Estratégica</div>',
                unsafe_allow_html=True)
    st.markdown(
        f'<div class="apb3-report-qual" style="border-color:rgba(0,200,150,.25);">'
        f'{conclusao}</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Runner da análise
# ─────────────────────────────────────────────────────────────────────────────

def _linha_do_score(scored: pd.DataFrame, ticker: str):
    """Linha do cross-section para um ticker; None quando não pontuou."""
    if scored is None or scored.empty or "symbol" not in scored.columns:
        return None
    linha = scored[scored["symbol"].astype(str).str.upper() == ticker.upper()]
    return None if linha.empty else linha.iloc[0]


@st.cache_data(ttl=1800, show_spinner=False)
def _usd_brl_da_base() -> float | None:
    """Última cotação USD/BRL do banco unificado (asset_quotes).

    Número real, não estimativa: a exposição cambial só é dizível se a taxa
    tiver origem. Sem a cotação, o relatório declara a ausência.
    """
    try:
        from sqlalchemy import text

        from core.database import get_engine
        engine = get_engine()
        if engine is None:
            return None
        with engine.connect() as conn:
            valor = conn.execute(text("""
                SELECT q.close
                FROM asset_quotes q
                JOIN assets a ON a.id = q.asset_id
                WHERE UPPER(a.ticker) IN ('USDBRL', 'USDBRL=X', 'USD/BRL')
                ORDER BY q.date DESC
                LIMIT 1
            """)).scalar()
        return float(valor) if valor is not None else None
    except Exception:  # noqa: BLE001 - câmbio é contexto, não bloqueio
        return None


def _executar_analise(items: list[dict], macro: dict, scored: pd.DataFrame,
                      avaliacao_quant: dict | None = None,
                      status: dict | None = None) -> dict:
    """Relatório por empresa + consolidado + redistribuição de pesos."""
    contexto_carteira = (
        f"Portfólio americano com {len(items)} empresas. "
        f"Score de entrada médio: "
        f"{float(np.mean([it.get('entry_score') or 0 for it in items])):.1f}. "
        f"Benchmark de referência: S&P 500. Custo de oportunidade: Treasury."
    )

    items_analisados: list[dict] = []
    erros: list[str] = []
    financials: dict[str, pd.DataFrame] = {}
    progresso = st.progress(0, text="Analisando empresas via LLM…")

    for idx, it in enumerate(items):
        tk = str(it.get("ticker") or it.get("symbol") or "").upper()
        nome = it.get("nome") or tk
        setor = it.get("setor") or "N/D"
        industria = it.get("industria") or "N/D"
        peso_pct = float(it.get("weight") or 0) * 100.0
        entrada = float(it.get("entry_score") or 0)

        progresso.progress((idx + 1) / len(items), text=f"Carregando dados de {tk}…")
        try:
            df_fin = us.company_financials(tk)
        except Exception:
            df_fin = None
        try:
            avancado = us.advanced_snapshot(tk) or {}
        except Exception:
            avancado = {}
        linha_score = _linha_do_score(scored, tk)
        if df_fin is not None and not getattr(df_fin, "empty", True):
            financials[tk] = df_fin

        progresso.progress((idx + 1) / len(items), text=f"LLM: {tk}…")
        dossie: dict = {}
        try:
            contexto_empresa = (
                f"{contexto_carteira} Empresa avaliada: {nome} | setor {setor} / "
                f"indústria {industria}. Peso atual na carteira: {peso_pct:.1f}% | "
                f"score de entrada {entrada:.1f}."
            )
            analise, dossie = generate_company_us_report(
                tk,
                df_fin=df_fin,
                advanced=avancado,
                macro=macro,
                scored=scored,
                portfolio_tickers=[str(i.get("ticker") or "") for i in items],
                portfolio_context=contexto_empresa,
                status=status,
            )
        except Exception as exc:  # noqa: BLE001 - fronteira de isolamento por empresa
            st.warning(f"{tk}: erro LLM — {exc}")
            erros.append(f"{tk}: {exc}")
            from core.portfolio_report_common import fallback_company
            analise = fallback_company(tk, str(exc)[:200])
        else:
            if int(analise.get("confianca") or 0) == 0:
                erros.append(
                    f"{tk}: relatório institucional não gerado — exibindo fallback neutro."
                )

        grau, _rotulo, confianca_score = grau_de_confianca(linha_score)
        faltando, marcas, mudas = [], [], []
        if linha_score is not None:
            bruto = linha_score.get("critical_missing") if hasattr(linha_score, "get") else None
            faltando = list(bruto) if bruto is not None else []
            bruto_m = linha_score.get("impairment_flags") if hasattr(linha_score, "get") else None
            marcas = list(bruto_m) if bruto_m is not None else []
            # Vitrine antiga não tem a coluna; ausência vira lista vazia e o
            # item continua sendo montado -- drift de schema já esvaziou esta
            # tela uma vez, e não pode voltar a esvaziar por uma coluna nova.
            bruto_u = linha_score.get("unanswerable_tracks") if hasattr(linha_score, "get") else None
            mudas = list(bruto_u) if bruto_u is not None else []
        items_analisados.append({
            "ticker": tk,
            "nome": nome,
            "setor": setor,
            "industria": industria,
            "peso_pct": peso_pct,
            "score": entrada,
            "analise": analise,
            "dossie": dossie,
            "avancado": avancado,
            # Grau de cobertura viaja com o item: o expander sinaliza e o
            # relatório consolidado sabe qual fatia da carteira não sustenta
            # leitura fundamentalista.
            "score_status": grau,
            "score_confidence": confianca_score,
            "critical_missing": faltando,
            "impairment_flags": marcas,
            "unanswerable_tracks": mudas,
        })
        progresso.progress((idx + 1) / len(items), text=f"Analisado: {tk}")

    progresso.empty()

    with st.spinner("Gerando relatório consolidado do portfólio…"):
        try:
            port_analise = analyze_us_portfolio_report(
                items_analisados, macro,
                avaliacao_quant=avaliacao_quant,
                status=status,
                financials=financials,
                usd_brl=_usd_brl_da_base(),
            )
            if int(port_analise.get("confianca_media") or 0) == 0:
                erros.append("Relatório consolidado: resposta da LLM não pôde ser "
                             "interpretada (JSON inválido).")
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Análise de portfólio falhou: {exc}")
            erros.append(f"Relatório consolidado: {exc}")
            from core.portfolio_report_common import fallback_portfolio
            port_analise = fallback_portfolio(str(exc)[:200])

    return {
        "items_analisados": items_analisados,
        "port_analise": port_analise,
        "pesos_novos": redistribuir_pesos(items_analisados),
        "erros": erros,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Chat contextual
# ─────────────────────────────────────────────────────────────────────────────

def _pesos_do_modelo(model: dict) -> dict[str, float]:
    bruto = {
        str(it.get("ticker") or it.get("symbol") or "").upper(): float(it.get("weight") or 0)
        for it in model.get("items", [])
    }
    total = sum(bruto.values()) or 1.0
    return {k: v / total for k, v in bruto.items()}


def _contexto_base_chat(model: dict, state: dict, macro: dict,
                        pesos: dict[str, float]) -> str:
    """Contexto real da carteira americana para o chat."""
    items = model.get("items", [])
    por_peso = sorted(items, key=lambda it: -float(it.get("weight") or 0))
    linhas = ["PORTFÓLIO AMERICANO ATUALMENTE AVALIADO:"]
    linhas.append(f"  Nome: {model.get('name') or 'Carteira modelo EUA'}"
                  + (f" | Ano de compra: {model['ano_compra']}" if model.get("ano_compra") else ""))
    linhas.append(f"  Nº de empresas: {len(items)}")

    setores: dict[str, float] = {}
    industrias: dict[str, float] = {}
    for it in items:
        tk = str(it.get("ticker") or "").upper()
        peso = pesos.get(tk, 0.0)
        setores[str(it.get("setor") or "—")] = setores.get(str(it.get("setor") or "—"), 0.0) + peso
        rot = str(it.get("industria") or "—")
        industrias[rot] = industrias.get(rot, 0.0) + peso
    linhas.append("  Composição por setor: " + ", ".join(
        f"{s}={w*100:.0f}%" for s, w in sorted(setores.items(), key=lambda x: -x[1])))
    linhas.append("  Composição por indústria: " + ", ".join(
        f"{s}={w*100:.0f}%" for s, w in sorted(industrias.items(), key=lambda x: -x[1])[:10]))

    linhas.append("\nEMPRESAS DA CARTEIRA (dados gravados na Criação de Portfólio):")
    for it in por_peso:
        tk = str(it.get("ticker") or "").upper()
        meta = [f"peso={pesos.get(tk, 0.0)*100:.1f}%"]
        if it.get("industria"):
            meta.append(f"indústria={it['industria']}")
        if it.get("entry_score") is not None:
            meta.append(f"score_entrada={float(it['entry_score']):.1f}")
        if it.get("coverage") is not None:
            meta.append(f"cobertura={float(it['coverage']):.0f}%")
        linhas.append(f"  {tk} ({it.get('nome', '')}) | " + " | ".join(meta))

    if state:
        port_an = state.get("port_analise", {})
        items_an = state.get("items_analisados", [])
        pesos_novos = state.get("pesos_novos", {})
        linhas.append("\nANÁLISE QUALITATIVA (LLM) JÁ EXECUTADA:")
        linhas.append(
            f"  Qualidade: {port_an.get('qualidade_carteira','N/D')} | "
            f"Perspectiva 12m: {port_an.get('perspectiva_12m','N/D')} | "
            f"Score médio: {port_an.get('score_medio','N/D')}"
        )
        if port_an.get("resumo_executivo"):
            linhas.append(f"  Resumo: {port_an['resumo_executivo'][:400]}")
        for it in sorted(items_an, key=lambda x: -pesos_novos.get(x.get("ticker", ""), 0))[:30]:
            an = it.get("analise", {})
            extras = []
            if an.get("perspectiva"):
                extras.append(f"perspectiva={an['perspectiva']}")
            if an.get("resumo"):
                extras.append(f"tese={an['resumo'][:160]}")
            if extras:
                linhas.append(f"  {it.get('ticker', '')}: " + " | ".join(extras))
    else:
        linhas.append(
            "\nANÁLISE QUALITATIVA (LLM): ainda não executada nesta sessão. Os dados "
            "quantitativos acima já permitem responder a maioria das perguntas."
        )
    return "\n".join(linhas)


def _render_chat(model: dict, state: dict, macro: dict) -> None:
    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
    st.markdown('<div class="apb3-section-title">💬 Tire Dúvidas sobre o Portfólio</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<p style="font-size:0.78rem;color:#9CA3AF;margin-bottom:16px;">'
        'Pergunte sobre múltiplos (P/L, EV/EBIT, retorno do FCL), medianas por '
        'indústria, comparações com empresas <strong>fora</strong> da carteira, '
        'concentração setorial, sensibilidade ao Fed ou a lógica da seleção. A IA '
        'consulta a carteira e o universo americano do warehouse local.</p>',
        unsafe_allow_html=True,
    )

    _, col_limpar = st.columns([5, 1])
    with col_limpar:
        if st.button("🗑️ Limpar chat", key="apus_chat_clear", width="stretch"):
            st.session_state.pop(_CHAT, None)
            st.rerun()

    historico: list[dict] = st.session_state.get(_CHAT, [])
    for msg in historico:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    pergunta = st.chat_input("Pergunte sobre o portfólio…", key="apus_chat_input")
    if not pergunta:
        return

    historico.append({"role": "user", "content": pergunta})
    with st.chat_message("user"):
        st.markdown(pergunta)

    with st.chat_message("assistant"):
        with st.spinner("Consultando carteira, universo americano e indústrias…"):
            try:
                pesos = _pesos_do_modelo(model)
                contexto, _meta = build_llm_context_for_us_portfolio_chat(
                    user_question=pergunta,
                    base_context=_contexto_base_chat(model, state, macro, pesos),
                    model=model,
                    weights=pesos,
                    macro=macro,
                    portfolio_tickers=[str(it.get("ticker") or "")
                                       for it in model.get("items", [])],
                )
                resposta = chat_com_portfolio(contexto, historico[:-1], pergunta)
            except Exception as exc:  # noqa: BLE001
                resposta = f"Erro ao consultar LLM: {exc}"
        st.markdown(resposta)

    historico.append({"role": "assistant", "content": resposta})
    st.session_state[_CHAT] = historico


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def render(show_header: bool = True) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    if show_header:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">'
            '<span style="font-size:2rem">🧠</span>'
            '<h1 style="font-size:2rem;font-weight:800;color:#E2E8F0;margin:0;">'
            'Avaliação de Portfólio — Estados Unidos</h1>'
            '</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<p style="font-size:0.80rem;color:#9CA3AF;margin-bottom:20px;">'
        '<strong style="color:#CBD5E1;">Etapa 3 de 3 · Avaliação do conjunto.</strong> '
        'Julga a carteira criada na aba <strong>Criação de Portfólio</strong> como um '
        'todo — diversificação, concentração por ativo, indústria e setor, exposição a '
        'risco, geração de caixa e crescimento consolidados, qualidade contábil e '
        'adequação ao objetivo. Combina demonstrações SEC/US GAAP, o regime '
        'macroeconômico americano e interpretação LLM. '
        'Responde: <em>esse conjunto forma uma carteira coerente, diversificada e defensável?</em>'
        '</p>',
        unsafe_allow_html=True,
    )

    with st.spinner("Carregando portfólio modelo…"):
        try:
            model = load_active_us_portfolio_model()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Não foi possível carregar a carteira salva: {exc}")
            return

    if not model or not model.get("items"):
        st.info(
            "Nenhum portfólio modelo salvo. Crie uma carteira na aba "
            "**🚀 Criação de Portfólio** e clique em **⭐ Salvar como carteira padrão**.",
            icon="ℹ️",
        )
        return
    if model.get("is_stale"):
        st.error(
            "O portfólio salvo usa uma versão antiga da metodologia. Recalcule "
            "e salve uma nova carteira na aba Criação de Portfólio antes da análise."
        )
        return

    items = model["items"]

    # Enriquece os itens salvos com a classificação corrente do warehouse: o
    # modelo grava setor e indústria do momento do salvamento, e a leitura de
    # concentração precisa do rótulo traduzido.
    for it in items:
        it["setor"] = translate_us_sector(it.get("setor"), it.get("industria"))
        it["industria"] = translate_us_industry(it.get("industria") or it.get("setor"))

    with st.spinner("Carregando universo americano…"):
        try:
            scored = us.scored_universe()
        except Exception:
            scored = pd.DataFrame()

    state = st.session_state.get(_STATE, {})
    _render_portfolio_salvo(model, state.get("pesos_novos") if state else None)

    # Procedência visível antes de qualquer número: uma nota profissional
    # começa dizendo de quando é o dado que sustenta a leitura.
    status = {}
    try:
        status = us.data_status() or {}
    except Exception:  # noqa: BLE001
        status = {}
    modo_txt = {"warehouse": "warehouse local completo",
                "snapshot": "vitrine publicada"}.get(str(status.get("mode") or ""), "origem não identificada")
    ultima = status.get("last_update")
    usd_brl = _usd_brl_da_base()
    st.markdown(
        '<div style="font-size:.76rem;color:#718096;margin:-8px 0 12px;line-height:1.7;">'
        f'🗂️ <b>Base:</b> {escape(modo_txt)}'
        + (f' · última ingestão {escape(str(ultima)[:19])}' if ultima else
           ' · data de ingestão não informada')
        + (f' · universo {status["companies"]} empresas' if status.get("companies") else "")
        + '<br>📄 Sem base documental indexada para o mercado americano — a evidência '
          'vem do dossiê determinístico e do laboratório avançado (Piotroski, '
          'Altman, Sloan), calculados em código sobre as demonstrações SEC.'
        + '<br>💱 <b>Carteira em dólares.</b> Seu retorno em reais é o retorno do '
          'ativo combinado com a variação do câmbio — o USD/BRL é um segundo '
          'ativo embutido nesta carteira'
        + (f' (referência na base: R$ {usd_brl:.2f}).' if usd_brl else
           ' (cotação USD/BRL não disponível na base).')
        + '</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
    # O macro vem antes da avaliação quantitativa porque ela o usa no ajuste —
    # mudar o cenário nos controles tem de mover a pontuação consolidada.
    macro = _controles_macro()
    _render_macro(macro)

    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
    avaliacao_quant = _avaliacao_quantitativa(model, scored, macro)
    _render_avaliacao_quantitativa(avaliacao_quant)
    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)

    st.markdown('<div class="apb3-section-title">🤖 Análise Qualitativa via LLM</div>',
                unsafe_allow_html=True)

    if not llm_disponivel():
        st.warning(
            "Nenhum provedor LLM configurado. Adicione `OPENAI_API_KEY` e/ou "
            "`GEMINI_API_KEY` no `.env` ou nos Streamlit Secrets para ativar a análise LLM.",
            icon="⚠️",
        )
        return

    provedores = provedores_disponiveis()
    if provedores:
        rotulos = {"openai": "OpenAI", "gemini": "Gemini"}
        st.caption(
            "🤖 Provedores LLM ativos (com fallback automático): **"
            + " → ".join(rotulos.get(p, p) for p in provedores) + "**"
        )
    st.caption(
        "A análise usa **somente o warehouse local** (demonstrações SEC, preços e "
        "score). O módulo americano é offline-first: nenhuma fonte externa é "
        "consultada durante a avaliação."
    )

    col_rodar, col_limpar, _ = st.columns([2, 1, 2])
    with col_rodar:
        rodar = st.button("🚀 Executar Análise LLM", type="primary",
                          width="stretch", key="apus_rodar")
    with col_limpar:
        if st.button("🗑️ Limpar", width="stretch", key="apus_reset"):
            st.session_state.pop(_STATE, None)
            st.rerun()

    if rodar:
        st.session_state[_STATE] = _executar_analise(
            items, macro, scored,
            avaliacao_quant=avaliacao_quant, status=status,
        )
        st.rerun()

    if state:
        erros = state.get("erros", [])
        if erros:
            with st.expander(
                f"⚠️ {len(erros)} falha(s) na análise LLM — relatório pode estar incompleto",
                expanded=True,
            ):
                for erro in erros:
                    st.markdown(f"- {erro}")
                st.caption(
                    "Causas comuns: cota/limite atingido em **todos** os provedores, "
                    "chave sem acesso ao modelo, timeout, ou empresa sem demonstrações "
                    "no warehouse local. Verifique e clique novamente em "
                    "**Executar Análise LLM**."
                )

        st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
        _render_relatorio_consolidado(state["port_analise"])

        st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
        _render_alocacao(state["items_analisados"], state["pesos_novos"])

        st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
        st.markdown('<div class="apb3-section-title">🏢 Relatórios por Empresa</div>',
                    unsafe_allow_html=True)
        for it in sorted(state["items_analisados"],
                         key=lambda x: -state["pesos_novos"].get(x.get("ticker", ""), 0)):
            _render_empresa_expander(it, state["pesos_novos"])

        st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
        _render_conclusao(state["port_analise"])
    else:
        st.info(
            "Clique **🚀 Executar Análise LLM** para o relatório qualitativo "
            "completo. O chat abaixo já responde com os dados quantitativos reais "
            "da carteira e do universo americano.",
            icon="ℹ️",
        )

    _render_chat(model, state, macro)
