"""
core/llm_context_b3.py
Camada de recuperação AMPLA de contexto para o chat "Tire Dúvidas sobre o
Portfólio" (aba Empresas B3 → Avaliação de Portfólio).

Objetivo: dar à LLM acesso estruturado a todo o banco já disponível — não apenas
ao subconjunto da carteira — de forma SELETIVA (detecção de intenção + limites de
linhas), para permitir comparações dentro/fora da carteira, por setor, múltiplos,
DRE, dividendos, criação de portfólio (selecionadas vs rejeitadas) e macro.

Reaproveita os loaders já existentes e cacheados em ``core.b3_db`` e ``core.rag_b3``.
Não duplica acesso a banco: apenas orquestra e formata. Nunca expõe credenciais.
"""
from __future__ import annotations

import json
import logging
import re

import numpy as np
import pandas as pd
import streamlit as st

import core.b3_db as _db
import core.data_quality as _dq

logger = logging.getLogger(__name__)

# Métricas-chave usadas nas agregações de universo/setor
_KEY_COLS = ("P/L", "P/VP", "DY", "ROE", "ROIC", "Margem_Liquida", "Endividamento_Total")
_PCT_COLS = {"DY", "ROE", "ROA", "ROIC", "Margem_Liquida", "Margem_Operacional", "Payout"}
_LABEL = {
    "P/L": "P/L", "P/VP": "P/VP", "DY": "DY", "ROE": "ROE", "ROA": "ROA", "ROIC": "ROIC",
    "Margem_Liquida": "MargemLiq", "Endividamento_Total": "Endiv", "Payout": "Payout",
}

_TICKER_RE = re.compile(r"\b([A-Z]{4}\d{1,2})\b")

# Tetos de caracteres por bloco (evita estourar o prompt)
_CAP_UNIVERSE = 1200
_CAP_SECTOR   = 1800
_CAP_FUND     = 1800
_CAP_CREATION = 1800


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de formatação
# ─────────────────────────────────────────────────────────────────────────────

def _norm_tk(tk: str) -> str:
    return str(tk).strip().upper().replace(".SA", "")


def _fmt_val(col: str, v) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)) or pd.isna(v):
        return "N/D"
    v = float(v)
    if col in _PCT_COLS:
        return f"{v*100:.1f}%" if abs(v) <= 2.0 else f"{v:.1f}%"
    return f"{v:.2f}"


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …(truncado)"


def _extract_tickers(text: str) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(m.group(1) for m in _TICKER_RE.finditer(text.upper())))


# ─────────────────────────────────────────────────────────────────────────────
# Detecção de intenção
# ─────────────────────────────────────────────────────────────────────────────

def detect_intent(user_question: str) -> set[str]:
    """
    Mapeia a pergunta para um conjunto de blocos a carregar.
    Blocos: 'universe', 'sector', 'fundamentals', 'creation', 'compare_outside'.
    'portfolio', 'macro' e 'schema' são sempre incluídos pelo orquestrador.
    """
    q = (user_question or "").lower()
    blocks: set[str] = set()

    fora = any(t in q for t in ("fora da carteira", "fora do portf", "que ficaram de fora",
                                "não selec", "nao selec", "rejeit", "descartad", "substitu",
                                "melhor que", "melhor do que", "alternativa", "trocar"))
    if fora:
        blocks.add("compare_outside")
        blocks.add("universe")

    if any(t in q for t in ("setor", "segmento", "indústria", "industria", "industrial",
                            "financeir", "varejo", "energia", "banco", "sanea", "concentr",
                            "diversific")):
        blocks.add("sector")

    if any(t in q for t in ("compar", "versus", " vs ", "ranking", "maior", "menor", "melhor",
                            "pior", "top ", "quais ações", "quais acoes", "quais empresas",
                            "roe", "roic", "p/l", "p/vp", "ev/ebit", "margem", "dívida",
                            "divida", "dividend", "payout", "crescimento", "valuation")):
        blocks.add("fundamentals")

    if any(t in q for t in ("por que", "porque", "por quê", "escolhid", "selec", "criação",
                            "criacao", "aprovad", "motivo", "lógica", "logica", "racional")):
        blocks.add("creation")

    if any(t in q for t in ("universo", "todas as empresas", "toda a b3", "mercado", "ibov")):
        blocks.add("universe")

    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# Schema disponível
# ─────────────────────────────────────────────────────────────────────────────

def get_available_database_schema() -> str:
    """Descrição curta das tabelas/dados consultáveis (orienta a LLM)."""
    return (
        "DADOS DISPONÍVEIS NO BANCO (você pode raciocinar sobre todos eles; abaixo já "
        "constam os recortes carregados para esta pergunta):\n"
        "  - setores: universo B3 (ticker, nome, SETOR, SUBSETOR, SEGMENTO).\n"
        "  - multiplos: múltiplos fundamentalistas por ticker e ano (P/L, P/VP, DY, ROE, "
        "ROA, ROIC, Margem_Liquida, Margem_Operacional, Endividamento_Total, "
        "Liquidez_Corrente, EV_EBIT, P_FCO, Payout).\n"
        "  - Demonstracoes_Financeiras: DRE anual (Receita_Liquida, Lucro_Liquido, EBITDA, "
        "Divida_Liquida, etc.).\n"
        "  - macro: indicadores macro anuais (Selic, IPCA, câmbio, PIB, dívida pública…).\n"
        "  - docs_corporativos / docs_corporativos_chunks: documentos CVM/IPE (RAG).\n"
        "  - b3_portfolio_models / _items: carteira-modelo salva (pesos, score, alpha, "
        "segmento, motivos de aprovação) e parâmetros/segmentos da Criação de Portfólio.\n"
        "  - Criação de Portfólio (sessão): segmentos analisados, empresas consideradas, "
        "aprovadas e rejeitadas, quando disponíveis.\n"
        "OBS.: valor de mercado e séries de preço nem sempre estão na base; só afirme "
        "indisponibilidade se o dado realmente não constar nos recortes abaixo."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Universo B3
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def get_full_b3_universe_context() -> str:
    """Resumo do universo: contagem por setor e amplitude — não despeja linhas."""
    try:
        setores = _db.load_setores()
    except Exception as exc:
        logger.warning("universe: load_setores falhou: %s", exc)
        setores = pd.DataFrame()
    if setores is None or setores.empty:
        return "UNIVERSO B3: indisponível (tabela `setores` vazia ou ausente)."

    n_emp = setores["ticker"].nunique()
    by_setor = (setores.groupby("SETOR")["ticker"].nunique()
                .sort_values(ascending=False))
    linhas = [f"UNIVERSO B3: {n_emp} empresas em {by_setor.shape[0]} setores."]
    top = ", ".join(f"{s}={int(n)}" for s, n in by_setor.head(14).items() if s)
    linhas.append("  Empresas por setor: " + top)
    return _cap("\n".join(linhas), _CAP_UNIVERSE)


# ─────────────────────────────────────────────────────────────────────────────
# Comparação setorial
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def _universe_with_sector() -> pd.DataFrame:
    """Junta múltiplos (todos os tickers) com setor/segmento."""
    try:
        mult = _db.load_multiplos_todos()
        setores = _db.load_setores()
    except Exception as exc:
        logger.warning("sector: load falhou: %s", exc)
        return pd.DataFrame()
    if mult is None or mult.empty:
        return pd.DataFrame()
    # Limpeza pela fonte única (data_quality): outliers (ex.: margem 190%) e
    # zeros-faltantes (ex.: DY=0) viram NaN → não poluem fundamentos nem medianas
    # setoriais e aparecem como N/D em vez de números errados.
    mult = _dq.clean_multiples_frame(mult)
    if setores is not None and not setores.empty:
        sec = setores[["ticker", "SETOR", "SEGMENTO"]].rename(columns={"ticker": "Ticker"})
        mult = mult.merge(sec, on="Ticker", how="left")
    return mult


def get_sector_comparison_context(segments: list[str] | None = None,
                                  portfolio_tickers: list[str] | None = None) -> str:
    """Medianas dos múltiplos por SETOR (foca nos setores da carteira)."""
    df = _universe_with_sector()
    if df.empty or "SETOR" not in df.columns:
        return "COMPARAÇÃO SETORIAL: indisponível (sem múltiplos+setor no banco)."

    focus = set(s for s in (segments or []) if s)
    port = set(_norm_tk(t) for t in (portfolio_tickers or []))
    if not focus and port and "Ticker" in df.columns:
        focus = set(df[df["Ticker"].isin(port)]["SETOR"].dropna().astype(str))

    cols = [c for c in _KEY_COLS if c in df.columns]
    grp = df.groupby("SETOR")
    lines = ["COMPARAÇÃO SETORIAL (medianas do universo — setores da carteira em foco):"]
    setores_iter = [s for s in grp.groups if (not focus or str(s) in focus)]
    # limita a 8 setores para caber no orçamento
    for setor in sorted(setores_iter)[:8]:
        sub = grp.get_group(setor)
        n = sub["Ticker"].nunique() if "Ticker" in sub.columns else len(sub)
        med = " | ".join(
            f"{_LABEL.get(c, c)}={_fmt_val(c, pd.to_numeric(sub[c], errors='coerce').median())}"
            for c in cols
        )
        lines.append(f"  {setor} (n={n}): {med}")
    return _cap("\n".join(lines), _CAP_SECTOR)


# ─────────────────────────────────────────────────────────────────────────────
# Fundamentos de tickers específicos (dentro ou fora da carteira)
# ─────────────────────────────────────────────────────────────────────────────

def get_company_fundamentals_context(tickers: list[str], max_n: int = 15) -> str:
    """Múltiplos atuais de tickers específicos (in/out da carteira)."""
    tks = [_norm_tk(t) for t in (tickers or []) if t]
    tks = list(dict.fromkeys(tks))[:max_n]
    if not tks:
        return ""
    df = _universe_with_sector()
    if df.empty or "Ticker" not in df.columns:
        return "FUNDAMENTOS SOLICITADOS: indisponíveis (sem múltiplos no banco)."
    cols = [c for c in (*_KEY_COLS, "ROA", "EV_EBIT", "Payout") if c in df.columns]
    sub = df[df["Ticker"].isin(tks)]
    if sub.empty:
        return f"FUNDAMENTOS SOLICITADOS: nenhum dado no banco para {', '.join(tks)}."
    lines = ["FUNDAMENTOS DE EMPRESAS CONSULTADAS (banco — múltiplos mais recentes):"]
    for _, row in sub.iterrows():
        setor = row.get("SETOR") or row.get("SEGMENTO") or ""
        inds = " | ".join(f"{_LABEL.get(c, c)}={_fmt_val(c, row.get(c))}" for c in cols)
        lines.append(f"  {row['Ticker']} [{setor}]: {inds}")
    return _cap("\n".join(lines), _CAP_FUND)


# ─────────────────────────────────────────────────────────────────────────────
# Macro
# ─────────────────────────────────────────────────────────────────────────────

def get_macro_context(macro_hist: dict | None = None) -> str:
    hist = macro_hist if macro_hist else _db.load_macro_history()
    if not hist:
        return "MACRO: indisponível."
    anos = sorted(hist.keys(), reverse=True)[:3]
    lines = ["MACROECONOMIA (últimos anos):"]
    for ano in sorted(anos):
        d = hist[ano]
        parts = []
        if "selic" in d:  parts.append(f"Selic={d['selic']*100:.2f}%")
        if "ipca" in d:   parts.append(f"IPCA={d['ipca']*100:.2f}%")
        if "cambio" in d: parts.append(f"USD/BRL={d['cambio']:.2f}")
        if parts:
            lines.append(f"  {ano}: {', '.join(parts)}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Contexto da Criação de Portfólio (selecionadas vs rejeitadas)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_json_field(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def get_creation_context(model: dict, max_rejected: int = 12) -> str:
    """
    Empresas selecionadas (na carteira), segmentos analisados/aprovados e
    candidatas rejeitadas/não selecionadas. Fonte robusta: modelo salvo + universo.
    Enriquecido por `pb3_resultados` da sessão, se presente.
    """
    items = (model or {}).get("items", [])
    sel_tks = [_norm_tk(it.get("ticker", "")) for it in items if it.get("ticker")]
    segs_carteira = sorted(set(str(it.get("setor") or it.get("segmento") or "")
                               for it in items if (it.get("setor") or it.get("segmento"))))
    params = _parse_json_field((model or {}).get("params_json"), {})

    lines = ["CRIAÇÃO DE PORTFÓLIO (seleção):"]
    lines.append(f"  Selecionadas (na carteira): {', '.join(sel_tks) or '—'}")
    if isinstance(params, dict) and params:
        for k in ("segmentos_analisados", "segmentos_aprovados", "min_anos_dre"):
            if params.get(k) is not None:
                lines.append(f"  {k}: {params.get(k)}")
    if segs_carteira:
        lines.append(f"  Setores presentes na carteira: {', '.join(segs_carteira)}")

    # Motivos de aprovação por empresa (curtos)
    for it in items[:12]:
        motivos = _parse_json_field(it.get("motivos_json") or it.get("motivos"), [])
        if isinstance(motivos, list) and motivos:
            lines.append(f"  {_norm_tk(it.get('ticker',''))}: {'; '.join(str(m) for m in motivos[:3])}")

    # Rejeitadas/não selecionadas — universo dos setores da carteira menos as selecionadas
    rejected_done = False
    resultados = st.session_state.get("pb3_resultados")
    if isinstance(resultados, list) and resultados:
        considerados: list[str] = []
        for res in resultados:
            for t in (res.get("tickers") or []):
                considerados.append(_norm_tk(t))
        rej = [t for t in dict.fromkeys(considerados) if t and t not in sel_tks][:max_rejected]
        if rej:
            lines.append(f"  Consideradas mas NÃO selecionadas (Criação, sessão): {', '.join(rej)}")
            rejected_done = True

    if not rejected_done and segs_carteira:
        df = _universe_with_sector()
        if not df.empty and "SETOR" in df.columns and "Ticker" in df.columns:
            pool = df[df["SETOR"].isin(segs_carteira) & ~df["Ticker"].isin(sel_tks)]
            if not pool.empty:
                rej = sorted(pool["Ticker"].dropna().astype(str).unique())[:max_rejected]
                lines.append(f"  Pares dos mesmos setores fora da carteira (universo): {', '.join(rej)}")

    return _cap("\n".join(lines), _CAP_CREATION)


# ─────────────────────────────────────────────────────────────────────────────
# RAG seletivo (documentos CVM)
# ─────────────────────────────────────────────────────────────────────────────

def get_chunks_context(query: str, tickers: list[str], cobertura_docs: dict | None = None) -> str:
    """Recupera trechos CVM/IPE relevantes (até 3 tickers)."""
    try:
        from core.rag_b3 import retrieve_chunks, format_rag_context
    except Exception:
        return ""
    cob = cobertura_docs or {}
    up = (query or "").upper()
    cand = [t for t in (_norm_tk(x) for x in (tickers or [])) if t]
    mencionados = [t for t in cand if t in up] or cand
    alvo = [t for t in mencionados if cob.get(t, 1) != 0][:3]
    if not alvo:
        return ""
    chunks: list[dict] = []
    for tk in alvo:
        try:
            c, _ = retrieve_chunks(tk, top_k_total=10, per_topic_k=3)
            chunks.extend(c or [])
        except Exception:
            continue
    if not chunks:
        return ""
    return "DOCUMENTOS CVM/IPE (trechos):\n" + format_rag_context(chunks, max_chars=2500)


# ─────────────────────────────────────────────────────────────────────────────
# Orquestrador
# ─────────────────────────────────────────────────────────────────────────────

def build_llm_context_for_portfolio_chat(
    user_question: str,
    base_context: str,
    model: dict,
    weights: dict[str, float],
    macro_hist: dict | None = None,
    portfolio_tickers: list[str] | None = None,
    cobertura_docs: dict | None = None,
) -> tuple[str, dict]:
    """
    Monta o contexto AMPLO para o chat. `base_context` é a saída do
    ``_build_chat_context`` existente (carteira + consolidados + RAG + macro da
    carteira). Adiciona schema e, conforme a intenção, blocos de universo,
    setor, fundamentos externos e criação de portfólio.

    Retorna (context_str, meta) — `meta` alimenta os gráficos.
    """
    port_tks = [_norm_tk(t) for t in (portfolio_tickers or [])]
    intent = detect_intent(user_question)
    q_tickers = _extract_tickers(user_question)
    # tickers externos mencionados (não na carteira)
    externos = [t for t in q_tickers if t not in port_tks]

    parts: list[str] = [get_available_database_schema(), "", base_context]

    if "universe" in intent:
        parts += ["", get_full_b3_universe_context()]

    if "sector" in intent or "compare_outside" in intent:
        segs = sorted(set(str(it.get("setor") or it.get("segmento") or "")
                          for it in (model or {}).get("items", []) if (it.get("setor") or it.get("segmento"))))
        parts += ["", get_sector_comparison_context(segments=segs, portfolio_tickers=port_tks)]

    # fundamentos: tickers externos citados + (se comparação fora) alguns pares
    fund_tks = list(externos)
    if "fundamentals" in intent and q_tickers:
        fund_tks = list(dict.fromkeys(q_tickers))  # inclui também os citados da carteira p/ comparar
    if fund_tks:
        block = get_company_fundamentals_context(fund_tks)
        if block:
            parts += ["", block]

    if "creation" in intent or "compare_outside" in intent:
        parts += ["", get_creation_context(model)]

    context = "\n".join(p for p in parts if p is not None)

    meta = {
        "model": model,
        "weights": weights,
        "portfolio_tickers": port_tks,
        "mentioned_tickers": q_tickers,
        "external_tickers": externos,
        "intent": sorted(intent),
    }
    return context, meta
