"""Carteira de safras FII com a estrutura da Criação de Portfólio B3.

Cobre: ponto único das regras de score (as views usam as mesmas funções),
equivalência do histórico de liderança com o laço da B3, A-118 (mês ausente
rende 0) na safra e no DCA, ausência de look-ahead nas métricas do portão,
regra D1 (sem carteira salvável, caixa à Selic), contabilidade de giro e IR
e o texto da leitura com o IFIX como alternativa.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import core.pesos_por_score as pps
from core import b3_oos_carteira as oos
from core import fii_safras_carteira as fsc

# ── ponto único ────────────────────────────────────────────────────────────


def test_views_usam_as_funcoes_do_ponto_unico():
    import views.empresas_b3 as eb
    import views.portfolio_b3 as pb

    assert eb._select_n_heuristica is pps.select_n_heuristica
    assert eb._weights_from_scores is pps.weights_from_scores
    assert eb._apply_cap_soft is pps.apply_cap_soft
    assert eb._apply_decay_penalty is pps.apply_decay_penalty
    assert pb._rank_ticker is pps.rank_ticker
    assert pb._pode_incluir_maior_participacao is pps.pode_incluir_maior_participacao


def test_leitura_honesta_mantem_o_texto_da_b3_e_aceita_ifix():
    resumo = {"n_safras": 4, "media": 0.01, "ic95": [-0.02, 0.03],
              "safras_negativas": [2020]}
    assert "dividir igualmente entre as empresas" in oos.leitura_honesta(resumo)
    texto = oos.leitura_honesta(resumo, "IFIX", alternativa="comprar o IFIX")
    assert "comprar o IFIX" in texto and "empresas" not in texto


# ── histórico de liderança: o laço da B3 ───────────────────────────────────


def _laco_b3(scores_por_ano, *, gamma, cap, soft):
    """Reescrita literal do trecho de `_processar_segmento` (cap estrito)."""
    from core.portfolio_constraints import minimum_assets_for_cap

    anos_lid: dict[str, int] = {}
    lids_ano, pesos_ano = {}, {}
    for ano in sorted(scores_por_ano):
        sm = pps.apply_decay_penalty(dict(scores_por_ano[ano]), anos_lid)
        ranked = sorted(sm.items(), key=lambda x: (-x[1], x[0]))
        n = pps.select_n_heuristica([s for _, s in ranked[:3]]) if len(ranked) >= 2 else 1
        n = min(len(ranked), max(n, minimum_assets_for_cap(cap)))
        lids = [t for t, _ in ranked[:n]]
        lids_ano[ano] = lids
        pesos_ano[ano] = (pps.apply_cap_soft(pps.weights_from_scores(lids, sm, gamma), cap, soft)
                          if len(lids) >= 2 else {lids[0]: 1.0})
        for t in list(anos_lid):
            if t not in lids:
                del anos_lid[t]
        for t in lids:
            anos_lid[t] = anos_lid.get(t, 0) + 1
    return lids_ano, pesos_ano


def test_historico_do_segmento_reproduz_o_laco_da_b3():
    rng = np.random.default_rng(3)
    nomes = [f"F{i:02d}11" for i in range(10)]
    scores = {ano: {t: float(rng.uniform(40, 80)) for t in nomes} for ano in range(2017, 2024)}
    hist = fsc.historico_do_segmento(scores, gamma=0.9, cap=0.25, soft=0.05)
    lids, pesos = _laco_b3(scores, gamma=0.9, cap=0.25, soft=0.05)
    assert hist["lids_por_ano"] == lids
    for ano in lids:
        assert hist["pesos_por_ano"][ano] == pytest.approx(pesos[ano])
        assert len(hist["lids_por_ano"][ano]) >= 4  # mínimo do cap 0,25
        assert max(hist["pesos_por_ano"][ano].values()) <= 0.25 + 1e-9


def test_decaimento_tira_quem_lidera_ha_anos():
    # Mesmo score todo ano: sem decaimento, o líder nunca mudaria.
    scores = {ano: {"A11": 80.0, "B11": 79.0, "C11": 78.0, "D11": 77.0, "E11": 76.5}
              for ano in range(2017, 2023)}
    hist = fsc.historico_do_segmento(scores, gamma=0.9, cap=0.25, soft=0.05)
    assert "E11" in hist["lids_por_ano"][2018]


# ── A-118 ──────────────────────────────────────────────────────────────────


def _matriz(dados: dict[str, list[float]], inicio="2020-04-30") -> pd.DataFrame:
    n = len(next(iter(dados.values())))
    return pd.DataFrame(dados, index=pd.date_range(inicio, periods=n, freq="ME"))


def test_mes_ausente_rende_zero_na_safra():
    com_nan = _matriz({"A11": [0.01, np.nan] + [0.01] * 10})
    com_zero = _matriz({"A11": [0.01, 0.0] + [0.01] * 10})
    assert fsc.retorno_composto(com_nan, 2020)["A11"] == pytest.approx(
        fsc.retorno_composto(com_zero, 2020)["A11"])


def test_mes_ausente_rende_zero_no_dca():
    kw = dict(lids_por_ano={2020: ["A11", "B11"]},
              pesos_por_ano={2020: {"A11": 0.5, "B11": 0.5}},
              universo_por_ano={2020: ["A11", "B11"]}, aporte=100.0, custo_compra=0.0)
    com_nan = _matriz({"A11": [0.01, np.nan, 0.02, 0.01], "B11": [0.0, 0.01, 0.01, 0.0]})
    com_zero = _matriz({"A11": [0.01, 0.0, 0.02, 0.01], "B11": [0.0, 0.01, 0.01, 0.0]})
    a = fsc.simular_segmento(com_nan, **kw)
    b = fsc.simular_segmento(com_zero, **kw)
    assert a[0] == pytest.approx(b[0]) and a[2] == pytest.approx(b[2])


def test_fundo_que_sai_da_bolsa_vira_caixa_e_e_reinvestido():
    m = _matriz({"A11": [0.0, 0.0, np.nan, np.nan], "B11": [0.0, 0.0, 0.0, 0.0]})
    v_est, _sel, _ew, contrib = fsc.simular_segmento(
        m, {2020: ["A11", "B11"]}, {2020: {"A11": 0.5, "B11": 0.5}},
        {2020: ["A11", "B11"]}, aporte=100.0, custo_compra=0.0)
    assert v_est == pytest.approx(400.0)  # nada se perde
    assert "A11" not in contrib and contrib["B11"] == pytest.approx(400.0)


# ── sem look-ahead ─────────────────────────────────────────────────────────


def _universo(seed=0, n_por_tipo=8, anos=range(2017, 2024)):
    rng = np.random.default_rng(seed)
    datas = pd.date_range("2017-01-31", "2024-12-31", freq="ME")
    fundos = {f"{t[:3].upper()}{i:02d}11": t for t in ("tijolo", "papel")
              for i in range(n_por_tipo)}
    qual = {tk: rng.normal() for tk in fundos}
    linhas = [{"date": d, "ticker": tk, "total_return": 0.01 + 0.004 * qual[tk]
               + rng.normal(0, 0.02), "price_return": 0.003 + rng.normal(0, 0.02)}
              for tk in fundos for d in datas]
    eleg = {a: [{"ticker": tk, "tipo": t, "type_score": 50 + 10 * qual[tk]}
                for tk, t in fundos.items()] for a in anos}
    return pd.DataFrame(linhas), eleg


def _selic(taxa=0.10):
    return pd.Series(taxa, index=pd.date_range("2016-12-31", "2024-12-31", freq="ME"))


def test_metricas_do_portao_nao_leem_depois_de_marco_da_safra():
    ret, eleg = _universo()
    total = fsc.matriz_de_retornos(ret)
    alterado = total.copy()
    alterado.loc[alterado.index >= pd.Timestamp("2021-04-01")] = 0.5  # futuro absurdo
    scores = fsc._scores_por_segmento(eleg)["tijolo"]
    hist = fsc.historico_do_segmento(scores, gamma=0.9, cap=0.25, soft=0.05)
    universo = {a: list(s) for a, s in scores.items()}
    p = fsc.PARAMS_DEFAULT
    a = fsc.metricas_pit(hist, 2021, total, universo, ano_inicio=2017, params=p)
    fsc._CACHE_RET.clear()
    b = fsc.metricas_pit(hist, 2021, alterado, universo, ano_inicio=2017, params=p)
    for chave in ("val_est", "val_selic", "val_ew", "maior", "rank_ic_mean", "ultimo_lid"):
        assert a[chave] == b[chave] or (
            isinstance(a[chave], float) and np.isnan(a[chave]) and np.isnan(b[chave]))


def test_aprovacao_da_safra_nao_muda_com_o_futuro():
    ret, eleg = _universo()
    futuro = ret.copy()
    futuro.loc[pd.to_datetime(futuro["date"]) >= pd.Timestamp("2022-04-01"), "total_return"] = -0.3
    sel = {a: 0.10 for a in eleg}
    kw = dict(selic_decisao=sel, selic_mensal=_selic(), params={"thr_selic": 0.0})
    a = fsc.medir_safras(eleg, ret, None, **kw)
    b = fsc.medir_safras(eleg, futuro, None, **kw)
    assert any(g["aprovado"] for r in a["safras"] for g in r["segmentos"])  # não é vácuo
    for ra, rb in zip(a["safras"], b["safras"]):
        if ra["safra"] <= 2022:
            assert [s["aprovado"] for s in ra["segmentos"]] == [s["aprovado"] for s in rb["segmentos"]]
            assert ra["pesos"] == rb["pesos"]


# ── D1 e contabilidade ─────────────────────────────────────────────────────


def _linha(safra, pesos, retornos, *, inviavel=False, selic=0.10, renda=None):
    return {"safra": safra, "pesos": pesos, "retornos": retornos, "renda": renda or {},
            "inviavel_no_cap": inviavel, "exige_revisao": inviavel, "mensuravel": True,
            "completa": True, "meses": 12, "selic": selic, "ifix": 0.05, "ew": 0.04}


def test_safra_sem_carteira_salvavel_fica_em_caixa_a_selic():
    linhas = [_linha(2020, {"A": .25, "B": .25, "C": .25, "D": .25},
                     {"A": .1, "B": .1, "C": .1, "D": .1}),
              _linha(2021, {"A": .5, "B": .5}, {"A": .2, "B": .2}, inviavel=True, selic=0.08)]
    out = fsc._contabilizar(linhas, oos.itens_da_cadeia(linhas), fsc.PARAMS_DEFAULT,
                            ir_calculado=True)
    assert out[1]["caixa"] is True
    assert out[1]["bruto"] == pytest.approx(0.08)
    # zerar a carteira: meia soma das diferenças, a convenção de
    # `portfolio_turnover` no período em caixa do backtest robusto
    assert out[1]["giro"] == pytest.approx(0.5)
    # ganho de 10% realizado: IR de 20% sobre 0,1/1,1 da carteira
    assert out[1]["ir"] == pytest.approx(0.2 * 0.1 / 1.1)
    assert out[1]["excesso_liquido_ifix"] == pytest.approx(out[1]["liquido"] - 0.05)


def test_entrada_paga_giro_cheio_e_provento_nao_paga_ir():
    custo = fsc.PARAMS_DEFAULT["transaction_cost"] + fsc.PARAMS_DEFAULT["slippage"]
    pesos = {"A": .25, "B": .25, "C": .25, "D": .25}
    linhas = [_linha(2020, pesos, dict.fromkeys(pesos, .1), renda=dict.fromkeys(pesos, .1)),
              _linha(2021, {"E": .25, "F": .25, "G": .25, "H": .25},
                     dict.fromkeys("EFGH", 0.0))]
    out = fsc._contabilizar(linhas, oos.itens_da_cadeia(linhas), fsc.PARAMS_DEFAULT,
                            ir_calculado=True)
    assert out[0]["giro"] == pytest.approx(1.0)
    assert out[0]["custo"] == pytest.approx(custo)
    assert out[0]["liquido"] == pytest.approx(0.1 - custo)
    # todo o ganho de 2020 veio de provento reinvestido: a venda não gera IR
    assert out[1]["ir"] == pytest.approx(0.0)


def test_primeira_safra_sem_resposta_e_aquecimento_e_nao_conta():
    linhas = [_linha(2019, {}, {}), _linha(2020, {"A": .25, "B": .25, "C": .25, "D": .25},
                                           dict.fromkeys("ABCD", .1))]
    out = fsc._contabilizar(linhas, oos.itens_da_cadeia(linhas), fsc.PARAMS_DEFAULT,
                            ir_calculado=True)
    assert out[0]["contada"] is False and out[1]["contada"] is True


def test_um_segmento_aprovado_nao_basta_para_o_cap():
    # Líder + maior de um tipo = no máximo 2 nomes; cap 0,25 exige 4.
    r = oos.montar_carteira([("papel", ["A", "B"], {"A": .3, "B": .25, "C": .25, "D": .2})],
                            cap=0.25, teto_setor=1.0, teto_ciclico=1.0)
    assert r["inviavel"] is True


def test_medicao_completa_marca_safra_em_curso_e_resume():
    ret, eleg = _universo()
    out = fsc.medir_safras(eleg, ret, None, selic_decisao={a: 0.10 for a in eleg},
                           selic_mensal=_selic(), params={"thr_selic": 0.0})
    assert out["status"] == "ok" and out["versao"] == fsc.VERSAO
    por_safra = {s["safra"]: s for s in out["safras"]}
    assert por_safra[2023]["completa"] is True
    assert set(out["resumo"]) >= {"excesso_liquido_ifix", "excesso_liquido_ew"}
    assert "resiliência ROIC − Selic (FII não tem ROIC)" in out["fora_do_porte"]


# ── achados da revisão do PR #790 ──────────────────────────────────────────


def test_selic_ausente_reprova_o_portao_em_vez_de_supor_taxa():
    ret, eleg = _universo()
    out = fsc.medir_safras(eleg, ret, None, selic_decisao={a: 0.10 for a in eleg},
                           params={"thr_selic": 0.0})
    segs = [g for r in out["safras"] for g in r["segmentos"]]
    assert segs and not any(g["aprovado"] for g in segs)
    assert any(g["motivo"] == "Selic ausente no período do portão" for g in segs)


def test_dca_na_selic_e_none_com_mes_sem_selic():
    m = _matriz({"A11": [0.01] * 4, "B11": [0.01] * 4})
    kw = dict(lids_por_ano={2020: ["A11", "B11"]},
              pesos_por_ano={2020: {"A11": 0.5, "B11": 0.5}},
              universo_por_ano={2020: ["A11", "B11"]}, aporte=100.0, custo_compra=0.0)
    so_depois = pd.Series(0.10, index=pd.date_range("2020-06-30", periods=3, freq="ME"))
    assert fsc.simular_segmento(m, selic_mensal=so_depois, **kw)[1] is None
    assert fsc.simular_segmento(m, selic_mensal=_selic(), **kw)[1] is not None


def test_ifix_com_menos_meses_que_a_carteira_fica_indisponivel():
    carteira = _matriz({"A11": [0.01] * 12})
    meses = fsc.meses_observados(carteira, 2020)
    ifix = pd.Series(0.01, index=carteira.index[:9])
    assert fsc.retorno_do_benchmark(ifix, 2020, meses) is None
    cheio = pd.Series(0.01, index=carteira.index)
    assert fsc.retorno_do_benchmark(cheio, 2020, meses) == pytest.approx(1.01 ** 12 - 1)


def test_renda_reinvestida_nao_isenta_a_alta_do_provento():
    from core.backtest_liquido import renda_reinvestida

    # provento de 10% (preço parado) e depois alta de 20% só de preço:
    # a renda é 10%, não os 12% de (composto total − composto preço)
    assert renda_reinvestida([0.10, 0.20], [0.0, 0.20]) == pytest.approx(0.10)
    # um mês só: as duas contas coincidem
    assert renda_reinvestida([0.07], [0.03]) == pytest.approx(0.04)
    assert renda_reinvestida([np.nan], [np.nan]) is None


def test_renda_da_safra_e_a_do_backtest_robusto_usam_a_mesma_conta():
    from core.backtest_liquido import renda_reinvestida

    total = _matriz({"A11": [0.10, 0.20] + [0.0] * 10, "B11": [0.02] * 12})
    preco = _matriz({"A11": [0.0, 0.20] + [0.0] * 10, "B11": [0.01] * 12})
    renda = fsc.renda_da_safra(total, preco, 2020)
    for tk in ("A11", "B11"):
        assert renda[tk] == pytest.approx(renda_reinvestida(total[tk].tolist(),
                                                            preco[tk].tolist()))
    assert renda["A11"] == pytest.approx(0.10)


def test_snapshot_de_outro_ano_nao_entra_com_corte_de_idade():
    from core.fii_validation import preparar_snapshots, ultimo_snapshot_na_data

    s = preparar_snapshots(pd.DataFrame([
        {"ticker": "VIVO11", "reference_date": "2021-03-31",
         "available_at": "2021-03-31T00:00:00Z"},
        {"ticker": "SAIU11", "reference_date": "2020-03-31",
         "available_at": "2020-03-31T00:00:00Z"},
    ]))
    d = pd.Timestamp("2021-03-31")
    assert set(ultimo_snapshot_na_data(s, d)["ticker"]) == {"VIVO11", "SAIU11"}
    assert set(ultimo_snapshot_na_data(s, d, idade_max_dias=0)["ticker"]) == {"VIVO11"}


def test_winsorizacao_nao_cria_perda_ficticia_no_ir():
    # preço parado, provento de 10%, retorno cortado para 5%: o corte sai do
    # provento (não há ganho de preço), e o razão não vê perda de capital
    assert fsc.renda_winsorizada(0.10, 0.05, 0.10) == pytest.approx(0.05)
    # com ganho de preço, o corte sai dele primeiro
    assert fsc.renda_winsorizada(0.30, 0.20, 0.08) == pytest.approx(0.08)
    assert fsc.renda_winsorizada(0.30, 0.05, 0.08) == pytest.approx(0.05)
    # corte para cima (perda extrema amortecida) vai todo para o preço
    assert fsc.renda_winsorizada(-0.60, -0.20, 0.05) == pytest.approx(0.05)
    # sem corte, nada muda
    assert fsc.renda_winsorizada(0.12, 0.12, 0.10) == pytest.approx(0.10)

    from core.backtest_liquido import IR_FII_GANHO, RastreadorIR

    r = RastreadorIR(IR_FII_GANHO)
    r.rebalancear({"A11": 1.0})
    r.evoluir({"A11": 0.05}, {"A11": fsc.renda_winsorizada(0.10, 0.05, 0.10)})
    r.rebalancear({})
    assert r.prejuizo == pytest.approx(0.0)


# ── adaptações FII: portão e nomes por tipo (22/11/2026) ───────────────────


def _m_portao(val_est, **refs):
    """Métricas PIT neutras fora da margem: Rank-IC ausente, seleção sem
    amostra, liderança recente."""
    base = {"val_est": val_est, "val_selic": 1000.0, "val_selic_liquida": 1000.0,
            "val_ifix": 1000.0, "val_ew": 0.0, "rank_ic_mean": float("nan"),
            "selecao_excesso": oos._selecao.intervalo_excesso([]),
            "ultimo_lid": {"A11": 2020}}
    return {**base, **refs}


def test_portao_selic_e_o_aprova_economico_da_b3():
    p = dict(fsc.PARAMS_DEFAULT)
    for val_est in (0.0, 900.0, 1100.0, 1149.0, 1151.0, 2000.0):
        for selic in (0.0, 1000.0, 1500.0):
            m = _m_portao(val_est, val_selic=selic)
            assert fsc.aprova_fii(m, p, 2021) == oos.aprova_economico(m, p, 2021)


def test_selecao_default_e_a_da_b3():
    ret, eleg = _universo()
    total = fsc.matriz_de_retornos(ret)
    scores = fsc._scores_por_segmento(eleg)["papel"]
    hist = fsc.historico_do_segmento(scores, gamma=0.9, cap=0.25, soft=0.05)
    m = fsc.metricas_pit(hist, 2021, total, {a: list(s) for a, s in scores.items()},
                         ano_inicio=2017, params=fsc.PARAMS_DEFAULT, selic_mensal=_selic())
    esperado = oos.selecao_do_segmento(
        hist, m, 2021, max_anos_lid=5, pode_incluir_maior=pps.pode_incluir_maior_participacao)
    assert fsc.selecao_fii(hist, m, 2021, fsc.PARAMS_DEFAULT) == esperado


def test_selic_liquida_desconta_o_ir_so_do_ganho():
    assert fsc.selic_liquida(1200.0, 1000.0, 0.15) == pytest.approx(1170.0)
    assert fsc.selic_liquida(900.0, 1000.0, 0.15) == pytest.approx(900.0)


def test_portao_selic_liquida_aprova_o_que_a_bruta_reprova():
    m = _m_portao(1180.0, val_selic=1200.0, val_selic_liquida=1170.0)
    p = {**fsc.PARAMS_DEFAULT, "thr_selic": 0.0}
    assert fsc.aprova_fii(m, p, 2021) == (False, "margem sobre a Selic")
    assert fsc.aprova_fii(m, {**p, "portao": "selic_liquida"}, 2021) == (True, "")
    assert fsc.aprova_fii(m, {**p, "portao": "selic_liquida", "thr_selic": 5.0}, 2021) == (
        False, "margem sobre a Selic líquida de IR")


def test_portao_ifix_exige_ganhar_do_indice_e_ignora_a_selic():
    p = {**fsc.PARAMS_DEFAULT, "portao": "ifix"}
    assert fsc.aprova_fii(_m_portao(1100.0, val_ifix=1200.0, val_selic=None), p, 2021) == (
        False, "margem sobre o IFIX")
    assert fsc.aprova_fii(_m_portao(1100.0, val_ifix=1050.0, val_selic=None), p, 2021) == (
        True, "")
    assert fsc.aprova_fii(_m_portao(1100.0, val_ifix=None), p, 2021) == (
        False, "IFIX ausente no período do portão")


def test_dca_no_ifix_empata_com_fundo_que_rende_o_ifix():
    m = _matriz({"A11": [0.01] * 4, "B11": [0.01] * 4})
    kw = dict(lids_por_ano={2020: ["A11", "B11"]},
              pesos_por_ano={2020: {"A11": 0.5, "B11": 0.5}},
              universo_por_ano={2020: ["A11", "B11"]}, aporte=100.0, custo_compra=0.0)
    ifix = pd.Series(0.01, index=m.index)
    det: dict = {}
    v_est = fsc.simular_segmento(m, ifix_mensal=ifix, details_out=det, **kw)[0]
    assert det["ifix"] == pytest.approx(v_est)
    assert det["aportado"] == pytest.approx(400.0)
    det_falta: dict = {}
    fsc.simular_segmento(m, ifix_mensal=ifix.drop(ifix.index[1]), details_out=det_falta, **kw)
    assert det_falta["ifix"] is None
    det_sem: dict = {}
    fsc.simular_segmento(m, details_out=det_sem, **kw)
    assert det_sem["ifix"] is None


def test_portao_ifix_nao_le_depois_de_marco_da_safra():
    ret, eleg = _universo()
    total = fsc.matriz_de_retornos(ret)
    ifix = pd.Series(0.008, index=total.index)
    futuro = ifix.copy()
    futuro[futuro.index >= pd.Timestamp("2021-04-01")] = -0.5
    scores = fsc._scores_por_segmento(eleg)["tijolo"]
    hist = fsc.historico_do_segmento(scores, gamma=0.9, cap=0.25, soft=0.05)
    universo = {a: list(s) for a, s in scores.items()}
    kw = dict(ano_inicio=2017, params=fsc.PARAMS_DEFAULT, selic_mensal=_selic())
    a = fsc.metricas_pit(hist, 2021, total, universo, ifix_mensal=ifix, **kw)
    b = fsc.metricas_pit(hist, 2021, total, universo, ifix_mensal=futuro, **kw)
    assert a["val_ifix"] is not None and a["val_ifix"] == b["val_ifix"]
    assert a["val_selic_liquida"] < a["val_selic"]


def test_medicao_com_portao_ifix_sem_ifix_reprova_tudo():
    ret, eleg = _universo()
    out = fsc.medir_safras(eleg, ret, None, selic_decisao={a: 0.10 for a in eleg},
                           selic_mensal=_selic(), params={"portao": "ifix"})
    segs = [g for r in out["safras"] for g in r["segmentos"]]
    # a primeira safra não tem histórico de DCA: reprova antes da referência
    motivos = {g["motivo"] for g in segs}
    assert motivos == {"sem patrimônio no backtest", "IFIX ausente no período do portão"}
    assert not any(g["aprovado"] for g in segs)


def _ifix_do_universo(ret):
    total = fsc.matriz_de_retornos(ret, "total_return")
    return total.mean(axis=1)


@pytest.mark.parametrize("fonte", ["XFIX11_proxy", "universe_median_proxy", None])
def test_portao_ifix_so_decide_com_o_ifix_oficial(fonte):
    ret, eleg = _universo()
    kw = dict(selic_decisao={a: 0.10 for a in eleg}, selic_mensal=_selic(),
              params={"portao": "ifix"})
    oficial = fsc.medir_safras(eleg, ret, _ifix_do_universo(ret),
                               ifix_fonte=fsc.FONTE_IFIX_OFICIAL, **kw)
    proxy = fsc.medir_safras(eleg, ret, _ifix_do_universo(ret), ifix_fonte=fonte, **kw)
    segs_of = [g for r in oficial["safras"] for g in r["segmentos"]]
    segs_px = [g for r in proxy["safras"] for g in r["segmentos"]]
    # com a série oficial o portão mede a margem; com substituto, não decide
    assert any(g["margem_ifix_pct"] is not None for g in segs_of)
    assert oficial["ifix_no_portao"] is True and proxy["ifix_no_portao"] is False
    assert proxy["ifix_fonte"] == fonte
    assert all(g["margem_ifix_pct"] is None for g in segs_px)
    assert not any(g["aprovado"] for g in segs_px)
    rotulo = f"IFIX oficial ausente (fonte: {fonte or 'não informada'})"
    assert {g["motivo"] for g in segs_px} == {"sem patrimônio no backtest", rotulo}
    # o substituto segue como referência do resultado, rotulado
    assert [r.get("ifix") for r in proxy["safras"]] == [
        r.get("ifix") for r in oficial["safras"]]


def test_portao_selic_ignora_a_fonte_do_ifix():
    ret, eleg = _universo()
    kw = dict(selic_decisao={a: 0.10 for a in eleg}, selic_mensal=_selic())
    a = fsc.medir_safras(eleg, ret, _ifix_do_universo(ret),
                         ifix_fonte=fsc.FONTE_IFIX_OFICIAL, **kw)
    b = fsc.medir_safras(eleg, ret, _ifix_do_universo(ret), ifix_fonte="XFIX11_proxy", **kw)
    assert [r["pesos"] for r in a["safras"]] == [r["pesos"] for r in b["safras"]]


def test_nomes_por_tipo_pega_os_n_primeiros_do_score():
    scores = {2020: {f"F{i}11": float(90 - i) for i in range(8)}}
    hist = fsc.historico_do_segmento(scores, gamma=0.9, cap=0.25, soft=0.05,
                                     nomes_por_tipo=3)
    assert hist["lids_por_ano"][2020] == ["F011", "F111", "F211"]
    pesos = hist["pesos_por_ano"][2020]
    assert sum(pesos.values()) == pytest.approx(1.0)
    assert max(pesos.values()) <= 1.0 / 3 + 1e-9  # cap do tipo = max(0,25; 1/3)
    seis = fsc.historico_do_segmento(scores, gamma=0.9, cap=0.25, soft=0.05,
                                     nomes_por_tipo=6)
    assert seis["lids_por_ano"][2020] == [f"F{i}11" for i in range(6)]
    assert max(seis["pesos_por_ano"][2020].values()) <= 0.25 + 1e-9
    curto = fsc.historico_do_segmento({2020: {"A11": 1.0, "B11": 2.0}}, gamma=0.9,
                                      cap=0.25, soft=0.05, nomes_por_tipo=5)
    assert curto["lids_por_ano"][2020] == ["B11", "A11"]


def test_nomes_por_tipo_entra_inteiro_e_um_tipo_basta_para_o_cap():
    ret, eleg = _universo()
    out = fsc.medir_safras(eleg, ret, None, selic_decisao={a: 0.10 for a in eleg},
                           selic_mensal=_selic(),
                           params={"thr_selic": 0.0, "nomes_por_tipo": 5})
    aprovados = [g for r in out["safras"] for g in r["segmentos"] if g["aprovado"]]
    assert aprovados and all(len(g["selecionados"]) == 5 for g in aprovados)
    for r in out["safras"]:
        n_aprov = sum(g["aprovado"] for g in r["segmentos"])
        if n_aprov:
            assert r["inviavel_no_cap"] is False
            assert len(r["pesos"]) == 5 * n_aprov
            assert max(r["pesos"].values()) <= 0.25 + 1e-9


@pytest.mark.parametrize("params", [{"portao": "cdi"}, {"nomes_por_tipo": 0},
                                    {"nomes_por_tipo": 2.5}, {"nomes_por_tipo": True}])
def test_parametro_invalido_levanta(params):
    ret, eleg = _universo()
    with pytest.raises(ValueError):
        fsc.medir_safras(eleg, ret, None, selic_decisao={}, params=params)


def test_grade_do_script_casa_limiar_com_portao():
    from scripts.medir_safras_fii import variantes

    grade = dict(variantes("selic,ifix", "15,0", "2", "b3,4"))
    assert grade["portao=selic|thr_selic=15|nomes_por_tipo=b3"] == {
        "portao": "selic", "thr_selic": 15.0, "nomes_por_tipo": None}
    assert grade["portao=ifix|thr_ifix=2|nomes_por_tipo=4"] == {
        "portao": "ifix", "thr_ifix": 2.0, "nomes_por_tipo": 4}
    assert len(grade) == 2 * 2 + 1 * 2
    # o default do script é a regra da B3
    assert variantes("selic", "15", "0", "b3") == [
        ("portao=selic|thr_selic=15|nomes_por_tipo=b3",
         {"portao": "selic", "thr_selic": 15.0, "nomes_por_tipo": None})]
