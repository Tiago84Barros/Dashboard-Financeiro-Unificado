"""A-120: "retorno 12m" tem de cobrir 12 meses, nao 12 observacoes.

`_price_metrics` usava `s.tail(12)`: as 12 ultimas OBSERVACOES. Com buraco na
serie a janela estica e o rotulo nao muda. Medido em 24/08/2026 no painel real
da B3 (1.087 tickers): 7 casos (0,6%), mas FSTU11 exibia 76 MESES rotulados
"retorno 12m" (-56%) e PSVM11, 33 meses.

O lado oposto fecha junto: uma serie com 2 pontos cobrindo 1 mes daria um
retorno MENSAL rotulado "12m". A janela precisa cobrir ao menos 10 meses --
abaixo disso "nao ha 12 meses de historico" e resposta melhor que um retorno
curto com o rotulo errado.
"""
import numpy as np
import pandas as pd
import pytest

from views.portfolio_b3 import _price_metrics


def _precos(datas: list[str], valores: list[float], ticker: str = "XPTO3"):
    return pd.DataFrame({ticker: valores}, index=pd.to_datetime(datas))


def test_serie_com_buraco_nao_estica_a_janela_de_doze_meses():
    # Duas cotacoes em 2019 e depois um ano completo de 2025: `tail(12)`
    # abrangeria 6 anos e chamaria isso de "retorno 12m".
    datas = ["2019-01-31", "2019-02-28"] + [
        f"2025-{m:02d}-28" for m in range(1, 13)]
    valores = [100.0, 100.0] + [10.0 * (1.0 + i * 0.01) for i in range(12)]
    m = _price_metrics(_precos(datas, valores), "XPTO3")

    # 12 meses de 2025: de 10,00 a 11,10 -> +11%. Nao os -89% de 2019 ate hoje.
    assert m["ret_12m"] == pytest.approx(0.10, abs=0.02)


def test_historico_curto_demais_nao_vira_retorno_de_doze_meses():
    m = _price_metrics(
        _precos(["2025-07-31", "2025-08-31"], [10.0, 11.0]), "XPTO3")
    assert np.isnan(m["ret_12m"]), "1 mes de historico nao e retorno de 12 meses"


def test_serie_mensal_normal_continua_medindo_doze_meses():
    datas = [f"2024-{m:02d}-28" for m in range(8, 13)] + \
            [f"2025-{m:02d}-28" for m in range(1, 9)]
    valores = [100.0] + [100.0] * 11 + [141.9]
    m = _price_metrics(_precos(datas, valores), "XPTO3")
    assert m["ret_12m"] == pytest.approx(0.419, abs=0.001)


def test_ticker_ausente_devolve_nan_sem_estourar():
    m = _price_metrics(_precos(["2025-01-31"], [10.0]), "NAOEXISTE3")
    assert np.isnan(m["ret_12m"])
