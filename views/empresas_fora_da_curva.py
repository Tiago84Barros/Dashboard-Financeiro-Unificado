"""
views/empresas_fora_da_curva.py
Seção PRÓPRIA: Empresas Fora da Curva (retorno assimétrico).

Deliberadamente SEPARADA de "Empresas Americanas": os propósitos são distintos e
misturá-los confunde a leitura.

  • Empresas Americanas → análise fundamentalista e carteira-modelo. Avalia o que
    a empresa JÁ entrega. Erro esperado baixo, posições normais.
  • Esta seção         → hipótese de retorno assimétrico. Aceita MAIOR incerteza e
    MAIOR taxa de erro; grandes vencedoras são raras e poucas posições podem
    responder por grande parte do retorno. Subcarteira pequena.

NÃO é recomendação e NÃO substitui a carteira fundamentalista. Offline-first: lê
só o warehouse local (core.us_data), nunca a FMP.
"""
from __future__ import annotations

import streamlit as st

import core.us_data as us
from core.us_methodology import US_ASYMMETRY_SCORE_VERSION
from design.componentes import (
    badge_status,
    card_metrica,
    container_pagina,
    em_construcao,
    estado_vazio,
    secao_titulo,
)

_STAGE_LABEL = {"early": "Estágio inicial", "scaling": "Escalando",
                "growth": "Crescimento", "mature": "Madura"}
_RISK_TIPO = {"média": "info", "alta": "alerta", "muito alta": "erro"}


def render() -> None:
    container_pagina(
        "Empresas Fora da Curva",
        "Trilha experimental de retorno assimétrico — separada da carteira fundamentalista",
        "🚀",
    )

    st.warning(
        "**Seção experimental.** Aceita maior incerteza e maior taxa de erro do que "
        "a seção Empresas Americanas. O que aparece aqui são **hipóteses** com "
        "sinais, riscos e condições de invalidação — **não é recomendação** nem "
        "garantia de retorno. Use como subcarteira pequena.",
        icon="⚠️")

    status = us.data_status()
    col1, col2, *_ = st.columns([1.2, 1.4, 4])
    with col1:
        if not status.get("schema_ready"):
            badge_status("Schema ausente", "erro")
        elif status.get("offline"):
            badge_status("Sem dados locais", "alerta")
        else:
            badge_status("Dados locais", "sucesso")
    with col2:
        badge_status(f"Score assimetria v{US_ASYMMETRY_SCORE_VERSION}", "info")

    st.markdown("<br>", unsafe_allow_html=True)

    abas = st.tabs(["Candidatas", "Dossiê da Tese", "Backtest de Assimetria",
                    "Metodologia"])
    with abas[0]:
        _tab_candidatas(status)
    with abas[1]:
        _tab_tese(status)
    with abas[2]:
        em_construcao(
            "Backtest retrospectivo",
            "Motor pronto em core/us_outlier_backtest.py (rótulos de multi-bagger, "
            "precisão/recall, lift sobre a taxa-base, contribuição das maiores "
            "vencedoras, cesta com posições indo a zero). A aba é ligada quando "
            "houver histórico de preços mensais no warehouse local.")
    with abas[3]:
        _tab_metodologia()


def _universo(status: dict):
    if status.get("offline"):
        estado_vazio("Sem dados locais para avaliar assimetria. Rode a carga na "
                     "seção Empresas Americanas → Sincronização.", "🚀")
        return None
    df = us.asymmetry_universe()
    if df is None or df.empty:
        estado_vazio("Sem empresas com histórico suficiente (≥ 3 anos).", "🚀")
        return None
    return df


# ── Candidatas ────────────────────────────────────────────────────────────────
def _tab_candidatas(status: dict) -> None:
    df = _universo(status)
    if df is None:
        return
    secao_titulo("Ranking de assimetria", "🚀")
    st.caption("Ordenado pelo score de assimetria. Confiança reflete a cobertura "
               "de dados — score alto com confiança baixa é hipótese frágil.")

    c1, c2 = st.columns(2)
    with c1:
        estagios = ["(todos)"] + [_STAGE_LABEL.get(s, s)
                                  for s in df["stage"].dropna().unique()]
        sel_est = st.selectbox("Estágio", estagios, key="fc_stage")
    with c2:
        min_conf = st.slider("Confiança mínima %", 0, 100, 0, key="fc_minconf")

    view = df[df["confidence"] >= min_conf]
    if sel_est != "(todos)":
        inv = {v: k for k, v in _STAGE_LABEL.items()}
        view = view[view["stage"] == inv.get(sel_est, sel_est)]
    if view.empty:
        estado_vazio("Nenhuma candidata com os filtros atuais.", "🔎")
        return

    tbl = view[["symbol", "name", "sector", "asymmetry_score", "confidence",
                "stage", "risk_class", "suggested_position_pct"]].head(50).copy()
    tbl["stage"] = tbl["stage"].map(_STAGE_LABEL).fillna(tbl["stage"])
    st.dataframe(tbl.rename(columns={
        "symbol": "Ticker", "name": "Nome", "sector": "Setor",
        "asymmetry_score": "Assimetria", "confidence": "Confiança %",
        "stage": "Estágio", "risk_class": "Risco",
        "suggested_position_pct": "Posição sug. %"}),
        hide_index=True, use_container_width=True)


# ── Dossiê da tese ────────────────────────────────────────────────────────────
def _tab_tese(status: dict) -> None:
    df = _universo(status)
    if df is None:
        return
    sym = st.selectbox("Ticker", df["symbol"].tolist(), key="fc_symbol")
    row = df[df["symbol"] == sym].iloc[0]

    secao_titulo(f"{sym} — {row.get('name') or ''}", "🔎", row.get("sector") or "—")
    cb1, cb2, cb3, *_ = st.columns([1, 1, 1, 3])
    with cb1:
        badge_status(f"Assimetria {row['asymmetry_score']}", "info")
    with cb2:
        badge_status(_STAGE_LABEL.get(row["stage"], row["stage"]), "neutro")
    with cb3:
        badge_status(f"Risco {row['risk_class']}",
                     _RISK_TIPO.get(row["risk_class"], "neutro"))

    c1, c2, c3 = st.columns(3)
    with c1:
        card_metrica("Confiança", f"{row['confidence']:.0f}%",
                     ajuda="Cobertura dos dados usados no score")
    with c2:
        card_metrica("Posição sugerida", f"{row['suggested_position_pct']:.2f}%",
                     ajuda="Subcarteira pequena — assimetria é rara e arriscada")
    with c3:
        card_metrica("Horizonte", row.get("horizon", "—"))

    colp, colr = st.columns(2)
    with colp:
        st.markdown("**Sinais positivos**")
        sinais = row.get("positive_signals") or []
        for s in sinais:
            st.markdown(f"- ✅ {s}")
        if not sinais:
            st.caption("Nenhum sinal positivo relevante.")
    with colr:
        st.markdown("**Riscos**")
        riscos = row.get("risks") or []
        for s in riscos:
            st.markdown(f"- ⚠️ {s}")
        if not riscos:
            st.caption("Nenhum sinal negativo relevante.")

    st.markdown("**Hipóteses necessárias** (precisam ser verdadeiras para a tese valer)")
    for h in (row.get("hypotheses") or []):
        st.markdown(f"- {h}")
    st.markdown("**Condições de invalidação** (o que faz abandonar a tese)")
    for i in (row.get("invalidation") or []):
        st.markdown(f"- {i}")
    if row.get("missing_data"):
        st.caption("Dados faltantes: " + ", ".join(row["missing_data"]))


# ── Metodologia ───────────────────────────────────────────────────────────────
def _tab_metodologia() -> None:
    secao_titulo("Metodologia — Fora da Curva", "📚")
    st.markdown(f"""
**Por que é uma seção separada.** O propósito é diferente do de *Empresas
Americanas*. Lá se avalia o que a empresa **já entrega** (qualidade, crescimento,
solidez, eficiência de capital, valuation, retorno ao acionista), com erro
esperado baixo e posições normais. Aqui se procura **retorno assimétrico**:
poucas vencedoras raras, tolerância explícita a errar mais, e posição pequena por
construção. Misturar as duas leituras confundiria a decisão — por isso os menus
são distintos.

**Como o score é calculado** (v{US_ASYMMETRY_SCORE_VERSION}, determinístico em
`core/us_asymmetry.py`). Combina **nível e trajetória**:

- *Sinais positivos* (ponderados): crescimento de receita elevado (3a ≥ 20%) e
  persistente (5a ≥ 15%), aceleração (3a > 5a), FCF positivo e crescente,
  expansão de margem operacional, ROIC ≥ 15%, baixa diluição/recompra, SBC
  controlada (< 10% da receita), alavancagem baixa, crescimento consistente.
- *Sinais negativos* (penalizam): FCF persistentemente negativo, diluição
  excessiva, SBC descontrolada, dívida elevada, deterioração de margem e
  **crescimento sem retorno sobre capital**.

**Confiança** reflete a cobertura dos dados: score alto com confiança baixa é
hipótese frágil, não convicção. **Tamanho de posição** escala com score ×
confiança, com teto baixo (subcarteira pequena).

**Gestão de risco.** Não use stop puramente de preço para tese de longo prazo.
Diferencie **volatilidade** (ruído), **deterioração fundamental** (piora real) e
**invalidação da tese** (premissa quebrou) — só a última obriga a sair.

**Backtest retrospectivo** (`core/us_outlier_backtest.py`): rótulos configuráveis
(ex.: 3× em 5 anos), precisão/recall, falsos positivos, **lift sobre a taxa-base**
(comparação com seleção aleatória), distribuição de retornos, contribuição das
maiores vencedoras e o resultado quando parte das posições vai a zero. O rótulo
usa o futuro apenas como alvo — nunca como *feature* (sem look-ahead).

> ⚠️ Nada aqui é recomendação de investimento. São hipóteses geradas por regras,
> com incerteza alta e taxa de erro alta por construção.
""")
