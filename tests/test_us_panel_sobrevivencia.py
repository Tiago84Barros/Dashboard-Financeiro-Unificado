"""A-116/A-117: o painel do backtest EUA nao pode apagar quem quebrou.

`build_annual_panel` fazia `if fut.empty: continue`. A acao que parou de
negociar sumia do painel -- vies de sobrevivencia puro, e o unico defeito desta
auditoria que enviesa o resultado exibido PARA CIMA. Medido em 24/08/2026 com
uma cesta de duas acoes, uma +30% e outra que caiu 80% e deslistou: o painel
devolvia +30,0% de retorno medio contra os -25,0% que aconteceram.

`fut.iloc[0]` tambem nao tinha teto: o primeiro preco DEPOIS do alvo, ainda que
7 anos depois, virava "retorno de 12 meses" (+300% medidos). Idem em
`forward_returns_from_monthly`, onde `shift(-1)` devolve a proxima LINHA e um
buraco na serie punha +100% de 11 meses rotulado como retorno mensal.
"""
import pandas as pd
import pytest

from core.us_backtest import walk_forward
from data_pipeline.us.scoring_history import (
    build_annual_panel, forward_returns_from_monthly)


def _meses(sym, pares):
    return pd.DataFrame([{"symbol": sym, "month_end": d, "adjusted_close": p}
                         for d, p in pares])


def _vint(simbolos, as_of="2020-06-30", score=75.0):
    return pd.DataFrame({"as_of_date": [as_of] * len(simbolos),
                         "symbol": list(simbolos),
                         "score": [score] * len(simbolos)})


def test_acao_que_deslistou_nao_pode_sumir_do_painel():
    monthly = pd.concat([
        _meses("VIVA", [("2020-06-30", 100.0), ("2021-06-30", 130.0)]),
        _meses("QUEBROU", [("2020-06-30", 100.0), ("2020-09-30", 20.0)]),
    ])
    p = build_annual_panel(_vint(["VIVA", "QUEBROU"]), monthly, horizon_months=12)

    assert set(p["symbol"]) == {"VIVA", "QUEBROU"}, "o perdedor tem de contar"
    quebrou = p[p["symbol"] == "QUEBROU"].iloc[0]
    assert quebrou["fwd_return"] == pytest.approx(-0.80)
    assert bool(quebrou["censored"]) is True
    assert bool(p[p["symbol"] == "VIVA"].iloc[0]["censored"]) is False
    assert p.attrs["n_censored"] == 1
    # E o efeito que motivou a correcao: a media deixa de ser so o vencedor.
    assert p["fwd_return"].mean() == pytest.approx(-0.25)


def test_dado_que_acabou_nao_e_acao_que_quebrou():
    """Na borda do dataset o retorno e inobservavel -- a linha sai, sem inventar."""
    monthly = _meses("A", [("2020-06-30", 100.0)])
    p = build_annual_panel(_vint(["A"]), monthly, horizon_months=12)
    assert p.empty
    assert p.attrs["n_inobservavel"] == 1
    assert p.attrs["n_censored"] == 0


def test_preco_muito_alem_do_alvo_nao_e_retorno_de_doze_meses():
    monthly = pd.concat([
        _meses("HIATO", [("2020-06-30", 100.0), ("2027-06-30", 400.0)]),
        _meses("REGULAR", [("2020-06-30", 100.0), ("2021-06-30", 110.0),
                           ("2027-06-30", 300.0)]),
    ])
    p = build_annual_panel(_vint(["HIATO", "REGULAR"]), monthly, horizon_months=12)

    # HIATO tem cotacao na BORDA do dataset: nao da para distinguir deslistagem
    # de buraco no dado, entao o retorno de 12 meses e inobservavel -- a linha
    # sai e e contada. O que nao pode acontecer e os +300% de 84 meses entrarem
    # no painel rotulados como retorno de 12 meses.
    assert "HIATO" not in set(p["symbol"])
    assert p.attrs["n_inobservavel"] == 1
    assert p[p["symbol"] == "REGULAR"].iloc[0]["fwd_return"] == pytest.approx(0.10)


def test_tolerancia_aceita_o_mes_seguinte_ao_alvo():
    """Alvo em jun; o pregao mensal so tem jul. Isso e o horizonte, nao censura."""
    monthly = pd.concat([
        _meses("A", [("2020-06-30", 100.0), ("2021-07-31", 120.0)]),
        _meses("B", [("2020-06-30", 50.0), ("2021-07-31", 50.0),
                     ("2022-01-31", 90.0)]),
    ])
    p = build_annual_panel(_vint(["A", "B"]), monthly, horizon_months=12)
    linha = p[p["symbol"] == "A"].iloc[0]
    assert linha["fwd_return"] == pytest.approx(0.20)
    assert bool(linha["censored"]) is False


def test_buraco_na_serie_mensal_nao_vira_retorno_mensal():
    f = forward_returns_from_monthly(
        _meses("GAP", [("2020-01-31", 100.0), ("2020-12-31", 200.0)]))
    assert f.empty, "11 meses rotulados como 1 mes contaminam a vol mensal"

    ok = forward_returns_from_monthly(
        _meses("OK", [("2020-01-31", 100.0), ("2020-02-29", 110.0)]))
    assert len(ok) == 1 and ok.iloc[0]["fwd_return"] == pytest.approx(0.10)


def test_backtest_declara_quanto_repousa_sobre_saida_forcada():
    monthly = pd.concat([
        _meses("VIVA", [("2020-06-30", 100.0), ("2021-06-30", 130.0)]),
        _meses("QUEBROU", [("2020-06-30", 100.0), ("2020-09-30", 20.0)]),
    ])
    p = build_annual_panel(_vint(["VIVA", "QUEBROU"]), monthly, horizon_months=12)
    res = walk_forward(p, top_n=2, weighting="equal", periods_per_year=1)

    assert res["ok"] is True
    assert res["censura"]["n_censurado"] == 1
    assert res["censura"]["fracao_censurada"] == pytest.approx(0.5)
