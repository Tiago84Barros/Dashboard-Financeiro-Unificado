"""Testes da carteira "nota + fatores" (core/b3_nota_fatores.py)."""

import math

import numpy as np
import pandas as pd
import pytest

from core import b3_nota_fatores as nf


def _precos(colunas: dict, inicio="2020-01-31", meses=48) -> pd.DataFrame:
    idx = pd.date_range(inicio, periods=meses, freq="ME")
    return pd.DataFrame({k: v(np.arange(meses)) for k, v in colunas.items()}, index=idx)


def test_precos_mensais_descarta_nao_positivo_e_completa_a_grade():
    idx = pd.to_datetime(["2024-01-10", "2024-01-31", "2024-03-29"])
    df = pd.DataFrame({"A": [10.0, 11.0, -1.0], "B": [0.0, 5.0, 6.0]}, index=idx)
    pm = nf.precos_mensais(df)
    assert list(pm.index.astype(str)) == ["2024-01", "2024-02", "2024-03"]
    assert pm.loc[pd.Period("2024-01"), "A"] == 11.0      # último do mês
    assert pd.isna(pm.loc[pd.Period("2024-02"), "A"])     # buraco não é preenchido
    assert pd.isna(pm.loc[pd.Period("2024-03"), "A"])     # negativo não é cotação
    assert pm.loc[pd.Period("2024-01"), "B"] == 5.0


def test_momento_12_1_pula_o_ultimo_mes():
    # Preço 100 até o mês t-1 subindo 1 por mês; no mês t salta para 1000.
    precos = _precos({"A": lambda i: 100.0 + i})
    pm = nf.precos_mensais(precos)
    inicio = pd.Timestamp("2023-04-01")
    t = pd.Period(inicio, freq="M") - 1
    pm.loc[t, "A"] = 1000.0
    fat = nf.fatores_na_formacao(["A"], pm, {}, inicio)
    esperado = pm.loc[t - 1, "A"] / pm.loc[t - 12, "A"] - 1
    assert fat.loc["A", "mom"] == pytest.approx(esperado)


def test_baixa_vol_exige_24_retornos_na_janela_de_36():
    precos = _precos({"A": lambda i: 100 * 1.01 ** i * (1 + 0.02 * (i % 2)),
                      "B": lambda i: 100 * 1.01 ** i})
    pm = nf.precos_mensais(precos)
    inicio = pd.Timestamp("2023-04-01")
    t = pd.Period(inicio, freq="M") - 1
    pm.loc[t - 20:t, "B"] = np.nan        # B fica com menos de 24 retornos
    fat = nf.fatores_na_formacao(["A", "B"], pm, {}, inicio)
    assert fat.loc["A", "lowvol"] < 0
    assert pd.isna(fat.loc["B", "lowvol"])


def test_roe_so_conta_o_publicado_antes_do_inicio():
    hist = pd.DataFrame({
        "Data": pd.to_datetime(["2021-12-31", "2022-12-31"]),
        "AvailableAt": ["2022-03-20T00:00:00Z", "2023-04-15T00:00:00Z"],
        "ROE": [0.10, 0.30],
    })
    assert nf.roe_disponivel(hist, "2023-04-01") == pytest.approx(0.10)
    assert nf.roe_disponivel(hist, "2023-05-01") == pytest.approx(0.30)
    assert math.isnan(nf.roe_disponivel(hist, "2022-01-01"))
    assert math.isnan(nf.roe_disponivel(None, "2023-04-01"))


def test_composto_sem_fator_fica_no_neutro():
    fat = pd.DataFrame({"mom": [0.1, 0.2, np.nan], "lowvol": [np.nan, np.nan, np.nan],
                        "roe": [0.3, 0.1, np.nan]}, index=["A", "B", "C"])
    c = nf.composto(fat)
    assert c["C"] == nf.NEUTRO
    assert c["A"] == pytest.approx((0.5 + 1.0) / 2)   # mom 2º de 2, roe 1º de 2
    assert c["B"] == pytest.approx((1.0 + 0.5) / 2)


def test_sinal_sem_nota_usa_so_o_composto():
    fat = pd.DataFrame({"mom": [0.1, 0.2, 0.3], "lowvol": [np.nan] * 3,
                        "roe": [np.nan] * 3}, index=["A", "B", "C"])
    partes = nf.sinal_nota_fatores(["A", "B", "C"], {"A": 90.0, "B": 10.0}, fat)
    assert partes.loc["C", "sinal"] == pytest.approx(partes.loc["C", "fatores"])
    assert partes.loc["A", "nota_pct"] == 1.0 and partes.loc["B", "nota_pct"] == 0.5
    assert partes.loc["A", "sinal"] == pytest.approx((1.0 + 1 / 3) / 2)


def test_tirar_piores_usa_floor_e_desempata_pelo_ticker():
    s = pd.Series({"D": 0.2, "B": 0.2, "A": 0.9, "C": 0.5, "E": 0.2})
    ficam, saem = nf.tirar_piores(s, 0.30)      # floor(1,5) = 1 sai
    assert saem == ["E"]                          # empate em 0,2: B < D < E
    assert ficam == ["A", "C", "B", "D"]
    ficam, saem = nf.tirar_piores(pd.Series({"A": 1.0, "B": 0.0, "C": 0.5}), 0.30)
    assert saem == []                             # floor(0,9) = 0


def test_tirar_piores_nao_depende_da_ordem_de_entrada():
    s = pd.Series({"X": 0.4, "Y": 0.4, "Z": 0.4, "W": 0.1})
    a = nf.tirar_piores(s, 0.5)
    b = nf.tirar_piores(s[["Z", "W", "Y", "X"]], 0.5)
    assert a == b


def test_inicio_da_formacao_antes_e_depois_de_abril():
    assert nf.inicio_da_formacao(2026, "2026-11-19") == pd.Timestamp("2026-04-01")
    assert nf.inicio_da_formacao(2027, "2027-02-10") == pd.Timestamp("2027-02-01")


def test_carteira_pesos_iguais_e_cobertura():
    precos = _precos({t: (lambda k: (lambda i: 100 * (1 + 0.001 * k) ** i))(k)
                      for k, t in enumerate("ABCDEFGHIJ")})
    scores = {t: float(k) for k, t in enumerate("ABCDEFGHIJ")}
    out = nf.carteira_nota_fatores(list("ABCDEFGHIJ"), scores, precos, {},
                                   pd.Timestamp("2023-04-01"))
    assert len(out["pesos"]) == 7                 # 10 − floor(3)
    assert sum(out["pesos"].values()) == pytest.approx(1.0)
    assert set(out["pesos"]) == set("DEFGHIJ")    # nota e momento crescem juntos
    assert out["cobertura"] == {"n": 10, "nota": 10, "mom": 10, "lowvol": 10, "roe": 0}
    assert out["mes_fatores"] == "2023-03"
    assert out["tabela"]["fica"].sum() == 7


def test_carteira_universo_vazio():
    out = nf.carteira_nota_fatores([], {}, pd.DataFrame(), {}, "2023-04-01")
    assert out["pesos"] == {} and out["cobertura"]["n"] == 0
