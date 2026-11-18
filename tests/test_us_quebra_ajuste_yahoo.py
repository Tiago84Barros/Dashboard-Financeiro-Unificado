"""Quebra de ajuste do Yahoo nos grupamentos de 2026.

Por que estes testes existem: nos grupamentos de setembro e outubro de 2026, o
Yahoo aplicou o fator só às barras do ano do split. Tudo até 31/12/2025 ficou
sem ajuste, e a série dá um salto falso de 1/razão em 02/01/2026 (BRTX 20,8x
para um 1:20, OPTT 32x para um 1:30). Num backtest mensal isso vira um retorno
de +2.000% em janeiro, que infla a carteira de peso igual usada de régua.

O Yahoo também demora alguns dias para ajustar um split novo: a quebra cai no
próprio dia do split (VSEE 90x num 1:80), às vezes com a barra do dia vazia.

A regra procura o salto só nesses dois lugares e só em split de fator 2 ou
mais. Varrida sobre os 1.624 ativos com split grande do armazém em 18/11/2026,
disparou em 17, todos quebras de verdade.
"""
import math

import pandas as pd
import pytest

from data_pipeline.us import ingest
from data_pipeline.us.prices_yf import YFinanceProvider, corrigir_quebra_de_ajuste


def _barras(precos: dict[str, float], volume: int = 1000) -> list[dict]:
    return [{"date": d, "open": p, "high": p, "low": p, "close": p,
             "adjClose": p, "volume": volume} for d, p in sorted(precos.items())]


def _split(d: str, razao: float) -> dict:
    return {"date": d, "numerator": razao, "denominator": 1.0}


# BRTX real: 1:20 em 08/09/2026; o Yahoo deixou 2025 sem ajuste.
BRTX = {"2025-12-30": 0.21, "2025-12-31": 0.206, "2026-01-02": 4.278,
        "2026-01-05": 4.30, "2026-09-04": 3.90, "2026-09-08": 3.85,
        "2026-09-09": 3.80}
SPLIT_BRTX = [_split("2026-09-08", 0.05)]


def test_quebra_na_virada_do_ano_recebe_o_fator():
    out = corrigir_quebra_de_ajuste(_barras(BRTX), SPLIT_BRTX)
    por = {b["date"]: b for b in out}
    assert por["2025-12-31"]["adjClose"] == pytest.approx(0.206 * 20)
    assert por["2025-12-31"]["close"] == pytest.approx(0.206 * 20)
    assert por["2025-12-31"]["open"] == pytest.approx(0.206 * 20)
    assert por["2025-12-31"]["volume"] == 50      # 1000 ações viram 50
    # do ano do split em diante, nada muda
    assert por["2026-01-02"]["adjClose"] == pytest.approx(4.278)
    assert por["2026-09-09"]["volume"] == 1000
    salto = por["2026-01-02"]["adjClose"] / por["2025-12-31"]["adjClose"]
    assert abs(math.log(salto)) < 0.1


def test_quebra_no_dia_do_split_recebe_o_fator():
    # Yahoo ainda sem ajustar: 0,02 -> 1,80 no dia de um 1:80.
    precos = {"2026-09-22": 0.019, "2026-09-23": 0.020, "2026-09-24": 1.80,
              "2026-09-25": 2.28}
    out = corrigir_quebra_de_ajuste(_barras(precos), [_split("2026-09-24", 0.0125)])
    assert out[1]["close"] == pytest.approx(0.020 * 80)
    assert out[2]["close"] == pytest.approx(1.80)


def test_dia_do_split_vazio_usa_o_primeiro_pregao_seguinte():
    barras = _barras({"2026-11-12": 0.208, "2026-11-16": 4.0, "2026-11-17": 4.1})
    barras.insert(1, {"date": "2026-11-13", "open": None, "high": None,
                      "low": None, "close": None, "adjClose": None, "volume": None})
    out = corrigir_quebra_de_ajuste(barras, [_split("2026-11-13", 0.05)])
    assert out[0]["close"] == pytest.approx(0.208 * 20)
    assert out[1]["close"] is None


def test_serie_ja_ajustada_sai_igual():
    precos = {"2025-12-31": 4.12, "2026-01-02": 4.278, "2026-09-04": 3.90,
              "2026-09-08": 3.85}
    barras = _barras(precos)
    assert corrigir_quebra_de_ajuste(barras, SPLIT_BRTX) == barras


def test_queda_real_que_nao_bate_com_o_split_fica():
    # Queda de 50% na virada do ano, num ano com grupamento 1:20: não é quebra.
    precos = {"2025-12-31": 8.0, "2026-01-02": 4.0, "2026-09-08": 3.85}
    barras = _barras(precos)
    assert corrigir_quebra_de_ajuste(barras, SPLIT_BRTX) == barras


def test_salto_fora_dos_dois_lugares_fica():
    # 20x em março não é a virada do ano nem o dia do split.
    precos = {"2026-03-10": 0.2, "2026-03-11": 4.0, "2026-09-08": 3.85}
    barras = _barras(precos)
    assert corrigir_quebra_de_ajuste(barras, SPLIT_BRTX) == barras


def test_split_pequeno_nao_dispara():
    # 3:2 (fator 1,5): uma queda de 33% na virada é movimento comum.
    precos = {"2025-12-31": 15.0, "2026-01-02": 10.0, "2026-06-01": 10.0}
    barras = _barras(precos)
    assert corrigir_quebra_de_ajuste(barras, [_split("2026-06-01", 1.5)]) == barras


def test_dois_splits_do_ano_esquecidos_juntos():
    # 1:5 em março e 1:40 em setembro, os dois só no ano: salto de 200x.
    precos = {"2025-12-31": 0.2, "2026-01-02": 40.0, "2026-03-02": 41.0,
              "2026-09-02": 40.0}
    out = corrigir_quebra_de_ajuste(
        _barras(precos), [_split("2026-03-02", 0.2), _split("2026-09-02", 0.025)])
    assert out[0]["close"] == pytest.approx(0.2 * 200)


def test_um_dos_splits_do_ano_aplicado_outro_esquecido():
    # FRGT: o 1:5 de maio foi aplicado; o 1:10 de outubro, só no ano.
    precos = {"2025-12-31": 0.4, "2026-01-02": 4.38, "2026-05-18": 4.0,
              "2026-10-19": 4.0}
    out = corrigir_quebra_de_ajuste(
        _barras(precos), [_split("2026-05-18", 0.2), _split("2026-10-19", 0.1)])
    assert out[0]["close"] == pytest.approx(0.4 * 10)


def test_idempotente():
    uma = corrigir_quebra_de_ajuste(_barras(BRTX), SPLIT_BRTX)
    assert corrigir_quebra_de_ajuste(uma, SPLIT_BRTX) == uma


def test_nao_altera_a_lista_de_entrada():
    barras = _barras(BRTX)
    copia = [dict(b) for b in barras]
    corrigir_quebra_de_ajuste(barras, SPLIT_BRTX)
    assert barras == copia


class _TickerFake:
    def __init__(self, precos, splits):
        self._precos, self._splits = precos, splits

    def history(self, start=None, end=None, **_):
        datas = [d for d in sorted(self._precos) if start is None or d >= start]
        idx = pd.DatetimeIndex(datas)
        p = [self._precos[d] for d in datas]
        return pd.DataFrame({
            "Open": p, "High": p, "Low": p, "Close": p, "Adj Close": p,
            "Volume": [1000] * len(p), "Dividends": [0.0] * len(p),
            "Stock Splits": [self._splits.get(d, 0.0) for d in datas],
        }, index=idx)


def _provider():
    t = _TickerFake(BRTX, {"2026-09-08": 0.05})
    return YFinanceProvider(ticker_factory=lambda _s: t, sleep_fn=lambda _x: None)


def test_provider_entrega_a_serie_corrigida():
    out = _provider().get_prices_daily("BRTX")
    assert out[1]["adjClose"] == pytest.approx(0.206 * 20)
    assert _provider().get_splits("BRTX") == [
        {"date": "2026-09-08", "numerator": 0.05, "denominator": 1.0}]


@pytest.mark.parametrize("inicio", ["2025-12-30", "2026-01-02", "2026-09-04"])
def test_janela_e_historico_completo_concordam(inicio):
    """A janela incremental compara o guardado (do histórico completo corrigido)
    com o que chegou. Se a correção dependesse da janela, cada passagem diária
    refaria o histórico do símbolo, ou pior, gravaria a quebra de volta."""
    completo = {b["date"]: b["adjClose"] for b in _provider().get_prices_daily("BRTX")}
    janela = _provider().get_prices_daily("BRTX", start=inicio)
    for b in janela:
        assert b["adjClose"] == pytest.approx(completo[b["date"]])
    guardado = {d: v for d, v in completo.items() if d >= inicio}
    assert ingest.passado_retroajustado(guardado, janela) is None
