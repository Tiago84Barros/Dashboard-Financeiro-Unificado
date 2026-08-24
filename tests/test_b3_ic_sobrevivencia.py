"""A-119: a empresa que deslistou nao pode sumir do Rank-IC do B3.

`views/portfolio_b3.py` montava os pares (ano, score, retorno) com
`end_rows.iloc[-1] / start_rows.iloc[0]`: UMA data especifica de cada ponta.
Quem nao negociou naquele pregao exato saia por NaN no dropna -- e junto saia
quem DESLISTOU no meio do ano, que e o caso que importa.

Medido em 24/08/2026 sobre o painel real da B3 (1.089 tickers, 2015-2026):

    empresas-ano no Rank-IC   antes 5.958   depois 6.402   (+7,5%)

444 empresas-ano descartadas. E as recuperadas rendem MENOS que as que ja
entravam em 9 dos 11 anos -- o que sumia era predominantemente o perdedor.
Sobrevivencia dentro do proprio teste com que o app afirma que o score preve.
"""
import numpy as np
import pandas as pd
import pytest

from core.b3_pooled_evidence import pooled_yearly_ics, retornos_da_janela


def _janela(dados: dict[str, list[float | None]], datas: list[str]):
    return pd.DataFrame(dados, index=pd.to_datetime(datas))


def test_empresa_que_deslistou_no_meio_da_janela_continua_no_teste():
    start = _janela({"VIVE3": [10.0, 10.5], "QUEBROU3": [10.0, 9.0]},
                    ["2020-04-30", "2020-05-31"])
    # QUEBROU3 parou de negociar: so tem cotacao no primeiro mes da janela final.
    end = _janela({"VIVE3": [12.0, 13.0], "QUEBROU3": [2.0, None]},
                  ["2021-01-31", "2021-02-28"])

    r = retornos_da_janela(start, end).dropna()

    assert "QUEBROU3" in r.index, "a ultima cotacao negociada e a saida"
    assert r["QUEBROU3"] == pytest.approx(-0.80)
    assert r["VIVE3"] == pytest.approx(0.30)

    # O comportamento antigo, para contraste explicito.
    antigo = (end.iloc[-1] / start.iloc[0] - 1.0).dropna()
    assert "QUEBROU3" not in antigo.index


def test_ausencia_num_unico_pregao_nao_elimina_a_empresa():
    start = _janela({"A3": [None, 10.0], "B3X": [20.0, 21.0]},
                    ["2020-04-30", "2020-05-31"])
    end = _janela({"A3": [11.0, 12.0], "B3X": [22.0, 23.0]},
                  ["2021-01-31", "2021-02-28"])

    r = retornos_da_janela(start, end).dropna()
    assert set(r.index) == {"A3", "B3X"}
    assert r["A3"] == pytest.approx(0.20)   # 12/10 - 1, primeira cotacao valida


def test_preco_inicial_invalido_continua_fora():
    """Preco zero ou negativo nao e cotacao: vira NaN, nao um retorno infinito."""
    start = _janela({"ZERO3": [0.0], "OK3": [10.0]}, ["2020-04-30"])
    end = _janela({"ZERO3": [5.0], "OK3": [11.0]}, ["2021-01-31"])

    r = retornos_da_janela(start, end)
    assert pd.isna(r["ZERO3"])
    assert r["OK3"] == pytest.approx(0.10)


def test_janela_vazia_devolve_none():
    vazio = pd.DataFrame()
    cheio = _janela({"A3": [10.0]}, ["2020-04-30"])
    assert retornos_da_janela(vazio, cheio) is None
    assert retornos_da_janela(cheio, vazio) is None
    assert retornos_da_janela(None, cheio) is None


def test_o_perdedor_recuperado_muda_o_rank_ic():
    """O ponto da correcao: o par que voltou entra no teste de poder preditivo."""
    # Score alto para quem quebrou => o score errou, e o IC tem de sentir isso.
    pares_sem = [(2020, 90.0, 0.30), (2020, 50.0, 0.10), (2020, 10.0, 0.05),
                 (2020, 70.0, 0.20), (2020, 30.0, 0.08)]
    pares_com = pares_sem + [(2020, 95.0, -0.80)]

    ic_sem = pooled_yearly_ics(pares_sem, min_ativos=5)[2020]
    ic_com = pooled_yearly_ics(pares_com, min_ativos=5)[2020]

    assert ic_sem == pytest.approx(1.0)
    assert ic_com < ic_sem, "apagar o erro do score inflava a evidencia"
