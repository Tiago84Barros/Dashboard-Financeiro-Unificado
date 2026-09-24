"""Empresas que saíram da B3 na reconstrução histórica (viés de sobrevivência)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import core.b3_saidas as saidas
import views.portfolio_b3 as portfolio
from core.b3_safras import _preco_nas_pontas


def _doc() -> dict:
    return {
        "versao": "t",
        "limitacoes": ["x"],
        "empresas": [{
            "ticker": "MORT3", "nome": "Morta SA",
            "SETOR": "S", "SUBSETOR": "SS", "SEGMENTO": "SEG",
            "primeiro_pregao": "2012-05-02", "ultimo_pregao": "2019-02-15",
            "anos_dfp": [2012, 2013], "precos": {"2018-11": 10.0, "2018-12": 9.0,
                                                  "2019-01": 8.0, "2019-02": 7.5},
            "volume": {"2018-12": 5e7, "2019-01": 4e7},
            "fundamentos": [
                {"ano": 2016, "available_at": "2017-03-20", "valor_mercado": 5e8, "ROE": 0.1},
                {"ano": 2017, "available_at": "2018-03-20", "valor_mercado": 2e9,
                 "ROE": 0.2, "P/L": 9.0},
            ],
        }],
    }


class TestLeitor:
    def test_vigencia_exige_estar_listada_no_rebalanceamento(self):
        per = saidas.periodo_listado(_doc())["MORT3"]
        # Saiu em fevereiro de 2019: o investidor de abril de 2019 já sabia.
        assert saidas.vigente(per, 2018)
        assert not saidas.vigente(per, 2019)
        # Estreou em maio de 2012: não existia no rebalanceamento de abril.
        assert not saidas.vigente(per, 2012)
        assert saidas.vigente(per, 2013)

    def test_piso_de_tamanho_usa_o_valor_de_mercado_da_epoca(self):
        anos = list(range(2013, 2021))
        assert saidas.elegibilidade(_doc(), anos)["MORT3"] == set(range(2013, 2019))
        # vm de 2016 = 5e8 barra 2017; vm de 2017 = 2e9 libera 2018; sem vm, fora.
        assert saidas.elegibilidade(_doc(), anos, min_mcap=1e9)["MORT3"] == {2018}

    def test_precos_no_ultimo_dia_do_mes_como_o_market(self):
        p = saidas.precos_mensais(_doc())
        assert list(p.index) == list(pd.to_datetime(
            ["2018-11-30", "2018-12-31", "2019-01-31", "2019-02-28"]))
        assert p["MORT3"].iloc[-1] == 7.5

    def test_volume_no_formato_do_universo_pit(self):
        v = saidas.volume_mensal(_doc())
        assert list(v.columns) == ["ticker", "mes", "financeiro"]
        assert v["mes"].iloc[0] == pd.Timestamp("2018-12-01").date()

    def test_historico_no_formato_do_batch(self):
        h = saidas.historico_multiplos(_doc())["MORT3"]
        assert {"Ticker", "Data", "ROE", "P/L", "AvailableAt"} <= set(h.columns)
        assert h["Data"].iloc[-1] == pd.Timestamp(2017, 12, 31)
        assert h["AvailableAt"].iloc[-1] == pd.Timestamp("2018-03-20")
        assert np.isnan(h["P/L"].iloc[0])

    def test_historico_minimo_conta_idade_e_nao_sobrevivencia(self):
        # Contar só os 2 exercícios publicados barraria a morta no piso de
        # 10 anos — o piso viraria filtro de sobrevivência.
        assert saidas.anos_de_historico(_doc(), 2026)["MORT3"] == 2025 - 2016 + 1

    def test_arquivo_ausente_devolve_vazio(self, tmp_path):
        assert saidas.carregar(tmp_path / "nao_existe.json") == {}


def test_arquivo_versionado_sem_saida_nao_curada():
    doc = json.loads(saidas.ARQUIVO.read_text(encoding="utf-8"))
    assert doc["saidas_nao_curadas"] == {}
    assert doc["empresas"]
    for e in doc["empresas"]:
        assert e["SETOR"] and e["SUBSETOR"] and e["SEGMENTO"], e["ticker"]
        assert pd.Timestamp(e["ultimo_pregao"]) >= pd.Timestamp("2016-01-01")


def test_simulador_reinveste_a_posicao_de_quem_saiu():
    idx = pd.date_range("2020-01-31", periods=12, freq="ME")
    precos = pd.DataFrame({
        "MORT3": [10.0] * 6 + [np.nan] * 6,
        "VIVE3": [10.0] * 11 + [20.0],
    }, index=idx)
    lids = {2020: ["MORT3", "VIVE3"]}
    pesos = {2020: {"MORT3": 0.5, "VIVE3": 0.5}}
    det: dict = {}
    est, _selic, _ew, _c = portfolio._simular_seg_backtest(
        precos, lids, pesos, aporte=100.0, taxa_selic_aa=0.0, selic_macro={},
        details_out=det,
    )
    # Em julho a posição na morta vira caixa e vai para a viva: os 1.100
    # aportados até novembro dobram em dezembro, mais o aporte de dezembro.
    # Congelada, metade ficaria rendendo zero (1.650 + 100).
    assert est == pytest.approx(2300.0)
    assert det["pending_cash_strategy"] == 0.0


def test_ponta_final_dispensada_so_para_quem_saiu_na_janela():
    s = pd.Series([10.0, 11.0, 12.0],
                  index=pd.to_datetime(["2020-03-31", "2020-04-30", "2020-05-31"]))
    ini, fim = pd.Timestamp("2020-04-01"), pd.Timestamp("2021-04-01")
    assert _preco_nas_pontas(s, ini, fim) is None
    assert _preco_nas_pontas(s, ini, fim, saida=pd.Timestamp("2020-05-20")) is not None


_TICKERS = ["AAAA3", "BBBB3", "CCCC3"]


def _hist_seg(tks: list[str]) -> dict[str, pd.DataFrame]:
    anos = list(range(2016, pd.Timestamp.now().year))
    return {
        tk: pd.DataFrame({
            "Ticker": tk,
            "Data": [pd.Timestamp(a, 12, 31) for a in anos],
            "ROE": [0.12 + i * 0.03] * len(anos),
            "ROIC": [0.10 + i * 0.02] * len(anos),
            "Margem_Liquida": [0.08] * len(anos),
            "P/L": [8.0] * len(anos),
            "P/VP": [1.2] * len(anos),
            "DY": [0.04] * len(anos),
            "AvailableAt": [pd.Timestamp(a + 1, 3, 20) for a in anos],
        })
        for i, tk in enumerate(tks)
    }


def test_segmento_morta_so_concorre_nos_anos_listada_e_nunca_na_decisao_atual():
    tks = _TICKERS + ["MORT3"]
    idx = pd.date_range("2016-01-31", periods=(pd.Timestamp.now().year - 2016) * 12, freq="ME")
    precos = pd.DataFrame({
        tk: 100.0 * np.cumprod(np.full(len(idx), 1.004 + i * 0.001))
        for i, tk in enumerate(tks)
    }, index=idx)
    precos.loc[precos.index > "2021-12-31", "MORT3"] = np.nan
    res = portfolio._processar_segmento(
        tks, _hist_seg(tks), precos, "S", "SS", "SEG",
        taxa_selic_aa=0.0, selic_macro={}, macro_history={}, aporte=1000.0,
        ano_inicio=2018, gamma=1.0, cap=1.0, soft=0.0,
        saidas_elegiveis={"MORT3": {2019, 2020, 2021}},
        saidas_ultimo_pregao={"MORT3": pd.Timestamp("2021-12-30")},
    )
    assert res is not None
    assert "MORT3" not in res["tickers"]
    assert "MORT3" not in res["score_proximo"]
    assert "MORT3" not in res["lids_prox"]
    assert set(res["tickers_saidos_por_ano"]) == {2019, 2020, 2021}
    anos_com_morta = {a for a, lids in res["lids_por_ano"].items() if "MORT3" in lids}
    # A morta tem o melhor ROE do segmento: concorre de fato, não só no papel.
    assert anos_com_morta and anos_com_morta <= {2019, 2020, 2021}
    assert res["saidas"] == {"MORT3": pd.Timestamp("2021-12-30")}
