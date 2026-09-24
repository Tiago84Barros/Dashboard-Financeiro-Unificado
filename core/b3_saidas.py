"""
core/b3_saidas.py — as empresas que saíram da B3, no formato que o motor lê.

O universo da seleção B3 vem de ``public.setores`` e do ``market.*``, que só
conhecem quem está listado HOJE. Uma reconstrução histórica sobre esse
universo só escolhe entre sobreviventes: quem quebrou (OGX, MMX, Americanas),
foi comprado (Fibria, Linx, Cielo) ou fechou capital nunca concorre, e a
carteira de 2017 é montada com o conhecimento de quem chegou a 2026.

``data/b3_saidas.json`` (gerado por ``scripts/gerar_b3_saidas.py`` a partir do
COTAHIST e da DFP da CVM, ver ``data_pipeline/market/b3_saidas.py``) traz,
para cada saída líquida curada, preço de retorno total mensal, volume mensal
e os múltiplos anuais com a data em que ficaram públicos. Este módulo só
converte esse arquivo para os formatos que ``views/portfolio_b3.py`` já usa.

Regra de uso (decisão AUD-2 do vault): empresa que saiu entra SÓ na
reconstrução histórica, e só nos anos em que estava listada. Nunca concorre
à carteira corrente nem à do próximo ano — ``vigente`` responde por isso.

Módulo puro: sem streamlit, sem banco.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

import pandas as pd

from core.b3_vigencia import REBAL_MONTH

log = logging.getLogger(__name__)

ARQUIVO = Path(__file__).resolve().parents[1] / "data" / "b3_saidas.json"

# Mesmo conjunto de colunas do histórico lido do market.* (core.market_read).
_METRICAS = ("P/L", "P/VP", "DY", "ROE", "ROA", "ROIC",
             "Margem_Liquida", "Margem_Operacional", "Endividamento_Total",
             "Liquidez_Corrente", "EV_EBIT", "P_FCO", "Payout")


@lru_cache(maxsize=2)
def _ler(caminho: str) -> dict:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def carregar(caminho: Path | str | None = None) -> dict:
    """Documento inteiro; arquivo ausente ou ilegível devolve ``{}``.

    Ausência não derruba a tela: sem o arquivo a reconstrução volta a ser a
    de antes (só sobreviventes), e quem chama declara isso.
    """
    try:
        return _ler(str(caminho or ARQUIVO))
    except (OSError, ValueError) as exc:
        log.warning("b3_saidas.json indisponível (%s)", exc)
        return {}


def empresas(doc: dict) -> list[dict]:
    return list((doc or {}).get("empresas") or [])


def tickers(doc: dict) -> list[str]:
    return [e["ticker"] for e in empresas(doc)]


def periodo_listado(doc: dict) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    """ticker -> (primeiro pregão, último pregão)."""
    return {
        e["ticker"]: (pd.Timestamp(e["primeiro_pregao"]), pd.Timestamp(e["ultimo_pregao"]))
        for e in empresas(doc)
    }


def vigente(periodo: tuple[pd.Timestamp, pd.Timestamp], ano: int,
            rebal_month: int = REBAL_MONTH) -> bool:
    """A empresa podia ser comprada no rebalanceamento de ``ano``?

    Precisa já negociar ANTES do 1º de abril e ainda negociar nele ou depois.
    Quem sai em fevereiro não é candidata em abril — o investidor da época já
    sabia da saída (OPA anunciada, pedido de recuperação publicado).
    """
    ini, fim = periodo
    corte = pd.Timestamp(int(ano), int(rebal_month), 1)
    return ini < corte <= fim


def historico_multiplos(doc: dict) -> dict[str, pd.DataFrame]:
    """ticker -> DataFrame no formato de ``load_multiplos_historico_batch``.

    Colunas ``Ticker``, ``Data`` (31/12 do exercício), as métricas e
    ``AvailableAt`` (recebimento da DFP na CVM). O saneamento por faixas fica
    com quem chama, pelo mesmo caminho dos tickers vivos.
    """
    out: dict[str, pd.DataFrame] = {}
    for e in empresas(doc):
        linhas = []
        for f in e.get("fundamentos") or []:
            linha = {"Ticker": e["ticker"],
                     "Data": pd.Timestamp(int(f["ano"]), 12, 31)}
            for m in _METRICAS:
                v = f.get(m)
                linha[m] = float(v) if v is not None else float("nan")
            linha["AvailableAt"] = pd.Timestamp(f["available_at"])
            linhas.append(linha)
        if linhas:
            out[e["ticker"]] = pd.DataFrame(linhas).sort_values("Data").reset_index(drop=True)
    return out


def valor_mercado_por_ano(doc: dict) -> dict[str, dict[int, float]]:
    """ticker -> {exercício: valor de mercado em 31/12}."""
    out: dict[str, dict[int, float]] = {}
    for e in empresas(doc):
        vm = {int(f["ano"]): float(f["valor_mercado"])
              for f in e.get("fundamentos") or [] if f.get("valor_mercado") is not None}
        out[e["ticker"]] = vm
    return out


def precos_mensais(doc: dict) -> pd.DataFrame:
    """Retorno total mensal, índice no último dia do mês (como o market.*)."""
    series = {}
    for e in empresas(doc):
        p = e.get("precos") or {}
        if not p:
            continue
        idx = pd.PeriodIndex(list(p), freq="M").to_timestamp(how="end").normalize()
        series[e["ticker"]] = pd.Series(list(p.values()), index=idx, dtype=float)
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).sort_index()


def volume_mensal(doc: dict) -> pd.DataFrame:
    """Colunas ``ticker``, ``mes`` (1º dia), ``financeiro`` — o formato de
    ``core.b3_universo_pit.elegiveis_por_ano``."""
    linhas = [
        (e["ticker"], pd.Period(m, freq="M").to_timestamp().date(), float(v))
        for e in empresas(doc) for m, v in (e.get("volume") or {}).items()
    ]
    return pd.DataFrame(linhas, columns=["ticker", "mes", "financeiro"])


def setores(doc: dict) -> pd.DataFrame:
    """Linhas no formato de ``load_setores`` (ticker, nome_empresa, taxonomia)."""
    return pd.DataFrame(
        [{"ticker": e["ticker"], "nome_empresa": e.get("nome") or e["ticker"],
          "SETOR": e["SETOR"], "SUBSETOR": e["SUBSETOR"], "SEGMENTO": e["SEGMENTO"]}
         for e in empresas(doc)],
        columns=["ticker", "nome_empresa", "SETOR", "SUBSETOR", "SEGMENTO"],
    )


def anos_de_historico(doc: dict, ano_atual: int) -> dict[str, int]:
    """O análogo de ``load_historico_anos`` para quem saiu.

    ``load_historico_anos`` conta os exercícios que a empresa viva tem HOJE.
    Contar só os que a empresa morta chegou a publicar transformaria o piso
    de "histórico DRE mínimo" num filtro de sobrevivência: com o padrão de
    10 anos, só entraria quem viveu até 2020, e o ponto do módulo se perderia.
    O critério equivalente é a idade do histórico: quantos exercícios ela
    teria hoje se seguisse listada, contados do primeiro publicado.
    """
    out = {}
    for e in empresas(doc):
        anos = [int(f["ano"]) for f in e.get("fundamentos") or []]
        out[e["ticker"]] = (int(ano_atual) - 1) - min(anos) + 1 if anos else 0
    return out


def elegibilidade(
    doc: dict,
    anos: list[int],
    min_mcap: float = 0.0,
    rebal_month: int = REBAL_MONTH,
) -> dict[str, set[int]]:
    """ticker -> anos de decisão em que podia concorrer.

    Listada no rebalanceamento (``vigente``) e, com piso de tamanho, valor de
    mercado em 31/12 do ano anterior acima dele. O piso dos vivos usa o valor
    de HOJE; aqui não há hoje, então vale o da época. Sem valor de mercado
    naquele exercício a empresa não concorre: o motor pontua com o exercício
    N-1, e sem ele não há o que pontuar de qualquer forma.
    """
    per = periodo_listado(doc)
    vm = valor_mercado_por_ano(doc)
    out: dict[str, set[int]] = {}
    for tk, p in per.items():
        ok = set()
        for ano in anos:
            if not vigente(p, ano, rebal_month):
                continue
            if min_mcap > 0 and vm.get(tk, {}).get(int(ano) - 1, 0.0) < min_mcap:
                continue
            ok.add(int(ano))
        out[tk] = ok
    return out


def limitacoes(doc: dict) -> list[str]:
    return list((doc or {}).get("limitacoes") or [])
