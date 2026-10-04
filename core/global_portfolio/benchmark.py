"""Benchmark composto do Portfólio Global: a meta de alocação aplicada aos índices.

Sem benchmark, "a carteira rendeu 11% a.a." não diz se a seleção de ativos
somou alguma coisa à decisão de alocação entre classes -- que o usuário tomou
sozinho e que um fundo de índice reproduziria. O benchmark composto responde
isso: os MESMOS pesos por classe da meta, cada classe no seu índice.

* Empresas B3 -> BOVA11 (Ibovespa);
* FIIs -> XFIX11 (IFIX; o mesmo proxy que `core.market_read` já usa);
* Internacional -> IVVB11 (S&P 500 já em reais: o câmbio entra no índice, como
  entra nos ativos americanos convertidos por `returns.retornos_mensais`);
* Renda fixa -> CDI do arquivo publicado (`core.rentabilidade.ler_cdi_publicado`).

ETFs, não os índices: são o que o app já tem em `market.historical_prices`, e
o retorno total do ETF (com a taxa de administração dele, 0,1% a 0,3% a.a.) é
o que um investidor passivo de fato teria.

Camada pura: recebe preços e CDI, não lê nada. Coberto por
tests/test_global_black_litterman.py.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

import pandas as pd

PROXIES_BENCHMARK: dict[str, str] = {"b3": "BOVA11", "fii": "XFIX11", "us": "IVVB11"}
ROTULO_PROXY = {"b3": "Ibovespa (BOVA11)", "fii": "IFIX (XFIX11)",
                "us": "S&P 500 em R$ (IVVB11)", "renda_fixa": "CDI"}


def cdi_mensal(cdi_diario: Mapping[date, float]) -> pd.Series:
    """CDI composto por mês, em fração, indexado no último dia do mês.

    O arquivo publicado guarda a taxa em % ao dia útil (SGS 12): o mês é
    Π(1 + r/100) − 1 sobre os dias úteis dele. Índice no fim do mês para casar
    com o `resample("ME")` das séries de preço.
    """
    if not cdi_diario:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in cdi_diario.items()}).sort_index()
    mensal = (1.0 + s / 100.0).resample("ME").prod() - 1.0
    return mensal[s.resample("ME").count() > 0]


def retornos_dos_proxies(precos: pd.DataFrame) -> pd.DataFrame:
    """Retorno mensal por classe a partir dos preços mensais dos ETFs."""
    if not isinstance(precos, pd.DataFrame) or precos.empty:
        return pd.DataFrame()
    # fill_method=None: mês sem preço é ausente, nunca 0% (mesmo cuidado de
    # returns.retornos_mensais e factors.series_de_fatores).
    ret = precos.sort_index().pct_change(fill_method=None)
    saida = pd.DataFrame(index=ret.index)
    for classe, ticker in PROXIES_BENCHMARK.items():
        if ticker in ret.columns:
            saida[classe] = ret[ticker]
    return saida.dropna(how="all")


def benchmark_composto(pesos_classe: Mapping[str, float], ret_proxies: pd.DataFrame,
                       cdi_m: pd.Series) -> tuple[pd.Series, list[str]]:
    """(série mensal do benchmark, avisos).

    Só os meses em que TODA classe com peso tem retorno: renormalizar mês a
    mês sobre quem tem dado transformaria o benchmark em outro índice nos
    meses de buraco (antes de 2020 o XFIX11 não existe, e o "benchmark" viraria
    só Ibovespa + S&P sem dizer).
    """
    componentes = {}
    avisos = []
    for classe, peso in pesos_classe.items():
        if peso is None or float(peso) <= 0:
            continue
        if classe == "renda_fixa":
            serie = cdi_m
        elif isinstance(ret_proxies, pd.DataFrame) and classe in ret_proxies.columns:
            serie = ret_proxies[classe]
        else:
            serie = None
        if serie is None or serie.dropna().empty:
            avisos.append(f"{ROTULO_PROXY.get(classe, classe)} sem série: a classe "
                          f"({float(peso):.0%} da meta) ficou fora do benchmark.")
            continue
        componentes[classe] = (float(peso), serie)
    if not componentes:
        return pd.Series(dtype=float), avisos
    quadro = pd.DataFrame({c: s for c, (_p, s) in componentes.items()}).dropna(how="any")
    total = sum(p for p, _s in componentes.values())
    pesos = pd.Series({c: p / total for c, (p, _s) in componentes.items()})
    return quadro.mul(pesos, axis=1).sum(axis=1), avisos


def com_renda_fixa(serie_risco: pd.Series, renda_fixa: float | None,
                   cdi_m: pd.Series) -> pd.Series:
    """A carteira-modelo cobre só a parcela de risco: a renda fixa entra como
    CDI na mesma proporção da meta, para comparar com o benchmark de igual
    para igual. Sem renda fixa definida, a série volta como está."""
    if serie_risco is None or serie_risco.empty:
        return pd.Series(dtype=float)
    if not renda_fixa:
        return serie_risco
    q = pd.DataFrame({"r": serie_risco, "cdi": cdi_m}).dropna(how="any")
    rf = float(renda_fixa)
    return (1.0 - rf) * q["r"] + rf * q["cdi"]


def resumo(serie: pd.Series, bench: pd.Series) -> dict | None:
    """Retorno anualizado, volatilidade, excesso e tracking error na janela comum."""
    q = pd.DataFrame({"c": serie, "b": bench}).dropna(how="any")
    n = len(q)
    if n < 12:
        return None
    anos = n / 12.0

    def anual(x: pd.Series) -> float:
        return float((1.0 + x).prod() ** (1.0 / anos) - 1.0)

    ativo = q["c"] - q["b"]
    return {
        "meses": n,
        "inicio": q.index[0], "fim": q.index[-1],
        "retorno_anual": anual(q["c"]),
        "retorno_anual_bench": anual(q["b"]),
        "excesso_anual": anual(q["c"]) - anual(q["b"]),
        "vol_anual": float(q["c"].std(ddof=1) * math.sqrt(12)),
        "vol_anual_bench": float(q["b"].std(ddof=1) * math.sqrt(12)),
        "tracking_error": float(ativo.std(ddof=1) * math.sqrt(12)),
        "acumulado": (1.0 + q).cumprod() - 1.0,
    }
