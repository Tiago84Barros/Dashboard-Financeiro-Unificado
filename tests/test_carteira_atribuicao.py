"""Atribuição Brinson-Fachler mensal e o encadeamento de Carino."""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

import core.carteira_atribuicao as ca


def _caso_livro():
    # Duas classes, contas feitas à mão:
    # R_b = 0,5·8% + 0,5·3% = 5,5%; R_p = 0,6·10% + 0,4·2% = 6,8%; excesso 1,3 p.p.
    w_p = {"acoes_br": 0.6, "renda_fixa": 0.4}
    w_b = {"acoes_br": 0.5, "renda_fixa": 0.5}
    r_p = {"acoes_br": 0.10, "renda_fixa": 0.02}
    r_b = {"acoes_br": 0.08, "renda_fixa": 0.03}
    return w_p, w_b, r_p, r_b


def test_caso_de_livro_duas_classes():
    bf = ca.brinson_fachler(*_caso_livro())
    assert bf["R_b"] == pytest.approx(0.055, abs=1e-15)
    assert bf["R_p"] == pytest.approx(0.068, abs=1e-15)
    a, rf = bf["efeitos"]["acoes_br"], bf["efeitos"]["renda_fixa"]
    # Ações: +10 p.p. de peso × (8% − 5,5%) = +0,25 p.p.; 50% × (10% − 8%) = +1 p.p.;
    # +10 p.p. × 2 p.p. = +0,2 p.p.
    assert a["alocacao"] == pytest.approx(0.0025, abs=1e-15)
    assert a["selecao"] == pytest.approx(0.0100, abs=1e-15)
    assert a["interacao"] == pytest.approx(0.0020, abs=1e-15)
    # Renda fixa: −10 p.p. × (3% − 5,5%) = +0,25 p.p.; 50% × (2% − 3%) = −0,5 p.p.;
    # −10 p.p. × −1 p.p. = +0,1 p.p.
    assert rf["alocacao"] == pytest.approx(0.0025, abs=1e-15)
    assert rf["selecao"] == pytest.approx(-0.0050, abs=1e-15)
    assert rf["interacao"] == pytest.approx(0.0010, abs=1e-15)
    soma = sum(v for ef in bf["efeitos"].values() for v in ef.values())
    assert soma == pytest.approx(0.013, abs=1e-15)
    assert soma == pytest.approx(bf["excesso"], abs=1e-15)


def test_classe_sem_posicao_nao_tem_selecao_nem_interacao():
    bf = ca.brinson_fachler({"acoes_br": 1.0, "fiis": 0.0},
                            {"acoes_br": 0.8, "fiis": 0.2},
                            {"acoes_br": 0.05, "fiis": None},
                            {"acoes_br": 0.04, "fiis": -0.01})
    f = bf["efeitos"]["fiis"]
    assert f["selecao"] == 0.0 and f["interacao"] == 0.0
    # Ficar fora de uma classe que rendeu menos que a meta é alocação positiva.
    assert f["alocacao"] == pytest.approx(-0.2 * (-0.01 - (0.8 * 0.04 - 0.2 * 0.01)))
    assert sum(v for ef in bf["efeitos"].values() for v in ef.values()) == \
        pytest.approx(bf["excesso"], abs=1e-15)


def test_classe_com_peso_e_sem_retorno_e_erro():
    with pytest.raises(ValueError):
        ca.brinson_fachler({"acoes_br": 0.5, "fiis": 0.5}, {"acoes_br": 0.5, "fiis": 0.5},
                           {"acoes_br": 0.01, "fiis": None}, {"acoes_br": 0.0, "fiis": 0.0})
    with pytest.raises(ValueError):
        ca.brinson_fachler({"acoes_br": 0.7}, {"acoes_br": 1.0}, {"acoes_br": 0.0},
                           {"acoes_br": 0.0})


def test_carino_um_periodo_preserva_os_efeitos():
    bf = ca.brinson_fachler(*_caso_livro())
    acc = ca.encadear_carino([bf])
    for c, ef in bf["efeitos"].items():
        for e, v in ef.items():
            assert acc["efeitos"][c][e] == pytest.approx(v, abs=1e-15)


def test_carino_soma_fecha_o_excesso_acumulado():
    w_p, w_b, r_p, r_b = _caso_livro()
    p1 = ca.brinson_fachler(w_p, w_b, r_p, r_b)
    p2 = ca.brinson_fachler({"acoes_br": 0.55, "renda_fixa": 0.45}, w_b,
                            {"acoes_br": -0.04, "renda_fixa": 0.011},
                            {"acoes_br": -0.06, "renda_fixa": 0.010})
    acc = ca.encadear_carino([p1, p2])
    r = 1.068 * (1 + p2["R_p"]) - 1
    b = 1.055 * (1 + p2["R_b"]) - 1
    assert acc["R_p"] == pytest.approx(r, abs=1e-15)
    assert acc["excesso"] == pytest.approx(r - b, abs=1e-15)
    soma = sum(v for ef in acc["efeitos"].values() for v in ef.values())
    assert abs(soma - (r - b)) < 1e-12
    assert abs(acc["residuo"]) < 1e-12
    # Somar os efeitos mensais sem ligar NÃO fecha: é o que o Carino corrige.
    ingenuo = p1["excesso"] + p2["excesso"]
    assert abs(ingenuo - (r - b)) > 1e-4


def test_carino_fecha_em_doze_meses_aleatorios_e_mes_sem_excesso():
    rnd = random.Random(7)
    periodos = []
    for i in range(12):
        w = rnd.random()
        rb = {c: rnd.uniform(-0.08, 0.08) for c in ca.CLASSES}
        rp = {c: rb[c] + rnd.uniform(-0.03, 0.03) for c in ca.CLASSES}
        wp = ca.normalizar({c: rnd.random() + (w if c == "exterior" else 0) for c in ca.CLASSES})
        wb = {"renda_fixa": 0.3, "acoes_br": 0.25, "fiis": 0.2, "exterior": 0.25}
        if i == 5:  # mês com R_p = R_b: k = 1/(1+R)
            wp, rp = dict(wb), dict(rb)
        periodos.append(ca.brinson_fachler(wp, wb, rp, rb))
    acc = ca.encadear_carino(periodos)
    assert abs(acc["residuo"]) < 1e-12
    assert math.isfinite(acc["excesso"])


def test_encadear_sem_periodos():
    assert ca.encadear_carino([])["excesso"] is None


def _pregoes(ini: date, fim: date) -> list[date]:
    d, out = ini, []
    while d <= fim:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_fronteiras_so_meses_fechados_e_inteiros():
    dias = _pregoes(date(2026, 4, 14), date(2026, 8, 31))
    dias = [d for d in dias if not (date(2026, 6, 24) <= d <= date(2026, 6, 30))]
    meses = ca.fronteiras_mensais(dias, hoje=date(2026, 9, 3))
    rotulos = [m[0] for m in meses]
    # Junho parou no dia 23 (7 dias antes do fim): nem junho nem julho (que começaria nele) entram.
    assert rotulos == ["2026-05", "2026-08"]
    assert meses[0][1:] == (date(2026, 4, 30), date(2026, 5, 29))
    # Mês corrente nunca entra, mesmo com pregão no último dia útil.
    dias2 = _pregoes(date(2026, 7, 1), date(2026, 9, 30))
    assert [m[0] for m in ca.fronteiras_mensais(dias2, hoje=date(2026, 9, 30))] == ["2026-08"]


def test_retorno_no_intervalo_nao_trata_buraco_como_zero():
    dias = _pregoes(date(2026, 7, 1), date(2026, 7, 31))
    serie = [{"data": d, "retorno": 0.01} for d in dias]
    r, n_med, n = ca.retorno_no_intervalo(serie, date(2026, 6, 30), date(2026, 7, 31))
    assert n_med == n == len(dias)
    assert r == pytest.approx(1.01 ** len(dias) - 1)
    serie[3]["retorno"] = None  # 1 de 23 sem medida: vale, e o dia sai do produto
    r, n_med, _ = ca.retorno_no_intervalo(serie, date(2026, 6, 30), date(2026, 7, 31))
    assert n_med == len(dias) - 1
    assert r == pytest.approx(1.01 ** (len(dias) - 1) - 1)
    for s in serie[:5]:
        s["retorno"] = None  # abaixo de 90%: o mês não tem retorno da classe
    assert ca.retorno_no_intervalo(serie, date(2026, 6, 30), date(2026, 7, 31))[0] is None


def test_retorno_preco_exato_e_com_repeticao():
    cot = {date(2026, 6, 30): 100.0, date(2026, 7, 30): 110.0}
    assert ca.retorno_preco(cot, date(2026, 6, 30), date(2026, 7, 31)) is None
    assert ca.retorno_preco(cot, date(2026, 6, 30), date(2026, 7, 31), ffill_dias=4) == \
        pytest.approx(0.10)


def test_mes_sem_referencia_fica_incompleto_e_nao_zero():
    meta = {"renda_fixa": 0.3, "acoes_br": 0.25, "fiis": 0.2, "exterior": 0.25}
    w_p = {"renda_fixa": 0.4, "acoes_br": 0.4, "fiis": 0.1, "exterior": 0.1}
    r_p = {"renda_fixa": 0.01, "acoes_br": 0.02, "fiis": 0.0, "exterior": 0.01}
    r_b = {"renda_fixa": 0.01, "acoes_br": 0.03, "fiis": None, "exterior": 0.0}
    m = ca.mes_atribuicao("2026-09", w_p, meta, r_p, r_b)
    assert m["completo"] is False
    assert any("IFIX" in x for x in m["motivos"])
    assert "efeitos" not in m
    r_b["fiis"] = -0.01
    r_p["exterior"] = None
    m = ca.mes_atribuicao("2026-09", w_p, meta, r_p, r_b)
    assert m["completo"] is False and any("Exterior" in x for x in m["motivos"])
    r_p["exterior"] = 0.02
    m = ca.mes_atribuicao("2026-09", w_p, meta, r_p, r_b)
    assert m["completo"] is True
    assert sum(v for ef in m["efeitos"].values() for v in ef.values()) == \
        pytest.approx(m["excesso"], abs=1e-15)


def _atr_exemplo() -> dict:
    meta = {"renda_fixa": 0.3, "acoes_br": 0.25, "fiis": 0.2, "exterior": 0.25}
    w_p = {"renda_fixa": 0.37, "acoes_br": 0.33, "fiis": 0.06, "exterior": 0.24}
    r_p = {"renda_fixa": 0.012, "acoes_br": 0.057, "fiis": 0.0035, "exterior": -0.0115}
    r_b = {"renda_fixa": 0.012, "acoes_br": 0.035, "fiis": -0.003, "exterior": -0.02}
    jul = ca.mes_atribuicao("2026-07", w_p, meta, r_p, r_b)
    set_ = ca.mes_atribuicao("2026-09", w_p, meta, r_p, {**r_b, "fiis": None})
    return {"disponivel": True, "meta": meta, "meta_versao": 1,
            "meta_desde": date(2026, 9, 29), "meses": [jul, set_],
            "meses_encadeados": ["2026-07"], "contiguo": True,
            "acumulado": ca.encadear_carino([jul]), "pct_fora_politica": 0.10}


def test_resumo_e_bloco_do_prompt():
    atr = _atr_exemplo()
    resumo = ca.resumo_atribuicao(atr)
    assert resumo.startswith("Contra a meta, a carteira ficou +")
    assert "07/2026" in resumo and "maior item: seleção em Ações Brasil" in resumo
    bloco = ca.bloco_atribuicao_para_prompt(atr)
    assert bloco.startswith("ATRIBUIÇÃO CONTRA A META")
    assert "Carino" in bloco and "29/09/2026" in bloco
    assert "- Resumo: " + resumo in bloco
    assert "09/2026: incompleto" in bloco and "IFIX" in bloco
    indisp = ca.bloco_atribuicao_para_prompt({"disponivel": False, "motivo": "sem meta"})
    assert "indisponível" in indisp and "sem meta" in indisp
    assert ca.bloco_atribuicao_para_prompt(None) == ""


def test_contexto_geral_anexa_atribuicao_e_a_regra_de_posicao():
    from core.llm_context_carteira import build_carteira_geral_context

    carteira = {"posicoes": [{"ticker": "BBAS3", "classe": "Ações BR",
                              "valor_mercado": 100.0, "pct_carteira": 100.0}],
                "por_classe": [], "por_setor": [], "total_mercado": 100.0}
    ctx = build_carteira_geral_context(carteira, {}, atribuicao=_atr_exemplo())
    assert "ATRIBUIÇÃO CONTRA A META" in ctx
    assert ca.REGRA_ATRIBUICAO in ctx
    assert "COMECE a resposta com a frase do Resumo" in ca.REGRA_ATRIBUICAO
    # O bloco vem antes das regras de leitura, como o do risco.
    assert ctx.index("ATRIBUIÇÃO CONTRA A META") < ctx.index("REGRAS DE LEITURA")
    sem = build_carteira_geral_context(carteira, {})
    assert "ATRIBUIÇÃO" not in sem and ca.REGRA_ATRIBUICAO not in sem


def test_loader_em_mock_nao_consulta_banco(monkeypatch):
    monkeypatch.setattr(ca.settings, "MOCK_MODE", True, raising=False)
    monkeypatch.setattr(ca, "_atribuicao_real",
                        lambda: (_ for _ in ()).throw(AssertionError("consultou o banco")))
    r = ca.get_atribuicao_carteira.__wrapped__() \
        if hasattr(ca.get_atribuicao_carteira, "__wrapped__") else ca.get_atribuicao_carteira()
    assert r["disponivel"] is False and r["data_source"] == "mock"


def test_contiguidade():
    assert ca._contiguo(["2026-07", "2026-08"])
    assert ca._contiguo(["2026-12", "2027-01"])
    assert not ca._contiguo(["2026-05", "2026-07"])


# ── Referência dos FIIs: IFIX oficial → IFIX spot → XFIX11 ───────────────────

def test_retorno_mensal_oficial_pela_chave_do_mes():
    oficial = {"2025-12": 100.0, "2026-01": 102.0, "2026-09": 3755.22, "2026-08": 3762.79}
    assert ca.retorno_mensal_oficial(oficial, "2026-01") == pytest.approx(0.02)
    assert ca.retorno_mensal_oficial(oficial, "2026-09") == pytest.approx(3755.22 / 3762.79 - 1)
    assert ca.retorno_mensal_oficial(oficial, "2026-02") is None  # sem o fim do mês
    assert ca.retorno_mensal_oficial({"2026-03": 0.0, "2026-04": 1.0}, "2026-04") is None


def test_referencia_fiis_prioridade_e_uma_fonte_por_mes():
    t0, t1 = date(2026, 8, 31), date(2026, 9, 30)
    oficial = {"2026-08": 100.0, "2026-09": 99.0}
    diario = {t0: 200.0, t1: 210.0}
    yahoo = {t0: 10.0, t1: 10.5}
    brapi = {t0: 10.0, t1: 9.0}
    r, rot = ca.referencia_fiis("2026-09", t0, t1, oficial, diario, (yahoo, brapi))
    assert (r, rot) == (pytest.approx(-0.01), "IFIX")
    r, rot = ca.referencia_fiis("2026-09", t0, t1, {}, diario, (yahoo, brapi))
    assert (r, rot) == (pytest.approx(0.05), "IFIX spot")
    # IFIX diário só no fim do mês: não se costura com outra série no início.
    r, rot = ca.referencia_fiis("2026-09", t0, t1, {}, {t1: 210.0}, (yahoo, brapi))
    assert (r, rot) == (pytest.approx(0.05), "XFIX11")
    r, rot = ca.referencia_fiis("2026-09", t0, t1, {}, {}, ({t1: 10.5}, brapi))
    assert (r, rot) == (pytest.approx(-0.10), "XFIX11")
    assert ca.referencia_fiis("2026-09", t0, t1, {}, {}, ({}, {})) == (None, None)
