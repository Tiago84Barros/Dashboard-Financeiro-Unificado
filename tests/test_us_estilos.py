import math

import pandas as pd
import pytest

import core.us_advanced_lab as lab
import core.us_estilos as es


def _diario(symbol, inicio, fim, preco):
    """Pregões úteis de ``inicio`` a ``fim`` com o preço dado por ``preco(data)``."""
    dias = pd.bdate_range(inicio, fim)
    return pd.DataFrame({"symbol": symbol, "date": dias, "adjusted_close": [preco(d) for d in dias]})


def test_momento_vai_de_12_meses_a_1_mes_antes_do_ultimo_pregao():
    # sobe 1% ao mês corrido; o último mês dobra e não pode contar
    base = pd.Timestamp("2025-10-28")

    def preco(d):
        meses = (d - base).days / 30.4375
        p = 100 * 1.01 ** meses
        return p * 2 if d > pd.Timestamp("2026-09-28") else p

    m = es.momento_12_1(_diario("abc", "2025-09-01", "2026-10-28", preco))
    assert set(m) == {"ABC"}
    assert m["ABC"]["momento_12_1"] == pytest.approx(1.01 ** 11 - 1, rel=2e-3)
    assert m["ABC"]["momento_12_1_at"].startswith("2026-10-28")


def test_sem_preco_de_12_meses_atras_nao_ha_momento():
    m = es.momento_12_1(_diario("NOVO", "2026-03-02", "2026-10-28", lambda d: 10.0))
    assert m == {}


def test_buraco_maior_que_a_tolerancia_na_ponta_nao_vira_momento():
    d = _diario("BURACO", "2025-08-01", "2026-10-28", lambda d: 10.0)
    d = d[(d["date"] < "2025-10-01") | (d["date"] > "2025-11-20")]
    assert es.momento_12_1(d) == {}


def _universo():
    return pd.DataFrame({
        "symbol": ["A", "B", "C", "D", "E"],
        "_market_cap": [1e10, 1e10, 1e10, 1e10, 1e10],
        "momento_12_1": [0.40, -0.20, 0.05, None, 0.10],
        "momento_12_1_at": ["2026-10-28T00:00:00+00:00"] * 4 + ["2026-07-01T00:00:00+00:00"],
        "pd_anual_usd": [2e9, 0.0, 5e8, None, 1e8],
    })


def test_notas_sao_percentil_no_universo_e_ausencia_fica_vazia():
    n = es.notas_de_estilo(_universo()).set_index("symbol")
    # E tem momento de julho: velho demais, não conta
    assert n.loc["A", "score_momentum"] == 100.0
    assert n.loc["B", "score_momentum"] < n.loc["C", "score_momentum"] < 100.0
    assert math.isnan(n.loc["D", "score_momentum"]) and math.isnan(n.loc["E", "score_momentum"])
    # P&D zero ou ausente é neutro; entre quem declara, mais P&D por valor é nota maior
    assert n.loc["A", "pd_valor_mercado"] == pytest.approx(0.2)
    assert n.loc["A", "score_pd"] > n.loc["C", "score_pd"] > n.loc["E", "score_pd"]
    assert math.isnan(n.loc["B", "score_pd"]) and math.isnan(n.loc["D", "score_pd"])


def test_nota_ja_calculada_e_mantida():
    u = _universo().assign(score_momentum=[1.0, 2.0, 3.0, 4.0, 5.0])
    assert es.notas_de_estilo(u)["score_momentum"].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_sem_colunas_de_estilo_as_trilhas_ficam_neutras():
    base = pd.DataFrame({"symbol": ["A", "B"], "score_quality": [80.0, 40.0]})
    e = lab.build_entry_scores(base)
    pesos = lab.normalize_weights()
    neutro = sum(50.0 * w for t, w in pesos.items() if t != "quality")
    assert e.set_index("symbol").loc["A", "score_base_adv"] == pytest.approx(
        round(80.0 * pesos["quality"] + neutro, 1))


def test_momento_e_pd_mudam_a_nota_de_entrada():
    u = _universo().assign(score_quality=60.0)
    e = lab.build_entry_scores(u).set_index("symbol")
    # A tem o maior momento e o maior P&D; B, o menor momento e P&D neutro
    assert e.loc["A", "score_base_adv"] > e.loc["B", "score_base_adv"]
    assert {"score_momentum", "score_pd"} <= set(e.columns)


def test_pesos_padrao_somam_um_e_trazem_as_oito_trilhas():
    assert sum(lab.DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)
    assert set(lab.DEFAULT_WEIGHTS) == set(lab.TRACK_COLUMNS) == set(lab.TRACK_LABELS)
    assert lab.TRACK_COLUMNS["momentum"] == "score_momentum"
    assert lab.TRACK_COLUMNS["rd"] == "score_pd"
    # as seis fundamentalistas guardam a proporção de antes (22:18:15:15:18:12)
    antes = {"quality": .22, "growth": .18, "solidity": .15, "capital_efficiency": .15,
             "valuation": .18, "shareholder": .12}
    for t, w in antes.items():
        assert lab.DEFAULT_WEIGHTS[t] / .82 == pytest.approx(w, abs=0.006)


def test_contribuicoes_nomeiam_as_trilhas_novas():
    row = pd.Series({"score_quality": 70.0, "score_momentum": 90.0, "score_pd": None})
    c = lab.factor_contributions(row).set_index("Trilha")
    assert c.loc["Momento (12 meses)", "Contribuição"] == pytest.approx(
        40.0 * lab.normalize_weights()["momentum"])
    assert c.loc["Pesquisa e desenvolvimento", "Contribuição"] == 0.0


def test_estilos_da_empresa_nunca_inventa_zero():
    r = es.estilos_da_empresa("x", {"X": {"momento_12_1": 0.1, "momento_12_1_at": "a"}}, {})
    assert r == {"momento_12_1": 0.1, "momento_12_1_at": "a", "pd_anual_usd": None,
                 "pd_exercicio": None}
