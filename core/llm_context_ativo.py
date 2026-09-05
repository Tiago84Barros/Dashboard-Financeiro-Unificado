"""Contexto auditável de UM ativo para o chat da tela de análise.

Reaproveita os construtores já usados pelos chats de carteira
(`core.llm_context_b3`, `core.llm_context_us`), mas recorta tudo em volta do
ticker aberto na tela em vez de uma seleção de portfólio.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

_CAP_CONTEXTO = 22000


def _fmt(value, casas: int = 2) -> str:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return "ausente"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{float(value):,.{casas}f}"
    except Exception:
        pass
    text = str(value).strip()
    return text or "ausente"


def _cap(texto: str) -> str:
    if len(texto) <= _CAP_CONTEXTO:
        return texto
    return texto[:_CAP_CONTEXTO] + "\n[contexto truncado por limite de tamanho]"


def _series_block(dados, campos: list[tuple[str, str]], titulo: str) -> str:
    linhas = [titulo]
    for rotulo, chave in campos:
        try:
            valor = dados.get(chave)
        except Exception:
            valor = None
        linhas.append(f"  {rotulo}: {_fmt(valor)}")
    return "\n".join(linhas)


def _local_macro_block(asset_class: str, symbol: str, sector: str) -> str:
    """Recorte mínimo por ativo; falha local nunca bloqueia a análise-base."""
    if not symbol or not sector:
        return ""
    try:
        from core.macro_data.database import get_local_macro_engine
        from core.macro_data.portfolio_context import (
            format_portfolio_macro_context,
            load_portfolio_macro_snapshot,
        )

        engine = get_local_macro_engine()
        if engine is None:
            return ""
        snapshot = load_portfolio_macro_snapshot(
            engine, asset_class=asset_class, assets={symbol: sector},
        )
        return format_portfolio_macro_context(snapshot)
    except Exception:
        logger.exception("contexto macro local de %s indisponível", symbol)
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# B3
# ─────────────────────────────────────────────────────────────────────────────

def build_b3_ativo_context(
    ticker: str, *, user_question: str = "", nome: str = "", setor: str = "",
    subsetor: str = "", preco=None, preco_status: str = "", mult=None,
    df_fin: pd.DataFrame | None = None, fontes: dict | None = None,
) -> str:
    """Identidade, múltiplos, DRE, pares de segmento, documentos CVM e macro."""
    from core.llm_context_b3 import (
        get_chunks_context,
        get_company_fundamentals_context,
        get_dre_history_context,
        get_macro_context,
        get_peers_context,
    )
    tk = str(ticker or "").strip().upper()
    preco_txt = {
        "falha_rede": "indisponível (falha de rede ao consultar o provedor)",
        "sem_dado": "indisponível (sem cotação no provedor)",
    }.get(str(preco_status or ""), _fmt(preco))
    blocos = [
        "ATIVO EM ANÁLISE (tela Empresas B3 → Análise de empresas):",
        f"  Ticker: {tk} | Nome: {nome or 'ausente'}",
        f"  Setor: {setor or 'ausente'} | Subsetor: {subsetor or 'ausente'}",
        f"  Preço corrente: {preco_txt}",
    ]
    if mult is not None and getattr(mult, "empty", True) is False:
        itens = [f"{k}={_fmt(v)}" for k, v in mult.to_dict().items()
                 if k not in ("Ticker", "data") and v is not None]
        blocos.append("  Múltiplos do snapshot: " + ("; ".join(itens) or "ausentes"))
    else:
        blocos.append("  Múltiplos do snapshot: ausentes no banco para este ticker.")
    if fontes:
        blocos.append("  Procedência por métrica: " + "; ".join(
            f"{k}={v}" for k, v in list(fontes.items())[:20]))
    if df_fin is not None and not df_fin.empty:
        blocos.append(f"  Demonstrações disponíveis: {len(df_fin)} exercícios no banco.")
    else:
        blocos.append("  Demonstrações: nenhuma linha no banco para este ticker.")

    for fn in (get_company_fundamentals_context, get_dre_history_context):
        try:
            texto = fn([tk])
            if texto:
                blocos.append("\n" + texto)
        except Exception:
            logger.exception("contexto B3 do ativo %s falhou em %s", tk, fn.__name__)
    try:
        pares_txt, _ = get_peers_context([tk], max_tickers=1)
        if pares_txt:
            blocos.append("\n" + pares_txt)
    except Exception:
        logger.exception("pares de %s indisponíveis", tk)
    try:
        docs = get_chunks_context(user_question or tk, [tk], None)
        if docs:
            blocos.append("\n" + docs)
    except Exception:
        logger.exception("documentos CVM de %s indisponíveis", tk)
    try:
        macro = get_macro_context(None)
        if macro:
            blocos.append("\n" + macro)
    except Exception:
        logger.exception("macro indisponível")
    local_macro = _local_macro_block("b3", tk, setor)
    if local_macro:
        blocos.append("\n" + local_macro)
    return _cap("\n".join(blocos))


# ─────────────────────────────────────────────────────────────────────────────
# Empresas americanas
# ─────────────────────────────────────────────────────────────────────────────

_US_CAMPOS = [
    ("Nome", "name"), ("Setor", "sector"), ("Indústria", "industry"),
    ("Score total", "score_total"), ("Confiança do score", "score_confidence"),
    ("Cobertura", "coverage"), ("Status", "status"),
    ("ROE", "roe"), ("Margem líquida", "net_margin"), ("P/L", "pe"),
    ("EV/EBIT", "ev_ebit"), ("Dívida/PL", "debt_to_equity"),
    ("Liquidez corrente", "current_ratio"), ("Dividend yield", "dividend_yield"),
]


def build_us_ativo_context(
    symbol: str, *, user_question: str = "", row=None,
    financials: pd.DataFrame | None = None, current_price=None,
) -> str:
    """Cadastro pontuado, série de demonstrações, pares de indústria e dossiê."""
    from core.llm_context_us import get_peers_context, get_sector_context
    sym = str(symbol or "").strip().upper()
    blocos = [
        "ATIVO EM ANÁLISE (tela Empresas Americanas → Análise de empresas):",
        f"  Símbolo: {sym}",
        f"  Preço corrente: US$ {_fmt(current_price)}",
        "  Universo: apenas ações ordinárias; REIT, fundo, SPAC e preferencial "
        "estão fora do módulo por regra.",
    ]
    if row is not None:
        blocos.append(_series_block(row, _US_CAMPOS, "  Cadastro pontuado:"))
    if financials is not None and not financials.empty:
        col_ano = next((c for c in ("fiscal_year", "year", "period_end")
                        if c in financials.columns), None)
        anos = ""
        if col_ano:
            serie = financials[col_ano].dropna().astype(str)
            if not serie.empty:
                anos = f" ({serie.min()}–{serie.max()})"
        blocos.append(f"  Demonstrações: {len(financials)} períodos no banco{anos}.")
    else:
        blocos.append("  Demonstrações: nenhuma linha no banco para este símbolo.")

    try:
        setor = str(row.get("sector")) if row is not None else ""
    except Exception:
        setor = ""
    try:
        from core.market_companies import translate_us_sector

        setor_macro = translate_us_sector(
            row.get("sector") if row is not None else "",
            row.get("industry") if row is not None else "",
        )
    except Exception:
        setor_macro = setor
    if setor and setor.lower() not in ("none", "nan", ""):
        try:
            texto = get_sector_context([setor])
            if texto:
                blocos.append("\n" + texto)
        except Exception:
            logger.exception("contexto setorial de %s indisponível", sym)
    try:
        pares_txt, _ = get_peers_context([sym], max_tickers=1)
        if pares_txt:
            blocos.append("\n" + pares_txt)
    except Exception:
        logger.exception("pares de %s indisponíveis", sym)
    try:
        import core.us_data as us
        dossie = us.dossie(sym)
        if isinstance(dossie, dict) and not dossie.get("erro"):
            itens = [f"{k}={v}" for k, v in dossie.items()
                     if not isinstance(v, (dict, list, pd.DataFrame)) and v is not None]
            if itens:
                blocos.append("\nDOSSIÊ DA EMPRESA: " + "; ".join(itens[:30]))
    except Exception:
        logger.exception("dossiê de %s indisponível", sym)
    local_macro = _local_macro_block("us", sym, setor_macro)
    if local_macro:
        blocos.append("\n" + local_macro)
    return _cap("\n".join(blocos))


# ─────────────────────────────────────────────────────────────────────────────
# FIIs
# ─────────────────────────────────────────────────────────────────────────────

_FII_CAMPOS = [
    ("Nome", "Nome"), ("Tipo", "Tipo"), ("Segmento", "Segmento"),
    ("Gestão", "Gestao"), ("Preço", "Preço"), ("P/VP", "P/VP"),
    ("DY 12m", "DY_12m"), ("VPA", "VPA"), ("Patrimônio líquido", "Patrimonio"),
    ("Cotistas", "Cotistas"), ("Liquidez diária", "Liquidez_Diaria"),
    ("Vacância", "Vacancia"), ("Nº de imóveis", "Num_Imoveis"),
    ("% imóveis", "Pct_Imoveis"), ("% papel", "Pct_Papel"),
    ("% caixa", "Pct_Caixa"), ("% fundos", "Pct_Fundos"),
]

_TIPOS_SEM_IMOVEL = ("papel", "fof", "fundo de fundos")


def build_fii_ativo_context(
    ticker: str, *, user_question: str = "", dados=None,
    universo: pd.DataFrame | None = None, imoveis: pd.DataFrame | None = None,
) -> str:
    """Cadastro do fundo, carteira de imóveis quando houver e pares do mesmo tipo."""
    tk = str(ticker or "").strip().upper()
    blocos = ["ATIVO EM ANÁLISE (tela Seleção de FIIs → Busca de ativo):",
              f"  Ticker: {tk}"]
    if dados is not None:
        blocos.append(_series_block(dados, _FII_CAMPOS, "  Cadastro do fundo:"))
    try:
        tipo = str(dados.get("Tipo") or "").strip() if dados is not None else ""
    except Exception:
        tipo = ""
    if tipo.lower() in _TIPOS_SEM_IMOVEL:
        blocos.append(f"  Vacância e carteira de imóveis não se aplicam ao tipo {tipo}.")
    elif imoveis is not None and not imoveis.empty:
        blocos.append(f"  Carteira de imóveis: {len(imoveis)} ativos cadastrados.")
        col_reg = next((c for c in ("Região", "Regiao", "regiao") if c in imoveis.columns), None)
        if col_reg:
            regioes = imoveis[col_reg].dropna().astype(str).value_counts().head(6)
            blocos.append("  Regiões: " + "; ".join(
                f"{k}={int(v)}" for k, v in regioes.items()))
        if "Área_m2" in imoveis.columns:
            area = pd.to_numeric(imoveis["Área_m2"], errors="coerce").sum()
            blocos.append(f"  Área total cadastrada: {_fmt(area, 0)} m²")
    else:
        blocos.append("  Carteira de imóveis: não cadastrada no banco para este fundo.")

    if universo is not None and not universo.empty and "Ticker" in universo.columns:
        pares = universo[universo["Ticker"] != tk]
        if tipo and "Tipo" in pares.columns:
            mesmo = pares[pares["Tipo"].astype(str).str.strip().str.lower() == tipo.lower()]
            if not mesmo.empty:
                pares = mesmo
        if "Score" in pares.columns:
            pares = pares.sort_values("Score", ascending=False, na_position="last")
        cols = [c for c in ("Ticker", "Nome", "Tipo", "Segmento", "P/VP", "DY_12m", "Score")
                if c in pares.columns]
        if cols:
            titulo = f"\nPARES{' DO TIPO ' + tipo.upper() if tipo else ''} (maiores scores primeiro):"
            linhas = [titulo]
            for _, r in pares.head(10)[cols].iterrows():
                linhas.append("  " + " | ".join(f"{c}={_fmt(r.get(c))}" for c in cols))
            if len(linhas) > 1:
                blocos.append("\n".join(linhas))
    local_macro = _local_macro_block("fii", tk, tipo.lower())
    if local_macro:
        blocos.append("\n" + local_macro)
    return _cap("\n".join(blocos))
