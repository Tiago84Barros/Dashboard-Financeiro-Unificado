import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import core.b3_previsao_lucros as pl

RAIZ = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def _modelos_leves(monkeypatch):
    # A regua e a mesma; so encolhe o custo do teste.
    monkeypatch.setitem(pl.HIPERPARAMETROS, "gbm", {**pl.HIPERPARAMETROS["gbm"], "n_arvores": 40})
    monkeypatch.setitem(pl.HIPERPARAMETROS, "mlp", {**pl.HIPERPARAMETROS["mlp"],
                                                    "epocas": 60, "membros": 2})
    monkeypatch.setitem(pl.HIPERPARAMETROS, "bootstrap_amostras", 300)


def _fund(n_emp=60, anos=range(2008, 2021), sinal=True, seed=0):
    """Painel sintetico: a variacao do lucro reverte a do ano anterior."""
    rng = np.random.default_rng(seed)
    linhas = []
    for cd in range(n_emp):
        ta, ni_prev, ni = 1000.0, 50.0, 50.0
        for ano in anos:
            choque = rng.normal(0, 20)
            delta = (-0.6 * (ni - ni_prev) if sinal else 0.0) + choque
            ni_prev, ni = ni, ni + delta
            ta *= 1.05
            linhas.append({"cd_cvm": cd, "ano": ano,
                           "available_at": pd.Timestamp(ano + 1, 3, 15),
                           "net_income": ni, "revenue": 800.0, "ebit": ni * 1.3,
                           "total_assets": ta, "equity": ta * 0.4,
                           "gross_debt": ta * 0.3, "fco": ni * 1.1,
                           "current_assets": 300.0, "current_liabilities": 200.0,
                           "dividendos_pagos": 10.0})
    return pd.DataFrame(linhas)


def test_data_decisao_e_a_do_score():
    assert pl.data_decisao(2024) == pd.Timestamp(2024, 4, 1)


def test_sem_dt_receb_vale_o_prazo_cvm():
    assert pl.disponivel_em(None, 2023) == pd.Timestamp(2024, 3, 31)
    assert pl.disponivel_em("2024-05-10", 2023) == pd.Timestamp(2024, 5, 10)


def test_painel_alvo_sem_escala_e_denominador_negativo_vira_nan():
    f = pd.DataFrame([
        {"cd_cvm": 1, "ano": 2019, "net_income": 10.0, "total_assets": 100.0,
         "revenue": 50.0, "equity": -5.0, "available_at": "2020-03-10"},
        {"cd_cvm": 1, "ano": 2020, "net_income": 30.0, "total_assets": 120.0,
         "revenue": 60.0, "equity": 20.0, "available_at": "2021-03-10"},
    ])
    p = pl.montar_painel(f)
    r = p[p["safra"] == 2020].iloc[0]
    assert r["y"] == pytest.approx((30 - 10) / 100)
    assert np.isnan(r["roe"])          # PL negativo: nada de ROE com sinal trocado
    assert r["pl_negativo"] == 1.0
    assert r["margem_liquida"] == pytest.approx(0.2)
    # A safra 2021 nao tem alvo (2021 nao publicado) mas fica no painel.
    assert np.isnan(p[p["safra"] == 2021].iloc[0]["y"])


def test_treino_so_ve_alvo_publicado_ate_a_decisao():
    f = _fund(n_emp=3, anos=range(2015, 2020))
    # Exercicio 2018 da empresa 0 publicado em maio/2019: fica fora da safra 2019.
    f.loc[(f.cd_cvm == 0) & (f.ano == 2018), "available_at"] = pd.Timestamp(2019, 5, 2)
    p = pl.montar_painel(f)
    tr = pl.conjunto_treino(p, 2019)
    assert not ((tr.cd_cvm == 0) & (tr.safra == 2018)).any()
    assert ((tr.cd_cvm == 1) & (tr.safra == 2018)).any()
    assert (tr["safra"] < 2019).all()
    # E a mesma empresa nao entra no TESTE da safra 2019 (base atrasada).
    te = pl.conjunto_teste(p, 2019)
    assert 0 not in set(te.cd_cvm)


def test_preparador_usa_so_estatistica_do_treino():
    tr = pd.DataFrame({c: np.linspace(0, 1, 200) for c in pl.FEATURES})
    te = pd.DataFrame({c: [1e6] for c in pl.FEATURES})
    prep = pl.Preparador().fit(tr)
    z_tr = prep.transform(tr)
    z_te = prep.transform(te)
    # O valor extremo do teste e cortado no limite APRENDIDO no treino.
    assert z_te.max() <= z_tr.max() + 1e-9
    assert np.allclose(z_tr.mean(axis=0), 0, atol=1e-9)


def test_indicador_de_ausencia_aprendido_no_treino():
    tr = pd.DataFrame({c: np.linspace(0, 1, 50) for c in pl.FEATURES})
    tr.loc[:9, "margem_liquida"] = np.nan
    prep = pl.Preparador().fit(tr)
    assert prep.ind == ["margem_liquida"]
    assert prep.transform(tr).shape[1] == len(pl.FEATURES) + 1


def test_ridge_recupera_coeficiente():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(500, 3))
    y = 2 * X[:, 0] - X[:, 2] + 0.5
    m = pl.Ridge(1e-6).fit(X, y)
    assert m.w == pytest.approx([2, 0, -1], abs=1e-3)
    assert m.b0 == pytest.approx(0.5, abs=1e-3)


def test_gbm_aprende_degrau_com_perda_absoluta():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(600, 2))
    y = np.where(X[:, 0] > 0, 1.0, -1.0) + rng.standard_t(2, 600) * 0.1
    m = pl.GBMAbsoluto(n_arvores=60, taxa=0.2).fit(X, y)
    assert np.mean(np.abs(m.predict(X) - y)) < 0.5 * np.mean(np.abs(y))


def test_walk_forward_com_sinal_vence_o_ingenuo():
    p = pl.montar_painel(_fund(sinal=True))
    preds = pl.walk_forward(p, list(range(2014, 2020)))
    assert set(preds["safra"]) == set(range(2014, 2020))
    v = pl.comparar_com_ingenuo(preds, "ridge")
    assert v["veredito"] == "APROVADO"
    assert v["mae_modelo"] < v["mae_ingenuo"]


def test_walk_forward_sem_sinal_e_reprovado():
    p = pl.montar_painel(_fund(sinal=False, seed=3))
    preds = pl.walk_forward(p, list(range(2014, 2020)), com_mlp=False)
    for k in ("ridge", "gbm"):
        assert pl.comparar_com_ingenuo(preds, k)["veredito"] == "REPROVADO"


def test_escolha_so_admite_mlp_que_vence_os_tres():
    df = pd.DataFrame({"y": [1.0, -1.0, 2.0, -2.0], "cd_cvm": [1, 2, 3, 4],
                       "pred_ingenuo": 0.0, "pred_ridge": [0.9, -0.9, 1.8, -1.8],
                       "pred_gbm": [0.5, -0.5, 1, -1], "pred_mlp": [0.95, -0.95, 1.9, -1.9]})
    e = pl.escolher_modelo(df)
    assert e["mlp_elegivel"] and e["modelo"] == "mlp"
    df["pred_mlp"] = [0.0, 0.0, 0.0, 0.0]
    e = pl.escolher_modelo(df)
    assert not e["mlp_elegivel"] and e["modelo"] == "ridge"


def test_sinal_comparado_ao_majoritario_nao_so_a_50():
    # 70% sobem; o modelo preve "sobe" sempre: 70% de acerto, zero habilidade.
    y = np.array([1.0] * 70 + [-1.0] * 30)
    df = pd.DataFrame({"y": y, "cd_cvm": np.arange(100), "pred_ingenuo": 0.0,
                       "pred_x": 0.5, "pred_sinal_majoritario": 1.0})
    v = pl.comparar_com_ingenuo(df, "x")
    assert v["p_sinal_vs_50"] < 0.05
    assert v["sinal"] == "REPROVADO"


def test_binomial_e_wilson():
    assert pl.p_binomial_maior(60, 100, 0.5) == pytest.approx(0.0284, abs=1e-3)
    lo, hi = pl.wilson(50, 100)
    assert lo < 0.5 < hi


def test_bootstrap_reamostra_empresas():
    dif = np.array([-1.0] * 10 + [1.0] * 10)
    cl = np.array([0] * 10 + [1] * 10)
    r = pl.bootstrap_diferenca_por_empresa(dif, cl, amostras=500)
    assert r["n_empresas"] == 2
    # Com duas empresas o intervalo tem de cobrir o zero -- linhas soltas nao.
    assert r["ic_low"] < 0 < r["ic_high"]


def test_intervalo_pit_usa_so_safras_anteriores():
    rng = np.random.default_rng(4)
    df = pd.DataFrame({"safra": np.repeat([2014, 2015, 2016], 50),
                       "y": rng.normal(size=150), "pred_m": 0.0})
    out = pl.intervalos_pit(df, "m")
    assert out.loc[out.safra == 2014, "ic_low"].isna().all()
    assert out.loc[out.safra == 2015, "ic_low"].notna().all()
    lo15 = out.loc[out.safra == 2015, "ic_low"].iloc[0]
    assert lo15 == pytest.approx(np.quantile(df.y[df.safra == 2014], 0.1))


def test_efeito_rank_ic_compara_no_mesmo_subconjunto():
    rng = np.random.default_rng(5)
    pares, prev = [], {}
    for ano in range(2016, 2024):
        for i in range(40):
            ret = rng.normal()
            pares.append((ano, f"T{i}", rng.normal(), ret))
            if i < 30:  # 10 tickers sem previsao: saem dos tres ICs
                prev[(ano, f"T{i}")] = ret + rng.normal(0, 0.3)
    r = pl.efeito_rank_ic(pares, prev)
    assert r["n_pares_com_previsao"] == 8 * 30
    assert r["cobertura_previsao"] == pytest.approx(0.75)
    assert r["veredito"] == "MELHORA"
    assert r["delta"]["ci_low"] > 0


def test_config_hash_muda_com_a_regua(monkeypatch):
    h = pl.config_hash()
    monkeypatch.setattr(pl, "HOLDOUT_INICIO", 2021)
    assert pl.config_hash() != h


def test_artefato_ausente_devolve_none(tmp_path):
    assert pl.carregar_artefato(str(tmp_path / "nada.json.gz")) is None


def test_modulo_e_puro():
    arv = ast.parse((RAIZ / "core" / "b3_previsao_lucros.py").read_text(encoding="utf-8"))
    nomes = set()
    for n in ast.walk(arv):
        if isinstance(n, ast.Import):
            nomes |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            nomes.add(n.module.split(".")[0])
    assert not nomes & {"streamlit", "sqlalchemy", "requests", "sklearn", "psycopg2"}


def test_portfolio_b3_entrega_pares_com_ticker_sem_quebrar_os_de_tres():
    src = (RAIZ / "views" / "portfolio_b3.py").read_text(encoding="utf-8")
    assert '"ic_pairs_tk": ic_pairs_tk' in src
    assert "ic_pairs: list[tuple[int, float, float]]" in src
