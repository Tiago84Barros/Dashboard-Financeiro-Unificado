"""Contexto amplo para o chat da Avaliação de Portfólio das Empresas Americanas.

Equivalente de ``core/llm_context_b3.py`` para o mercado dos EUA. A detecção de
intenção é reaproveitada de lá — a pergunta do usuário é a mesma em qualquer
mercado ("compare com quem ficou de fora", "qual o setor mais concentrado"). O
que muda é a fonte: aqui tudo sai de ``core.us_data``, offline-first, e a
hierarquia é indústria SEC → setor, não segmento → subsetor → setor.

Não existe camada RAG documental: a CVM/IPE não tem equivalente indexado no
warehouse americano. Onde a B3 injeta trechos de documentos, aqui entra o
laboratório avançado (Piotroski, Altman, Sloan), calculado em código.
"""
from __future__ import annotations

import logging
import re

import pandas as pd
import streamlit as st

import core.us_data as us
from core.llm_context_b3 import detect_intent
from core.portfolio_report_common import safe_float
from core.portfolio_report_us import (
    _PEER_METRICS,
    build_industry_medians_context,
    build_us_fundamentals_context,
    compute_industry_peers,
    format_us_macro,
)

logger = logging.getLogger(__name__)

# Ticker americano: 1 a 5 letras, sem dígitos (difere do padrão XXXX3 da B3).
_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")
_STOPWORDS = frozenset({
    "A", "E", "O", "DE", "DO", "DA", "EM", "NO", "NA", "OU", "SE", "P", "L",
    "VP", "ROE", "ROA", "US", "USD", "EUA", "PIB", "CPI", "FED", "SP", "ETF",
    "IA", "LLM", "DY", "EV", "FCL", "FCO", "SBC", "PL", "M", "B", "K",
})

_CAP_UNIVERSO = 1600
_CAP_SETOR = 1800


def _norm_tk(tk: str) -> str:
    return str(tk).strip().upper()


def extrair_tickers(texto: str, universo: set[str] | None = None) -> list[str]:
    """Tickers citados na pergunta.

    Sem `universo`, um ticker americano é indistinguível de sigla comum — "FED"
    e "CPI" casam com o mesmo padrão de 1–5 letras. Por isso a lista conhecida
    é o filtro primário e as stopwords são só a rede de segurança.
    """
    if not texto:
        return []
    candidatos = [t for t in _TICKER_RE.findall(texto.upper()) if t not in _STOPWORDS]
    if universo:
        candidatos = [t for t in candidatos if t in universo]
    return list(dict.fromkeys(candidatos))


@st.cache_data(ttl=1800, show_spinner=False)
def _universo_pontuado() -> pd.DataFrame:
    try:
        frame = us.scored_universe()
    except Exception as exc:  # noqa: BLE001 - chat nunca derruba a aba
        logger.warning("Universo americano indisponível para o chat: %s", exc)
        return pd.DataFrame()
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].astype(str).str.upper()
    return out


def get_available_database_schema() -> str:
    """O que a LLM pode consultar — evita que ela invente fontes."""
    return (
        "FONTES DISPONÍVEIS (warehouse local, schema market_us — offline, sem chamada externa):\n"
        "  companies/assets: símbolo, nome, setor e indústria (classificação SEC).\n"
        "  income_statements / balance_sheets / cash_flow_statements: séries ANUAIS "
        "US GAAP por fiscal_year (receita, EBIT, EBITDA, lucro, patrimônio, dívida "
        "líquida, FCO, capex, FCL, dividendos pagos, ações em circulação).\n"
        "  prices_daily: cotações e giro diário em dólares.\n"
        "  score: pontuação fundamentalista 0–100 por trilha, calculada dentro da "
        "própria indústria.\n"
        "  laboratório avançado: Piotroski, Altman, Sloan e ROIC incremental.\n"
        "NÃO EXISTE base documental indexada (não há equivalente de CVM/IPE aqui). "
        "Se a pergunta exigir fato de documento, declare a limitação.\n"
        "Todos os valores em dólares. Custo de oportunidade: Treasury. Benchmark: S&P 500."
    )


def get_universe_context() -> str:
    """Tamanho, dispersão e extremos do universo americano elegível."""
    frame = _universo_pontuado()
    if frame.empty:
        return "UNIVERSO AMERICANO: indisponível (sem dados locais)."
    linhas = [f"UNIVERSO AMERICANO ELEGÍVEL: {len(frame)} empresas com demonstrações anuais."]
    if "sector" in frame.columns:
        contagem = frame["sector"].value_counts().head(12)
        linhas.append("  Por setor: " + " | ".join(f"{k}={v}" for k, v in contagem.items()))
    for key, label, unit in _PEER_METRICS:
        if key not in frame.columns:
            continue
        serie = pd.to_numeric(frame[key], errors="coerce").dropna()
        if len(serie) < 5:
            continue
        mediana = serie.median()
        p25, p75 = serie.quantile(0.25), serie.quantile(0.75)
        if unit == "%":
            linhas.append(f"  {label}: mediana={mediana*100:.1f}% | p25={p25*100:.1f}% | p75={p75*100:.1f}%")
        else:
            linhas.append(f"  {label}: mediana={mediana:.2f}x | p25={p25:.2f}x | p75={p75:.2f}x")
    if "score" in frame.columns:
        top = frame.sort_values("score", ascending=False).head(10)
        linhas.append(
            "  Maiores pontuações: "
            + ", ".join(f"{r['symbol']}={safe_float(r.get('score')):.0f}" for _, r in top.iterrows())
        )
    return "\n".join(linhas)[:_CAP_UNIVERSO]


def get_sector_context(sectors: list[str] | None = None) -> str:
    frame = _universo_pontuado()
    if frame.empty or "sector" not in frame.columns:
        return "COMPARAÇÃO SETORIAL: indisponível."
    alvos = [s for s in (sectors or []) if s]
    sub = frame if not alvos else frame[frame["sector"].isin(alvos)]
    if sub.empty:
        sub = frame
    linhas = ["COMPARAÇÃO SETORIAL (medianas do universo americano):"]
    for setor, grupo in sub.groupby("sector", dropna=True):
        if len(grupo) < 3:
            continue
        partes = []
        for key, label, unit in _PEER_METRICS[:7]:
            if key not in grupo.columns:
                continue
            mediana = pd.to_numeric(grupo[key], errors="coerce").median()
            if pd.notna(mediana):
                partes.append(
                    f"{label}={mediana*100:.1f}%" if unit == "%" else f"{label}={mediana:.2f}x"
                )
        if partes:
            linhas.append(f"  {setor} (n={len(grupo)}): " + " | ".join(partes))
    return "\n".join(linhas)[:_CAP_SETOR]


def get_peers_context(tickers: list[str], max_tickers: int = 3) -> tuple[str, dict]:
    """Concorrentes da mesma indústria dos tickers citados."""
    frame = _universo_pontuado()
    if frame.empty:
        return "", {}
    blocos: list[str] = []
    mapa: dict[str, list[str]] = {}
    for tk in [_norm_tk(t) for t in (tickers or [])][:max_tickers]:
        pares, nivel = compute_industry_peers(frame, tk, max_peers=8)
        if not pares:
            continue
        mapa[tk] = pares
        blocos.append(f"CONCORRENTES DE {tk} (mesma {nivel}): {', '.join(pares)}")
        blocos.append(build_us_fundamentals_context(frame, [tk, *pares], max_n=9))
    return ("\n".join(blocos), mapa) if blocos else ("", {})


def get_creation_context(model: dict) -> str:
    """Parâmetros e métricas gravados quando a carteira foi salva."""
    if not model:
        return ""
    params = model.get("params_json") or {}
    metrics = model.get("metrics_json") or {}
    linhas = ["LÓGICA DA SELEÇÃO (gravada na Criação de Portfólio):"]
    if params:
        linhas.append("  Parâmetros: " + " | ".join(
            f"{k}={v}" for k, v in list(params.items())[:14] if v is not None
        ))
    if metrics:
        linhas.append("  Métricas da carteira: " + " | ".join(
            f"{k}={v}" for k, v in list(metrics.items())[:14] if v is not None
        ))
    linhas.append(
        "  Método: líderes por indústria sobre o universo elegível, com pisos de "
        "negociabilidade e qualidade e tetos simultâneos por ativo, indústria e setor."
    )
    return "\n".join(linhas)


def build_llm_context_for_us_portfolio_chat(
    user_question: str,
    base_context: str,
    model: dict,
    weights: dict[str, float],
    macro: dict | None = None,
    portfolio_tickers: list[str] | None = None,
) -> tuple[str, dict]:
    """Contexto amplo do chat americano. Retorna (texto, meta)."""
    frame = _universo_pontuado()
    universo = set(frame["symbol"]) if not frame.empty and "symbol" in frame.columns else set()
    port_tks = [_norm_tk(t) for t in (portfolio_tickers or [])]
    universo |= set(port_tks)

    intent = detect_intent(user_question)
    citados = extrair_tickers(user_question, universo)
    externos = [t for t in citados if t not in port_tks]

    partes: list[str] = [get_available_database_schema(), "", base_context]

    if "universe" in intent or "compare_outside" in intent:
        partes += ["", get_universe_context()]

    if "sector" in intent or "compare_outside" in intent:
        setores = sorted({
            str(it.get("setor") or "") for it in (model or {}).get("items", [])
            if it.get("setor")
        })
        partes += ["", get_sector_context(setores)]

    alvos_fund = list(externos)
    if "fundamentals" in intent and citados:
        alvos_fund = list(dict.fromkeys(citados))
    if alvos_fund:
        partes += ["", build_us_fundamentals_context(frame, alvos_fund)]

    mapa_pares: dict[str, list[str]] = {}
    if "peers" in intent and citados:
        bloco_pares, mapa_pares = get_peers_context(citados)
        if bloco_pares:
            partes += ["", bloco_pares]

    if "creation" in intent:
        bloco = get_creation_context(model)
        if bloco:
            partes += ["", bloco]

    industrias = sorted({
        str(it.get("industria") or "") for it in (model or {}).get("items", [])
        if it.get("industria")
    })
    if industrias and ("sector" in intent or "fundamentals" in intent):
        partes += ["", build_industry_medians_context(frame, industrias)]

    if macro:
        partes += ["", format_us_macro(macro)]

    meta = {
        "mentioned_tickers": citados,
        "peers": mapa_pares,
        "portfolio_tickers": port_tks,
        "weights": weights,
    }
    return "\n".join(partes), meta
