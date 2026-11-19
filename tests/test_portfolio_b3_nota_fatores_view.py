"""Partes puras da seção "nota + fatores" (views/portfolio_b3_nota_fatores.py)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import core.b3_nota_fatores as nf
import views.portfolio_b3_nota_fatores as v

ROOT = Path(__file__).resolve().parents[1]


def _precos(tickers, meses=48):
    idx = pd.date_range("2023-01-31", periods=meses, freq="ME")
    return pd.DataFrame({t: 100 * (1 + 0.001 * k) ** np.arange(meses)
                         for k, t in enumerate(tickers)}, index=idx)


def test_formar_hoje_usa_todo_nome_com_nota_de_qualquer_segmento():
    tickers = list("ABCDEFGHIJ")
    resultados = [{"score_proximo": {t: float(k) for k, t in enumerate(tickers[:5])}},
                  {"score_proximo": {t: float(k) for k, t in enumerate(tickers[5:], 5)}},
                  {"score_proximo": {}}]
    out = v.formar_hoje(resultados, _precos(tickers), {}, perfil="X",
                        hoje="2026-11-19")
    assert out["cobertura"]["n"] == 10
    assert len(out["pesos"]) == 7
    assert out["safra"] == 2026 and out["previa"] is False
    assert out["inicio"] == pd.Timestamp("2026-04-01")
    assert out["perfil"] == "X"


def test_formar_hoje_antes_de_abril_e_previa():
    out = v.formar_hoje([{"score_proximo": {"A": 1.0, "B": 2.0}}], _precos("AB"), {},
                        perfil="X", hoje="2027-02-10")
    assert out["safra"] == 2027 and out["previa"] is True
    assert out["inicio"] == pd.Timestamp("2027-02-01")


def test_formar_hoje_sem_nota_devolve_none():
    assert v.formar_hoje([{"score_proximo": {}}], pd.DataFrame(), {}, perfil="X") is None


def test_tabela_para_exibicao_so_quem_fica_em_ordem_de_sinal():
    out = nf.carteira_nota_fatores(list("ABCDEFGHIJ"),
                                   {t: float(k) for k, t in enumerate("ABCDEFGHIJ")},
                                   _precos("ABCDEFGHIJ"), {}, pd.Timestamp("2026-04-01"))
    t = v.tabela_para_exibicao(out["tabela"])
    fica = out["tabela"][out["tabela"]["fica"]]
    assert set(t["Ticker"]) == set(fica.index) == set(out["pesos"])
    esperado = sorted(fica.index, key=lambda k: (-fica.loc[k, "sinal"], k))
    assert list(t["Ticker"]) == esperado
    assert t["Sinal"].is_monotonic_decreasing
    assert (t["Volatilidade mensal (%)"] >= 0).all()      # sinal invertido do lowvol
    assert v.tabela_para_exibicao(pd.DataFrame()).empty


def test_avisos_da_medicao():
    assert v.avisos_da_medicao(None, [], "Equilibrado (padrão)")
    dados = {"perfil": "Equilibrado (padrão)"}
    assert v.avisos_da_medicao(dados, [], "Equilibrado (padrão)") == []
    avisos = v.avisos_da_medicao(dados, ["medição x ≠ y"], "Outro")
    assert len(avisos) == 2 and "versão anterior" in avisos[0] and "Outro" in avisos[1]


def test_medicao_gravada_casa_com_o_codigo():
    """A medição no repositório tem de ser da versão atual da regra."""
    import core.b3_oos_carteira as oos
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import RECOMENDADO
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION

    dados = json.loads((ROOT / "data" / "oos_nota_fatores_b3.json").read_text(encoding="utf-8"))
    assert oos.vencida(dados, SCORE_VERSION, PRESETS_VERSION, versao_medicao=nf.VERSAO) == []
    assert dados["perfil"] == RECOMENDADO
    assert dados["regra"]["corte"] == pytest.approx(nf.CORTE)
    assert dados["nota_fatores"]["vs_equal_weight"]["n_safras"] == len(dados["safras"])


def test_grau_de_confianca_nomeia_a_medicao_gravada():
    from core.confianca_secao import nota_medicao_nota_fatores
    nota = nota_medicao_nota_fatores()
    assert nf.VERSAO in nota and "oos_nota_fatores_b3.json" in nota
    assert "VENCIDA" not in nota


def test_series_do_acumulado_da_medicao():
    dados = {"nota_fatores": {"safras": [
                 {"safra": 2017, "liquido": 0.10, "equal_weight": 0.08},
                 {"safra": 2018, "liquido": -0.05, "equal_weight": 0.01}]},
             "comparacoes": {"tirar_30_pela_nota": {"safras": [
                 {"safra": 2017, "liquido": 0.09}, {"safra": 2018, "liquido": 0.0}]}}}
    s = v.series_do_acumulado(dados)
    assert s == {"nota_fatores": {2017: 0.10, 2018: -0.05},
                 "equal_weight": {2017: 0.08, 2018: 0.01},
                 "so_nota": {2017: 0.09, 2018: 0.0}}
    assert v.series_do_acumulado(None) == {}
    assert v.series_do_acumulado({}) == {}


def test_acumulado_da_medicao_gravada():
    """Na medição do repositório, as três séries cobrem as mesmas safras."""
    from core.b3_safras import acumulado_das_safras
    dados = json.loads((ROOT / "data" / "oos_nota_fatores_b3.json").read_text(encoding="utf-8"))
    acum = acumulado_das_safras(v.series_do_acumulado(dados))
    assert acum["safras"] == dados["safras"]
    assert acum["pulados"] == [] and acum["fora"] == []
    nf_ = acum["series"]["nota_fatores"]
    esperado = float(np.prod([1 + t["liquido"] for t in dados["nota_fatores"]["safras"]]))
    assert nf_["valor_final"] == pytest.approx(esperado)
