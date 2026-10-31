"""Black-Litterman do Portfólio Global (GLB-01): prior, views, tetos, fronteira e benchmark."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from core.black_litterman import BLView, posterior_returns
from core.global_portfolio import advisor, benchmark
from core.global_portfolio import alocacao_bl as bl
from core.global_portfolio.signals import Sinal
from core.rebalancing import ThresholdRebalance

# Medições no formato de data/vantagem_oos.json (números de 24/09 e 08/09/2026).
MED_B3 = {"formato": "coeficiente", "media": 0.0750, "ic_low": 0.0172,
          "ic_high": 0.1327, "versao_metodologia": "2.28.0", "medido_em": "2026-09-24"}
MED_US = {"formato": "percentual", "media": -0.0405, "ic_low": -0.1384,
          "ic_high": 0.0510, "versao_metodologia": "0.9.0", "medido_em": "2026-09-08",
          "extras": {"rank_ic_medio": 0.1069, "rank_ic_t": 3.96}}
VERSOES = {"b3": "2.28.0", "us": "0.9.0", "fii": "6.10.0"}


def _motores():
    return bl.confianca_dos_motores(
        carregar=lambda m: {"b3": MED_B3, "us": MED_US}.get(m),
        versao=lambda m: VERSOES[m])


def _carteira(n_meses: int = 72, seed: int = 7):
    """Seis ativos, três classes, dois setores por classe; meta 50/30/20."""
    rng = np.random.default_rng(seed)
    simbolos = ["B1", "B2", "F1", "F2", "U1", "U2"]
    classes = ["b3", "b3", "fii", "fii", "us", "us"]
    setores = ["Bancos", "Energia", "Logística", "Shoppings", "Tecnologia", "Saúde"]
    meta = [0.25, 0.25, 0.15, 0.15, 0.10, 0.10]
    fator = rng.normal(0.008, 0.04, n_meses)
    dados = {s: 0.6 * fator + rng.normal(0.004, 0.05 + 0.01 * i, n_meses)
             for i, s in enumerate(simbolos)}
    idx = pd.date_range("2020-01-31", periods=n_meses, freq="ME")
    ret = pd.DataFrame(dados, index=idx)
    df = pd.DataFrame({"symbol": simbolos, "asset_class": classes, "sector": setores,
                       "weight_global": meta,
                       "payload": [{"metrics": {}} for _ in simbolos]})
    return df, ret


FOLGADO = bl.Restricoes(cap_ativo=0.6, cap_setor=0.6, limites_classe={})


# -- núcleo BL -------------------------------------------------------------------

def test_sem_views_o_posterior_e_o_prior():
    sigma = np.array([[0.04, 0.01], [0.01, 0.09]])
    pi = np.array([0.03, 0.05])
    assert np.array_equal(posterior_returns(pi, sigma, [], ["A", "B"]), pi)


def test_sem_views_os_pesos_sao_a_meta_exata():
    df, ret = _carteira()
    res = bl.black_litterman_global(df, ret, motores=_motores(), restricoes=FOLGADO,
                                    scores={})
    assert res.disponivel and not res.views
    assert res.pesos_bl == pytest.approx(res.pesos_meta, abs=1e-12)
    assert res.mu_bl == pytest.approx(res.pi)


def test_view_com_confianca_total_converge_para_a_view():
    df, ret = _carteira()
    sigma, _a, _m = bl.covariancia_lw(ret)
    tick = list(sigma.columns)
    pi = 2.5 * sigma.to_numpy() @ np.full(len(tick), 1 / len(tick))
    views = [BLView("absolute", ["B1"], [1.0], 0.20, 1.0),
             BLView("absolute", ["U2"], [1.0], -0.05, 1.0)]
    mu = posterior_returns(pi, sigma.to_numpy(), views, tick)
    assert mu[tick.index("B1")] == pytest.approx(0.20, abs=1e-6)
    assert mu[tick.index("U2")] == pytest.approx(-0.05, abs=1e-6)


def test_confianca_de_idzorek_anda_a_fracao_c_do_caminho():
    """Uma view absoluta isolada com Ω de Idzorek move μ exatamente c do caminho
    -- é o que dá sentido a "confiança 0,375" na tela."""
    df, ret = _carteira()
    sigma, _a, _m = bl.covariancia_lw(ret)
    tick = list(sigma.columns)
    pi = 2.5 * sigma.to_numpy() @ np.full(len(tick), 1 / len(tick))
    i = tick.index("F1")
    q = pi[i] + 0.04
    mu = posterior_returns(pi, sigma.to_numpy(),
                           [BLView("absolute", ["F1"], [1.0], q, 0.375)], tick)
    assert (mu[i] - pi[i]) / (q - pi[i]) == pytest.approx(0.375, abs=1e-6)


# -- confiança por motor ---------------------------------------------------------

def test_confianca_proporcional_ao_ic_medido():
    m = _motores()
    # B3: 0,5 × 0,075/0,10
    assert m["b3"].confianca == pytest.approx(0.375)
    assert m["b3"].ic == pytest.approx(0.075)
    # EUA: IC 0,107 satura em 0,5 e cai à metade porque o excesso reprovou
    assert m["us"].confianca == pytest.approx(0.25)
    assert "reprovado" in m["us"].fonte
    # FII: sem medição -> Ω = 99·τ·pΣp
    assert m["fii"].confianca == bl.CONFIANCA_MINIMA
    assert m["fii"].omega_multiplo == pytest.approx(99.0)
    assert not m["fii"].efetiva


def test_medicao_de_outra_versao_nao_da_confianca():
    c = bl.confianca_do_motor("b3", MED_B3, "2.29.0")
    assert c.confianca == bl.CONFIANCA_MINIMA and "2.28.0" in c.fonte


def test_ic_com_intervalo_atravessando_zero_nao_da_confianca():
    med = dict(MED_B3, ic_low=-0.01)
    assert bl.confianca_do_motor("b3", med, "2.28.0").confianca == bl.CONFIANCA_MINIMA


def test_ic_de_postos_nao_significativo_nao_da_confianca():
    med = dict(MED_US, extras={"rank_ic_medio": 0.05, "rank_ic_t": 1.2})
    assert bl.confianca_do_motor("us", med, "0.9.0").confianca == bl.CONFIANCA_MINIMA


def test_artefato_ilegivel_vira_sem_medicao():
    def quebra(_m):
        raise ValueError("json ruim")
    m = bl.confianca_dos_motores(carregar=quebra, versao=lambda m: "x")
    assert all(c.confianca == bl.CONFIANCA_MINIMA for c in m.values())


# -- views -----------------------------------------------------------------------

def test_z_por_classe_e_posto_na_normal_e_classe_unica_fica_neutra():
    z = bl.z_por_classe({"A": 1.0, "B": 5.0, "C": 3.0, "X": 9.0},
                        {"A": "b3", "B": "b3", "C": "b3", "X": "us"})
    assert z["C"] == pytest.approx(0.0)
    assert z["B"] == pytest.approx(-z["A"]) and z["B"] > 0
    assert z["X"] == 0.0


def test_alpha_segue_ic_vezes_vol_vezes_z():
    df, ret = _carteira()
    scores = {"B1": 10.0, "B2": 2.0, "F1": 8.0, "F2": 1.0}
    res = bl.black_litterman_global(df, ret, motores=_motores(), restricoes=FOLGADO,
                                    scores=scores)
    v = {x.symbol: x for x in res.views}
    assert v["B1"].alpha == pytest.approx(0.075 * v["B1"].sigma_anual * v["B1"].z)
    assert v["B1"].q == pytest.approx(v["B1"].pi + v["B1"].alpha)
    # FII sem IC medido: view desenhada com IC 0,05, mas confiança mínima
    assert v["F1"].alpha == pytest.approx(bl.IC_SEM_MEDICAO * v["F1"].sigma_anual * v["F1"].z)
    assert v["F1"].confianca == bl.CONFIANCA_MINIMA


def test_view_forte_move_peso_e_view_ignorada_quase_nao_move():
    df, ret = _carteira()
    scores = {"B1": 10.0, "B2": 2.0, "F1": 10.0, "F2": 2.0}
    res = bl.black_litterman_global(df, ret, motores=_motores(), restricoes=FOLGADO,
                                    scores=scores)
    desvio_b3 = res.pesos_bl["B1"] - res.pesos_meta["B1"]
    desvio_fii = res.pesos_bl["F1"] - res.pesos_meta["F1"]
    assert desvio_b3 > 0.01
    assert 0 <= desvio_fii < desvio_b3 / 10


# -- covariância e restrições ----------------------------------------------------

def test_lw_usa_so_a_janela_comum_e_os_ultimos_meses():
    _df, ret = _carteira(n_meses=90)
    ret.iloc[:5, 0] = np.nan
    sigma, alpha, meses = bl.covariancia_lw(ret, max_meses=60)
    assert meses == 60 and 0.0 <= alpha <= 1.0
    esperado = ret.dropna().tail(60).cov().to_numpy() * 12
    assert np.allclose(np.diag(sigma.to_numpy()), np.diag(esperado))


def test_tetos_sao_respeitados_no_resultado_final():
    df, ret = _carteira()
    scores = {"B1": 10.0, "B2": 0.0, "F1": 10.0, "F2": 0.0, "U1": 10.0, "U2": 0.0}
    forte = {m: bl.ConfiancaMotor(m, 0.30, 0.95, "teste") for m in bl.MOTORES}
    r = bl.Restricoes(cap_ativo=0.27, cap_setor=0.27, limites_classe={})
    res = bl.black_litterman_global(df, ret, motores=forte, restricoes=r, scores=scores)
    assert res.disponivel and res.degrau == "completo"
    assert max(res.pesos_bl.values()) <= 0.27 + 1e-6
    assert sum(res.pesos_bl.values()) == pytest.approx(1.0)
    por_classe = res.pesos_por_classe(res.pesos_bl)
    assert por_classe["b3"] == pytest.approx(0.50, abs=1e-6)
    assert por_classe["fii"] == pytest.approx(0.30, abs=1e-6)
    assert por_classe["us"] == pytest.approx(0.20, abs=1e-6)


def test_limite_de_classe_da_politica_acima_da_meta_relaxa_com_aviso():
    df, ret = _carteira()
    r = bl.Restricoes(cap_ativo=0.6, cap_setor=None, limites_classe={"b3": 0.40})
    res = bl.black_litterman_global(df, ret, motores=_motores(), restricoes=r, scores={})
    assert res.degrau.startswith("classes_livres")
    assert res.pesos_por_classe(res.pesos_bl)["b3"] <= 0.40 + 1e-6
    assert any("Restrição relaxada" in a for a in res.avisos)


def test_tetos_impossiveis_viram_indisponivel_com_motivo():
    df, ret = _carteira()
    r = bl.Restricoes(cap_ativo=0.10, cap_setor=None, limites_classe={})
    res = bl.black_litterman_global(df, ret, motores=_motores(), restricoes=r, scores={})
    assert not res.disponivel and "teto" in res.motivo


def test_ativo_sem_serie_fica_fixado_na_meta():
    df, ret = _carteira()
    res = bl.black_litterman_global(df, ret.drop(columns=["U2"]), motores=_motores(),
                                    restricoes=FOLGADO, scores={"B1": 1.0, "B2": 0.0})
    assert res.fixados == ("U2",)
    assert res.pesos_bl["U2"] == pytest.approx(0.10)
    assert sum(res.pesos_bl.values()) == pytest.approx(1.0)


def test_restricoes_da_politica_convertem_para_a_parcela_de_risco():
    valores = {"single_asset_limit_pct": 10, "sector_limit_pct": 25,
               "asset_class_limits": {"acoes_br": 50, "fiis": 20, "exterior": 30,
                                      "renda_fixa": 80}}
    r = bl.restricoes_da_politica(valores, renda_fixa=0.30)
    assert r.cap_ativo == pytest.approx(0.10 / 0.70)
    assert r.cap_setor == pytest.approx(0.25 / 0.70)
    assert r.limites_classe == pytest.approx({"b3": 0.50 / 0.7, "fii": 0.20 / 0.7,
                                              "us": 0.30 / 0.7})
    sem_rf = bl.restricoes_da_politica(valores, renda_fixa=None)
    assert sem_rf.cap_ativo == pytest.approx(0.10)
    assert bl.restricoes_da_politica(None, 0.3) == bl.Restricoes()


# -- fronteira -------------------------------------------------------------------

def test_fronteira_contem_o_ponto_bl_e_domina_a_meta():
    df, ret = _carteira()
    scores = {"B1": 10.0, "B2": 2.0, "U1": 1.0, "U2": 9.0}
    res = bl.black_litterman_global(df, ret, motores=_motores(), restricoes=FOLGADO,
                                    scores=scores)
    pontos = bl.fronteira_eficiente(res, n_pontos=15)
    assert len(pontos) >= 10
    vol_bl, ret_bl = res.risco_retorno(res.pesos_bl)
    vol_meta, ret_meta = res.risco_retorno(res.pesos_meta)
    # Nenhum ponto da fronteira com retorno >= o do BL tem vol menor (com folga
    # da discretização): o BL é eficiente sob as mesmas restrições.
    assert ret_bl >= ret_meta - 1e-9
    vols, rets = np.array([p[0] for p in pontos]), np.array([p[1] for p in pontos])
    assert np.interp(ret_bl, rets, vols) <= vol_bl + 1e-3
    assert np.interp(ret_meta, rets, vols) <= vol_meta + 1e-6


def test_pesos_reais_cobertos_dizem_a_fracao_representada():
    df, ret = _carteira()
    res = bl.black_litterman_global(df, ret, motores=_motores(), restricoes=FOLGADO,
                                    scores={})
    pesos, cob = bl.pesos_reais_cobertos(res, {"B1": 600.0, "U1": 200.0, "XPTO": 200.0})
    assert cob == pytest.approx(0.8)
    assert pesos == pytest.approx({"B1": 0.75, "U1": 0.25})


# -- integração com o motor de movimentação --------------------------------------

def test_advisor_sem_pesos_alvo_continua_no_tilt_e_com_pesos_alvo_segue_o_bl():
    df, _ret = _carteira()
    sinais = [Sinal(nome="q", symbol=s, valor=0.5, direcao="aumentar",
                    analisador="metrics", texto="q") for s in df["symbol"] if s != "U2"]
    kw = dict(alvos={}, politica=ThresholdRebalance(banda_abs=0.0, rebal_inicial=True),
              custos={}, patrimonio_total=1e6, data_atual=date(2026, 10, 4))
    tilt = {a.symbol: a.peso_sugerido for a in advisor.recomendar(df, sinais, **kw)}
    alvo = {"B1": 0.35, "B2": 0.15, "F1": 0.15, "F2": 0.15, "U1": 0.10, "U2": 0.10}
    via_bl = {a.symbol: a for a in advisor.recomendar(df, sinais, pesos_alvo=alvo, **kw)}
    assert via_bl["B1"].peso_sugerido == pytest.approx(0.35)
    assert via_bl["B2"].peso_sugerido == pytest.approx(0.15)
    assert via_bl["U2"].acao == "indeterminado"     # sem sinal continua pinado
    assert tilt["B1"] == pytest.approx(0.25, abs=0.02)  # tilt uniforme mantém ~meta


# -- benchmark composto ----------------------------------------------------------

def test_cdi_mensal_compoe_os_dias_uteis():
    d0 = date(2026, 1, 1)
    diario = {d0 + timedelta(days=i): 0.05 for i in range(20)}
    m = benchmark.cdi_mensal(diario)
    assert len(m) == 1 and m.iloc[0] == pytest.approx(1.0005 ** 20 - 1)


def test_benchmark_composto_pondera_pela_meta_e_exige_todas_as_classes():
    idx = pd.date_range("2024-01-31", periods=4, freq="ME")
    ret = pd.DataFrame({"b3": [0.02, 0.01, np.nan, 0.03],
                        "fii": [0.01, 0.01, 0.01, 0.01],
                        "us": [0.00, 0.02, 0.02, 0.02]}, index=idx)
    cdi = pd.Series([0.01] * 4, index=idx)
    serie, avisos = benchmark.benchmark_composto(
        {"b3": 0.35, "fii": 0.21, "us": 0.14, "renda_fixa": 0.30}, ret, cdi)
    assert not avisos
    assert len(serie) == 3        # mês sem Ibovespa sai, não vira outro índice
    assert serie.iloc[0] == pytest.approx(0.35 * 0.02 + 0.21 * 0.01 + 0.30 * 0.01)


def test_classe_sem_proxy_e_nomeada():
    idx = pd.date_range("2024-01-31", periods=3, freq="ME")
    _s, avisos = benchmark.benchmark_composto(
        {"b3": 0.5, "fii": 0.5}, pd.DataFrame({"b3": [0.01] * 3}, index=idx),
        pd.Series(dtype=float))
    assert avisos and "IFIX" in avisos[0]


def test_carteira_recebe_a_renda_fixa_como_cdi():
    idx = pd.date_range("2024-01-31", periods=2, freq="ME")
    s = benchmark.com_renda_fixa(pd.Series([0.10, 0.0], index=idx), 0.3,
                                 pd.Series([0.01, 0.01], index=idx))
    assert s.iloc[0] == pytest.approx(0.7 * 0.10 + 0.3 * 0.01)


# -- tela ------------------------------------------------------------------------

def test_valores_reais_por_ativo_so_risco_e_sem_sufixo():
    from core.global_portfolio.carteira_real import valores_reais_por_ativo

    pos = [{"ticker": "PETR4.SA", "classe": "Ações BR", "valor_mercado": 100.0},
           {"ticker": "HGLG11", "classe": "FII", "valor_mercado": 50.0},
           {"ticker": "AAPL", "classe": "Ações BR", "moeda": "USD", "pais": "US",
            "valor_mercado": 30.0},
           {"ticker": "TESOURO", "classe": "Tesouro Direto", "valor_mercado": 999.0},
           {"ticker": "BTC", "classe": "Cripto", "valor_mercado": 10.0}]
    assert valores_reais_por_ativo(pos) == {"PETR4": 100.0, "HGLG11": 50.0, "AAPL": 30.0}


def test_tabela_da_tela_mostra_meta_bl_e_real_ordenada_pela_mudanca():
    from views.portfolio_global import alvo_do_motor_bl, tabela_black_litterman

    df, ret = _carteira()
    res = bl.black_litterman_global(df, ret, motores=_motores(), restricoes=FOLGADO,
                                    scores={"B1": 10.0, "B2": 0.0})
    t = tabela_black_litterman(res, {"B1": 0.5})
    assert list(t.columns[:6]) == ["Ativo", "Classe", "Meta", "Black-Litterman", "Real",
                                   "BL − meta (pp)"]
    assert t.iloc[0]["Ativo"] in ("B1", "B2")
    assert t.set_index("Ativo").loc["B1", "Real"] == pytest.approx(50.0)
    assert t["Meta"].sum() == pytest.approx(100.0)
    # Meta somando 0,8 no snapshot: o alvo do motor volta para a mesma escala.
    df2 = df.assign(weight_global=df["weight_global"] * 0.8)
    alvo = alvo_do_motor_bl(df2, res)
    assert sum(alvo.values()) == pytest.approx(0.8)


def test_render_passa_o_mesmo_bl_ao_motor_e_ao_chat():
    import inspect

    from views import portfolio_global as v

    src = inspect.getsource(v.render)
    assert "bl = _painel_black_litterman(" in src
    assert src.index("_painel_black_litterman(") < src.index("_painel_recomendacoes(")
    assert "macro=macro, bl=bl)" in src
    assert "bl.para_llm()" in inspect.getsource(v._painel_chat)


def test_explicacao_de_hoje_sai_das_confiancas_vigentes():
    """GLB-N1: o texto fixo dizia B3 c = 37,5% quando o cálculo já dava 1%."""
    texto = bl.explicacao_numeros_de_hoje(_motores())
    assert "Empresas B3 → c = 37,5%" in texto and "EUA → c = 25,0%" in texto
    assert "FIIs → c = 1,0%" in texto
    assert "Exemplo: um ativo de Empresas B3" in texto  # maior confiança efetiva

    b3_cruza_zero = bl.confianca_dos_motores(
        carregar=lambda m: {"b3": dict(MED_B3, ic_low=-0.016), "us": MED_US}.get(m),
        versao=lambda m: VERSOES[m])
    texto = bl.explicacao_numeros_de_hoje(b3_cruza_zero)
    assert "Empresas B3 → c = 1,0%" in texto and "37,5" not in texto
    assert "Exemplo: um ativo de EUA" in texto


def test_explicacao_sem_motor_efetivo_nao_inventa_exemplo():
    nada = bl.confianca_dos_motores(carregar=lambda m: None, versao=lambda m: "x")
    texto = bl.explicacao_numeros_de_hoje(nada)
    assert "Exemplo" not in texto and "praticamente na meta" in texto
