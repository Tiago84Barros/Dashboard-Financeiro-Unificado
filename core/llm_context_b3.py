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

import core.b3_data as _db  # facade c/ feature flag MARKET_READ_SOURCE (default: legacy)
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

    if any(t in q for t in ("concorrent", "concorrência", "concorrencia", "competidor",
                            "competir", "rival", "pares", "peer", "mesmo segmento",
                            "mesmo setor", "mesma indústria", "mesma industria", "vs ")):
        blocks.add("peers")

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
        "  - historical_prices: séries de preço mensais AJUSTADAS (retorno total) por ticker "
        "— disponíveis para gráfico de desempenho ('performance').\n"
        "OBS.: séries de PREÇO e a DRE histórica (receita/lucro por ano) ESTÃO no banco para "
        "praticamente todos os tickers — para gráficos de preço/desempenho e de receita×lucro, "
        "EMITA a diretiva de gráfico ('performance' / 'financials'); NÃO afirme indisponibilidade."
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
# Evidência web (Fundamentus + Status Invest) confrontada com o banco
# ─────────────────────────────────────────────────────────────────────────────

# Ações da reconciliação que significam "a web mudou o que o banco dizia".
_ACOES_WEB = ("web_preencheu", "web_corrigiu", "db_sobrescrito")
_CAP_WEB = 2600


def _corroboracao(preenchidos: int, divergentes: int) -> float:
    """Fator 0,90–1,05 sobre a convicção da empresa.

    Divergir da web não prova que o banco está errado, então o castigo é
    pequeno e limitado: derruba no máximo 10% do peso. Concordar dá um bônus
    menor ainda — dado corroborado é o caso NORMAL, não um mérito.
    """
    if divergentes:
        return max(0.90, 1.0 - 0.035 * divergentes)
    if preenchidos:
        return 1.0
    return 1.05


@st.cache_data(ttl=1800, show_spinner=False)
def get_web_evidence_context(tickers: tuple[str, ...]) -> tuple[str, dict[str, dict]]:
    """Confronta os fundamentos do banco com Fundamentus e Status Invest.

    Reaproveita ``core.data_reconciliacao.batch_multiplos_reconciliados``, que
    já é o caminho oficial banco→web do app (mesmos limiares de discrepância,
    mesma normalização de escala). Aqui a reconciliação não corrige tabela
    nenhuma: vira (1) texto para o prompt, para a LLM saber quais números têm
    segunda fonte e quais são palavra do banco, e (2) um fator de convicção por
    empresa, usado na redistribuição de pesos.

    Rede indisponível devolve ("", {}) — offline a análise segue só com banco.
    """
    tks = tuple(dict.fromkeys(_norm_tk(t) for t in (tickers or []) if t))
    if not tks:
        return "", {}
    try:
        import core.data_reconciliacao as _recon
        _, audit, summary = _recon.batch_multiplos_reconciliados(
            tks, include_status=True,
        )
    except Exception as exc:  # noqa: BLE001 - fonte externa nunca derruba a análise
        logger.warning("Evidência web indisponível: %s", exc)
        return "", {}

    por_ticker: dict[str, dict] = {
        tk: {"preenchidos": 0, "divergentes": 0, "detalhes": []} for tk in tks
    }
    if audit is not None and not audit.empty:
        for _, row in audit.iterrows():
            tk = _norm_tk(row.get("Ticker", ""))
            if tk not in por_ticker:
                continue
            acao = str(row.get("Acao") or "")
            if acao not in _ACOES_WEB:
                continue
            registro = por_ticker[tk]
            if acao == "db_sobrescrito":
                registro["divergentes"] += 1
            else:
                registro["preenchidos"] += 1
            registro["detalhes"].append(
                f"{row.get('Indicador')}: banco={_fmt_val(str(row.get('Indicador')), row.get('Antes'))} "
                f"→ {row.get('Fonte')}={_fmt_val(str(row.get('Indicador')), row.get('Depois'))}"
            )

    for registro in por_ticker.values():
        registro["fator"] = _corroboracao(registro["preenchidos"], registro["divergentes"])

    linhas = [
        "EVIDÊNCIA WEB (Fundamentus + Status Invest) CONFRONTADA COM O BANCO:",
        "  Regra de leitura: sem linha abaixo, o indicador do banco foi confirmado "
        "pela web ou não tem segunda fonte. Divergência NÃO prova erro do banco — "
        "trate como incerteza e diga isso na análise.",
    ]
    for tk in tks:
        registro = por_ticker[tk]
        if not registro["detalhes"]:
            linhas.append(f"  {tk}: sem divergência entre banco e web.")
            continue
        linhas.append(
            f"  {tk}: {registro['divergentes']} divergência(s), "
            f"{registro['preenchidos']} lacuna(s) preenchida(s) pela web — "
            + "; ".join(registro["detalhes"][:6])
        )
    if summary:
        linhas.append(
            f"  Resumo: {summary.get('correcoes_web', 0)} célula(s) ajustada(s) pela web."
        )
    return _cap("\n".join(linhas), _CAP_WEB), por_ticker


# ─────────────────────────────────────────────────────────────────────────────
# Pares / concorrentes (mesmo segmento) de tickers citados
# ─────────────────────────────────────────────────────────────────────────────

def compute_segment_peers(ticker: str, max_peers: int = 10) -> tuple[list[str], str]:
    """
    Retorna (peers, nivel) — empresas do MESMO SEGMENTO do ticker; se houver
    menos de 3, expande p/ SUBSETOR e depois SETOR. Ordena por receita (maiores
    primeiro) quando a DRE estiver disponível. `peers` exclui o próprio ticker.
    """
    tk = _norm_tk(ticker)
    try:
        setores = _db.load_setores()
    except Exception:
        setores = pd.DataFrame()
    if setores is None or setores.empty or "ticker" not in setores.columns:
        return [], ""
    srow = setores[setores["ticker"] == tk]
    if srow.empty:
        return [], ""
    seg = str(srow["SEGMENTO"].iloc[0] or "")
    sub = str(srow["SUBSETOR"].iloc[0] or "")
    setor = str(srow["SETOR"].iloc[0] or "")

    raiz = tk[:4]  # radical p/ excluir outras classes da MESMA empresa (EUCA3 de EUCA4)

    def _peers(col, val):
        if not val:
            return []
        pool = setores[setores[col] == val]
        return [t for t in dict.fromkeys(pool["ticker"].tolist())
                if t and t != tk and t[:4] != raiz]

    peers, nivel = _peers("SEGMENTO", seg), f"segmento {seg}"
    if len(peers) < 3 and sub:
        peers, nivel = _peers("SUBSETOR", sub), f"subsetor {sub}"
    if not peers and setor:
        peers, nivel = _peers("SETOR", setor), f"setor {setor}"
    if not peers:
        return [], nivel

    # ordena por receita líquida (tamanho) quando houver DRE
    rec: dict[str, float] = {}
    try:
        dres = _db.load_demonstracoes_batch(tuple([tk] + peers))
        for p, d in (dres or {}).items():
            if isinstance(d, pd.DataFrame) and not d.empty and \
               "Receita_Liquida" in d.columns and "Data" in d.columns:
                s = pd.to_numeric(d.sort_values("Data")["Receita_Liquida"],
                                  errors="coerce").dropna()
                if not s.empty:
                    rec[p] = float(s.iloc[-1])
    except Exception:
        pass
    peers.sort(key=lambda p: rec.get(p, -1.0), reverse=True)
    return peers[:max_peers], nivel


def get_peers_context(tickers: list[str], max_tickers: int = 2) -> tuple[str, dict]:
    """
    Lista os concorrentes (mesmo segmento) dos tickers citados, com nome, receita
    (tamanho) e múltiplos-chave. Retorna (texto, peers_map) — peers_map alimenta
    o gráfico de comparação de pares.
    """
    tks = list(dict.fromkeys(_norm_tk(t) for t in (tickers or []) if t))[:max_tickers]
    if not tks:
        return "", {}
    try:
        setores = _db.load_setores()
    except Exception:
        setores = pd.DataFrame()
    nomes = dict(zip(setores["ticker"], setores["nome_empresa"])) if not setores.empty else {}
    dfm = _universe_with_sector()

    lines: list[str] = []
    peers_map: dict[str, list[str]] = {}
    for tk in tks:
        peers, nivel = compute_segment_peers(tk)
        if not peers:
            continue
        peers_map[tk] = peers
        lines.append(f"CONCORRENTES DE {tk} ({nivel} — maiores por receita primeiro):")
        for p in peers:
            nm = str(nomes.get(p, "") or "")[:26]
            m = ""
            if not dfm.empty and "Ticker" in dfm.columns:
                mrow = dfm[dfm["Ticker"] == p]
                if not mrow.empty:
                    m = " | " + " ".join(
                        f"{_LABEL.get(c, c)}={_fmt_val(c, mrow[c].iloc[0])}"
                        for c in ("P/L", "ROE", "Margem_Liquida") if c in mrow.columns)
            lines.append(f"  {p} [{nm}]{m}")
    if not lines:
        return "", {}
    return _cap("\n".join(lines), _CAP_SECTOR), peers_map


# ─────────────────────────────────────────────────────────────────────────────
# DRE histórica (receita / lucro / EBITDA por ano) de tickers citados
# ─────────────────────────────────────────────────────────────────────────────

def get_dre_history_context(tickers: list[str], max_n: int = 3, anos: int = 6) -> str:
    """Série anual de Receita_Liquida / Lucro_Liquido / EBITDA dos tickers citados."""
    tks = list(dict.fromkeys(_norm_tk(t) for t in (tickers or []) if t))[:max_n]
    if not tks:
        return ""
    def _mi(v):
        try:
            f = float(v)
            return f"{f/1e6:,.0f}" if np.isfinite(f) else "N/D"
        except (TypeError, ValueError):
            return "N/D"
    lines: list[str] = []
    for tk in tks:
        try:
            d = _db.load_demonstracoes(tk)
        except Exception:
            d = pd.DataFrame()
        if d is None or d.empty or "Data" not in d.columns:
            continue
        d = d.copy()
        d["_ano"] = pd.to_datetime(d["Data"], errors="coerce").dt.year
        d = d.dropna(subset=["_ano"]).sort_values("_ano").tail(anos)
        if d.empty:
            continue
        anos_txt = ", ".join(
            f"{int(r['_ano'])}: Rec={_mi(r.get('Receita_Liquida'))} "
            f"LL={_mi(r.get('Lucro_Liquido'))}"
            + (f" EBITDA={_mi(r.get('EBITDA'))}" if pd.notna(r.get('EBITDA')) else "")
            for _, r in d.iterrows())
        lines.append(f"  {tk} (R$ mi): {anos_txt}")
    if not lines:
        return ""
    return "DRE HISTÓRICA (banco — Receita/Lucro líquido por ano, R$ milhões):\n" + "\n".join(lines)


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
            c, _ = retrieve_chunks(tk, top_k_total=30, per_topic_k=3, months_back=48)
            chunks.extend(c or [])
        except Exception:
            continue
    if not chunks:
        return ""
    # format_rag_context prioriza Fato Relevante/Resultados e ordena cronologicamente.
    return "DOCUMENTOS CVM/IPE (trechos):\n" + format_rag_context(chunks, max_chars=4500)


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

    # DRE histórica (receita/lucro por ano) dos tickers citados — habilita análise
    # de tendência e o gráfico 'financials'. Também cobre a carteira quando a
    # pergunta é sobre receita/lucro/crescimento sem citar ticker.
    q_low = (user_question or "").lower()
    dre_tks = list(q_tickers)
    if not dre_tks and any(t in q_low for t in ("receita", "lucro", "dre", "faturamento",
                                                "resultado", "crescimento")):
        dre_tks = port_tks
    if dre_tks:
        dre_block = get_dre_history_context(dre_tks)
        if dre_block:
            parts += ["", dre_block]

    # Concorrentes (mesmo segmento) dos tickers citados — identifica pares p/ a LLM
    # e alimenta o gráfico de comparação; sem isso ela não sabe quem são os rivais.
    peers_map: dict[str, list[str]] = {}
    if "peers" in intent and q_tickers:
        peers_block, peers_map = get_peers_context(q_tickers)
        if peers_block:
            parts += ["", peers_block]

    # Documentos CVM/IPE SOB DEMANDA para qualquer ticker citado na pergunta
    # (dentro OU fora da carteira). Aplica os mesmos critérios do RAG (limpeza de
    # rodapé/disclaimer + ordem temporal), permitindo análise de ativos fora do
    # portfólio diretamente no chat. O base_context já traz o RAG da carteira;
    # aqui garantimos cobertura para os ativos externos consultados.
    if q_tickers:
        docs_block = get_chunks_context(user_question, q_tickers, cobertura_docs)
        if docs_block:
            parts += ["", docs_block]

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
        "peers": peers_map,
    }
    return context, meta
