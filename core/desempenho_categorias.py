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

# O gráfico abre só com estas; as outras ficam na legenda, a um clique.
# Seis linhas de saída poluíam a leitura (pedido do usuário, 31/10/2026).
INICIAIS = frozenset({"Ações", "ETFs Americanos", "Tesouro Direto"})


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
    # Passo sem ativo com preço nas duas pontas não foi medido: a data sai da
    # curva em vez de entrar como retorno zero. Sem nenhum passo medido não
    # há série -- dois ativos com uma foto cada, em datas diferentes, não
    # dizem retorno nenhum.
    r = num / den.where(den > 0) - 1
    if r.iloc[1:].notna().sum() == 0:
        return pd.Series(dtype=float)
    r.iloc[0] = 0.0
    r = r.dropna()
    return ((1 + r).cumprod() - 1) * 100


def quantidades_pelo_valor(precos: pd.DataFrame, valores: dict[str, float]) -> dict[str, float]:
    """Quantidade na unidade da série: valor de mercado de hoje ÷ último preço.

    Para a renda fixa, cuja posição de hoje pode vir de uma fonte que conta
    quantidade de outro jeito que a série escolhida (uma cota de R$ 1 contra
    uma nota de R$ 1.000): multiplicar a série por essa quantidade distorce
    o peso de cada ativo. Ativo sem preço ou sem valor fica de fora.
    """
    saida = {}
    for c in precos.columns:
        serie = pd.to_numeric(precos[c], errors="coerce").dropna()
        serie = serie[serie > 0]
        valor = float(valores.get(c) or 0)
        if not serie.empty and valor > 0:
            saida[c] = valor / float(serie.iloc[-1])
    return saida


def chave_unica_tesouro(ticker: str, nomes) -> str | None:
    """Chave da curva de uma posição do Tesouro; ``None`` se ambígua.

    A carteira agrupa pelo ticker, e "IPCA+ 2032" e "IPCA+ com Juros
    Semestrais 2032" chegam somados como TIPCA2032. Um PU só daria a uma parte
    da quantidade a trajetória de outro título, por isso o grupo fica fora --
    a mesma regra de ``core.investimentos._marcacao_pela_curva``.
    """
    from core.tesouro_curva import chave_da_curva

    chaves = {chave_da_curva(ticker, n) for n in (nomes or [None])}
    return next(iter(chaves)) if len(chaves) == 1 else None


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
    """PU de recompra diário de cada título, colunas pelo ticker da posição.

    ``posicoes``: ``{"ticker", "nomes"}``, com os nomes de todos os títulos que
    a carteira somou sob o ticker. Grupo ambíguo fica sem série.
    """
    from core.tesouro_curva import serie_titulo

    series = {}
    for p in posicoes:
        ticker = str(p.get("ticker") or "").strip()
        chave = chave_unica_tesouro(ticker, p.get("nomes"))
        if chave is None:
            continue
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
