"""
core/desempenho_categorias.py
Retorno comparado das categorias da carteira (Análise › Visão Geral).

Cada categoria vira uma cesta das posições ATUAIS, com a quantidade de hoje,
e o retorno da cesta é encadeado dia a dia (ou foto a foto): no passo de
``t-1`` para ``t`` entram só os ativos com preço nas duas datas, pesados pelo
valor em ``t-1``. Ativo que começa a ter preço mais tarde passa a contar dali
em diante, sem degrau na curva. Cada categoria começa onde o seu dado começa:
as linhas não são alinhadas no tempo, de propósito.

É retorno de PREÇO das posições de hoje, não rentabilidade da conta: aporte,
venda e o histórico de quantidades ficam de fora. Fontes por categoria:

- Ações, FIIs, Empresas Americanas, ETFs Americanos: fechamento diário
  (yfinance ajustado; na falha, ``asset_quotes`` sem ajuste). As duas
  categorias americanas são convertidas para BRL pelo USDBRL do dia quando a
  série de câmbio existe; sem ela ficam em USD e a tela diz.
- Tesouro Direto: PU de recompra diário da curva oficial
  (``tesouro_market_rates``). Título com cupom cai no pagamento do cupom.
- Renda Fixa: valor unitário das fotos de custódia
  (``portfolio_position_snapshots``), ``market_value / quantity``, de uma
  fonte só por ativo -- fontes diferentes contam quantidade de jeitos
  diferentes, e trocar de fonte no meio inventaria retorno.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from core.investimentos import _CLASS_LABEL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Categoria:
    nome: str
    classes: frozenset[str]   # rótulos de ``posicao["classe"]``
    cor: str
    fonte: str                # "mercado" | "tesouro" | "fotos"
    dolar: bool = False


# Rótulos de classe lidos de ``core.investimentos._CLASS_LABEL``, o mesmo
# dicionário que rotula ``posicao["classe"]``: renomear lá não esvazia aqui.
_ROTULO = _CLASS_LABEL.get

CATEGORIAS: tuple[Categoria, ...] = (
    Categoria("Ações", frozenset({_ROTULO("stock")}), "#4C9BE8", "mercado"),
    Categoria("FIIs", frozenset({_ROTULO("fii")}), "#E84C9B", "mercado"),
    Categoria("Empresas Americanas", frozenset({_ROTULO("stock_us")}), "#F6C90E",
              "mercado", dolar=True),
    Categoria("ETFs Americanos", frozenset({_ROTULO("etf_intl")}), "#63CAB7",
              "mercado", dolar=True),
    Categoria("Tesouro Direto", frozenset({_ROTULO("tesouro")}), "#2ECC71", "tesouro"),
    Categoria("Renda Fixa", frozenset({_ROTULO("renda_fixa")}), "#A855F7", "fotos"),
)

POR_NOME = {c.nome: c for c in CATEGORIAS}


def posicoes_por_categoria(posicoes: list[dict]) -> dict[str, list[dict]]:
    """``{categoria: [posições com quantidade > 0]}``, só as que têm posição."""
    saida: dict[str, list[dict]] = {}
    for cat in CATEGORIAS:
        lista = [p for p in posicoes
                 if p.get("classe") in cat.classes
                 and str(p.get("ticker") or "").strip()
                 and float(p.get("quantidade") or 0) > 0]
        if lista:
            saida[cat.nome] = lista
    return saida


def inicio_do_periodo(dias: int | None, hoje: date | None = None) -> date:
    """Primeiro dia do período; ``dias=None`` é o ano corrente (YTD)."""
    hoje = hoje or date.today()
    if dias is None:
        return date(hoje.year, 1, 1)
    return hoje - timedelta(days=int(dias))


def retorno_encadeado(precos: pd.DataFrame, quantidades: dict[str, float]) -> pd.Series:
    """Retorno acumulado (%) da cesta, primeiro ponto = 0.

    ``precos``: índice de datas, uma coluna por ticker. Buraco no meio da série
    de um ativo (feriado de outra praça, pregão sem negócio) herda o último
    preço; antes do primeiro e depois do último preço o ativo não conta.
    """
    cols = [c for c in precos.columns if float(quantidades.get(c) or 0) > 0]
    if not cols:
        return pd.Series(dtype=float)
    p = precos[cols].apply(pd.to_numeric, errors="coerce").sort_index()
    p = p.where(p > 0)
    p = p.ffill().where(p.bfill().notna())   # preenche só por dentro da série
    p = p.dropna(how="all")
    if p.empty:
        return pd.Series(dtype=float)

    q = pd.Series({c: float(quantidades[c]) for c in cols})
    valor = p * q
    anterior = valor.shift()
    ambos = valor.notna() & anterior.notna()
    num = valor.where(ambos).sum(axis=1)
    den = anterior.where(ambos).sum(axis=1)
    r = (num / den.where(den > 0) - 1).fillna(0.0)
    r.iloc[0] = 0.0
    return ((1 + r).cumprod() - 1) * 100


def _sem_fuso(indice) -> pd.DatetimeIndex:
    idx = pd.to_datetime(indice)
    return idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx


def converter_para_brl(precos_usd: pd.DataFrame, usdbrl: pd.Series) -> pd.DataFrame:
    """Preço em USD × câmbio do dia (o último conhecido até aquele dia)."""
    if precos_usd.empty or usdbrl is None or usdbrl.dropna().empty:
        return pd.DataFrame()
    fx = pd.to_numeric(usdbrl, errors="coerce").dropna()
    fx.index = _sem_fuso(fx.index)
    fx = fx[~fx.index.duplicated(keep="last")].sort_index()
    idx = _sem_fuso(precos_usd.index)
    base = precos_usd.copy()
    base.index = idx
    fx_no_dia = fx.reindex(fx.index.union(idx)).ffill().reindex(idx)
    return base.mul(fx_no_dia, axis=0).where(fx_no_dia.notna(), axis=0)


def unitario_de_fonte_unica(linhas: pd.DataFrame) -> pd.DataFrame:
    """Valor unitário por data e ticker, de uma única fonte por ticker.

    ``linhas``: colunas ``ticker, data, fonte, preco``. Para cada ticker fica a
    fonte com mais fotos (empate: a de foto mais recente); na mesma data e
    fonte, a média.
    """
    if linhas is None or linhas.empty:
        return pd.DataFrame()
    df = linhas.copy()
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    df["preco"] = pd.to_numeric(df["preco"], errors="coerce")
    df = df.dropna(subset=["ticker", "data", "preco"])
    df = df[df["preco"] > 0]
    if df.empty:
        return pd.DataFrame()
    resumo = (df.groupby(["ticker", "fonte"])["data"]
                .agg(["count", "max"]).reset_index()
                .sort_values(["ticker", "count", "max"], ascending=[True, False, False]))
    escolhida = resumo.drop_duplicates("ticker").set_index("ticker")["fonte"]
    df = df[df["fonte"] == df["ticker"].map(escolhida)]
    return (df.groupby(["data", "ticker"])["preco"].mean()
              .unstack("ticker").sort_index())


# ── Leitura do banco ─────────────────────────────────────────────────────────

def precos_tesouro(engine, posicoes: list[dict], desde: date) -> pd.DataFrame:
    """PU de recompra diário de cada título, colunas pelo ticker da posição."""
    from core.tesouro_curva import chave_da_curva, serie_titulo

    series = {}
    for p in posicoes:
        ticker = str(p.get("ticker") or "").strip()
        chave = chave_da_curva(ticker, p.get("nome"))
        df = serie_titulo(engine, chave, desde)
        if df.empty:
            continue
        pu = pd.to_numeric(df["sell_pu"], errors="coerce").fillna(
            pd.to_numeric(df["base_pu"], errors="coerce"))
        s = pd.Series(pu.values, index=pd.to_datetime(df["base_date"])).dropna()
        s = s[s > 0]
        if not s.empty:
            series[ticker] = s[~s.index.duplicated(keep="last")]
    return pd.DataFrame(series).sort_index() if series else pd.DataFrame()


_SQL_UNITARIO_FOTOS = """
    SELECT a.ticker, pps.report_date AS data, pps.source_table AS fonte,
           pps.market_value::float8 / pps.quantity::float8 AS preco
      FROM portfolio_position_snapshots pps
      JOIN assets a ON a.id = pps.asset_id
     WHERE pps.user_id = :uid
       AND a.ticker IN :tickers
       AND pps.quantity > 0
       AND pps.market_value > 0
       AND pps.report_date >= :desde
"""


def precos_renda_fixa(engine, owner: str, tickers: list[str], desde: date) -> pd.DataFrame:
    """Valor unitário das fotos de custódia; vazio se não houver."""
    if engine is None or not owner or not tickers:
        return pd.DataFrame()
    from sqlalchemy import bindparam, text

    try:
        with engine.connect() as conn:
            linhas = conn.execute(
                text(_SQL_UNITARIO_FOTOS).bindparams(bindparam("tickers", expanding=True)),
                {"uid": owner, "tickers": sorted(set(tickers)), "desde": desde},
            ).mappings().all()
    except Exception as exc:
        logger.info("desempenho_categorias: fotos de renda fixa indisponíveis (%s)",
                    type(exc).__name__)
        return pd.DataFrame()
    return unitario_de_fonte_unica(pd.DataFrame(linhas))
