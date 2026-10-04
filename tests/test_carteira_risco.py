"""Série diária da carteira e métricas de risco (INV-A4), em séries sintéticas."""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from core import carteira_risco as cr
from core.llm_context_carteira import build_carteira_geral_context


def _dias(n: int, inicio: date = date(2026, 4, 1)) -> list[date]:
    out, d = [], inicio
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ── TWR pelos fluxos ─────────────────────────────────────────────────────────

def test_aporte_no_meio_nao_muda_o_retorno():
    """Comprar 100 cotas no 3º dia não pode aparecer como ganho."""
    dias = _dias(5)
    precos = {"AAA": [10.0, 11.0, 12.1, 11.5, 12.0]}
    sem = cr.retornos_diarios(dias, {"AAA": [100, 100, 100, 100, 100]}, precos)
    com = cr.retornos_diarios(dias, {"AAA": [100, 100, 200, 200, 200]}, precos)
    r_sem = [s["retorno"] for s in sem]
    r_com = [s["retorno"] for s in com]
    assert r_com == pytest.approx(r_sem)
    assert cr.twr(r_com) == pytest.approx(12.0 / 10.0 - 1)


def test_resgate_no_meio_nao_muda_o_retorno():
    dias = _dias(4)
    precos = {"AAA": [10.0, 9.0, 9.9, 10.5], "BBB": [50.0, 50.5, 51.0, 51.0]}
    base = cr.retornos_diarios(dias, {"AAA": [10] * 4, "BBB": [2] * 4}, precos)
    resg = cr.retornos_diarios(dias, {"AAA": [10, 10, 4, 4], "BBB": [2, 2, 2, 2]}, precos)
    # Até o dia do resgate os retornos coincidem; depois o peso muda, como deve.
    assert resg[0]["retorno"] == pytest.approx(base[0]["retorno"])
    assert resg[1]["retorno"] == pytest.approx(base[1]["retorno"])


def test_forma_de_fluxo_igual_a_forma_de_holdings():
    dias = _dias(3)
    q = {"AAA": [10, 15, 15], "BBB": [4, 4, 1]}
    p = {"AAA": [20.0, 21.0, 20.5], "BBB": [100.0, 98.0, 101.0]}
    serie = cr.retornos_diarios(dias, q, p)
    for i, s in enumerate(serie, start=1):
        ant = sum(q[t][i - 1] * p[t][i - 1] for t in q)
        hold = sum(q[t][i - 1] * p[t][i] for t in q)
        assert s["retorno"] == pytest.approx(hold / ant - 1)


def test_provento_entra_como_renda():
    dias = _dias(3)
    serie = cr.retornos_diarios(dias, {"AAA": [100] * 3}, {"AAA": [10.0, 10.0, 10.0]},
                                {"AAA": {dias[2]: 50.0}})
    assert serie[0]["retorno"] == pytest.approx(0.0)
    assert serie[1]["retorno"] == pytest.approx(50.0 / 1000.0)


def test_ativo_sem_preco_fica_fora_nao_vira_zero():
    dias = _dias(3)
    q = {"AAA": [10] * 3, "BBB": [10] * 3}
    p = {"AAA": [10.0, 11.0, 12.1], "BBB": [10.0, None, 10.0]}
    serie = cr.retornos_diarios(dias, q, p)
    # Dia 2: só AAA mede (+10%); BBB sem preço não puxa para +5%.
    assert serie[0]["retorno"] == pytest.approx(0.10)
    assert serie[0]["cobertura"] == pytest.approx(0.5)
    assert serie[1]["retorno"] == pytest.approx(0.10)


def test_dia_sem_nenhum_preco_e_none():
    dias = _dias(2)
    serie = cr.retornos_diarios(dias, {"AAA": [10, 10]}, {"AAA": [10.0, None]})
    assert serie[0]["retorno"] is None
    assert cr.twr([s["retorno"] for s in serie]) is None


def test_salto_acima_de_35_porcento_e_descartado():
    dias = _dias(3)
    q = {"AAA": [10] * 3, "BBB": [10] * 3}
    p = {"AAA": [10.0, 10.1, 10.2], "BBB": [10.0, 100.0, 100.5]}
    serie = cr.retornos_diarios(dias, q, p)
    assert serie[0]["saltos"] == ["BBB"]
    assert serie[0]["retorno"] == pytest.approx(0.01)


def test_cobertura_conta_ativo_antes_da_primeira_cotacao():
    dias = _dias(3)
    q = {"AAA": [10] * 3, "USD": [1] * 3}
    p = {"AAA": [10.0, 10.0, 10.0], "USD": [None, None, 100.0]}
    serie = cr.retornos_diarios(dias, q, p)
    assert serie[0]["cobertura"] == pytest.approx(0.5)


# ── Métricas com valor conhecido ─────────────────────────────────────────────

def test_drawdown_conhecido():
    # Índice: 1,10 → 0,88 (−20% do pico) → 0,968 → 1,0648.
    rets = [0.10, -0.20, 0.10, 0.10]
    dd = cr.drawdown(rets)
    assert dd["max"] == pytest.approx(0.20)
    assert dd["i_pico"] == 0 and dd["i_vale"] == 1
    assert dd["atual"] == pytest.approx(1 - 1.0648 / 1.10)


def test_drawdown_zero_em_serie_so_de_alta():
    dd = cr.drawdown([0.01] * 10)
    assert dd["max"] == 0.0 and dd["atual"] == 0.0


def test_var_historico_conhecido():
    # 100 retornos de −0,50% a +0,49%: o corte de 5% (estatística de ordem
    # floor(0,05·99) = 4) é −0,46%.
    rets = [(i - 50) / 10000 for i in range(100)]
    assert cr.var_historico(rets) == pytest.approx(0.0046)


def test_var_exige_amostra_minima():
    assert cr.var_historico([-0.01] * 19) is None
    assert cr.var_mensal([-0.001] * 62) is None


def test_var_mensal_janelas_compostas():
    rets = [-0.001] * 63
    esperado = 1 - 0.999 ** 21
    assert cr.var_mensal(rets) == pytest.approx(esperado)
    assert len(cr.retornos_em_janela(rets)) == 63 - 21 + 1


def test_volatilidade_anual():
    rets = [0.01, -0.01] * 20
    dp = math.sqrt(sum((r - 0) ** 2 for r in rets) / (len(rets) - 1))
    assert cr.volatilidade_anual(rets) == pytest.approx(dp * math.sqrt(252))


def test_beta_de_carteira_alavancada():
    ref = [0.01 * ((i % 5) - 2) for i in range(30)]
    b, n = cr.beta([2 * r for r in ref], ref)
    assert b == pytest.approx(2.0) and n == 30


def test_sharpe_sem_excesso_e_none_ou_zero():
    cdi = [0.0004] * 30
    rets = [0.0004 + (0.001 if i % 2 else -0.001) for i in range(30)]
    assert cr.sharpe(rets, cdi) == pytest.approx(0.0, abs=1e-9)


# ── Quantidade ancorada nas fotos ────────────────────────────────────────────

def test_intervalo_conciliado_segue_os_eventos():
    dias = _dias(10)
    ancoras = {dias[0]: 100.0, dias[9]: 150.0}
    eventos = [(dias[4], 50.0)]
    qs, info = cr.serie_quantidade(dias, ancoras, [eventos])
    assert qs[3] == 100.0 and qs[4] == 150.0 and qs[9] == 150.0
    assert info == {"conciliados": 1, "degraus": 0}


def test_intervalo_que_nao_fecha_vira_degrau_na_foto():
    dias = _dias(10)
    ancoras = {dias[0]: 100.0, dias[9]: 150.0}
    eventos = [(dias[4], 20.0)]  # não leva 100 a 150
    qs, info = cr.serie_quantidade(dias, ancoras, [eventos])
    assert qs[8] == 100.0 and qs[9] == 150.0
    assert info == {"conciliados": 0, "degraus": 1}


def test_segunda_fonte_vale_quando_a_primeira_nao_fecha():
    dias = _dias(10)
    ancoras = {dias[0]: 10.0, dias[9]: 30.0}
    qs, _ = cr.serie_quantidade(dias, ancoras, [[(dias[2], 5.0)], [(dias[6], 20.0)]])
    assert qs[5] == 10.0 and qs[6] == 30.0


def test_antes_da_primeira_foto_negativo_vira_none():
    dias = _dias(6)
    ancoras = {dias[3]: 10.0}
    qs, _ = cr.serie_quantidade(dias, ancoras, [[(dias[1], 30.0)]])
    assert qs[0] is None and qs[1] == 10.0


def test_degrau_de_quantidade_nao_cria_retorno():
    """A foto que a reconstrução não explica entra como fluxo no preço."""
    dias = _dias(10)
    qs, _ = cr.serie_quantidade(dias, {dias[0]: 100.0, dias[9]: 300.0}, [])
    serie = cr.retornos_diarios(dias, {"AAA": qs}, {"AAA": [10.0] * 10})
    assert all(s["retorno"] == pytest.approx(0.0) for s in serie)


def test_recuar_pregoes_no_calendario():
    dias = _dias(10)
    assert cr.recuar_pregoes(dias[5], dias, 2) == dias[3]
    # Fora do calendário: dias úteis (sem feriado: seg 05/01 → qui 01/01).
    assert cr.recuar_pregoes(date(2026, 1, 5), dias, 2) == date(2026, 1, 1)


# ── Preço modelado e alinhamento ─────────────────────────────────────────────

def test_preco_modelado_pelo_cdi_rende_o_cdi_da_vespera():
    dias = _dias(4)
    hoje = dias[-1]
    cdi = {d: 0.05 for d in dias}  # 0,05% ao dia
    ps = cr.preco_modelado_cdi(dias, 100.0, hoje, cdi)
    assert ps[-1] == pytest.approx(100.0)
    assert ps[1] / ps[0] - 1 == pytest.approx(0.0005)
    assert cr.cdi_por_dia(dias, cdi) == pytest.approx([0.0005] * 3)


def test_alinhar_precos_repete_so_ate_o_limite():
    dias = _dias(6, date(2026, 4, 6))  # seg 06/04 a seg 13/04
    cot = {date(2026, 4, 6): 1.0}
    out = cr.alinhar_precos(dias, cot, ffill_dias=4)
    assert out[:5] == [1.0] * 5 and out[5] is None
    assert cr.alinhar_precos(dias, cot)[1] is None


# ── Política e contexto ──────────────────────────────────────────────────────

def test_alerta_quando_queda_passa_da_tolerancia():
    assert cr.alerta_drawdown(0.25, 0.05, 20)["nivel"] == "excedido"
    assert cr.alerta_drawdown(0.25, 0.22, 20)["nivel"] == "atual_excede"
    assert cr.alerta_drawdown(0.10, 0.02, 20)["nivel"] == "dentro"
    assert cr.alerta_drawdown(0.10, 0.02, None)["nivel"] == "sem_tolerancia"


def _risco_exemplo() -> dict:
    return {
        "disponivel": True, "frequencia": "diária",
        "inicio": date(2026, 4, 14), "fim": date(2026, 10, 2), "n_dias": 119,
        "twr": 0.0086, "cdi_periodo": 0.0645, "vol_anual": 0.11,
        "max_drawdown": 0.083, "drawdown_atual": 0.0, "var_1d": 0.0117,
        "var_1m": 0.0415, "sharpe": -0.98, "beta": 0.45, "tolerancia_pct": 15.0,
        "alerta": cr.alerta_drawdown(0.083, 0.0, 15.0),
        "cobertura": {"pct_observado": 0.563, "pct_modelado": 0.153, "pct_fora": 0.285},
        "excluidos": [{"motivo_curto": "CDB/renda fixa sem PU diário"}],
    }


def test_bloco_do_prompt_traz_cobertura_periodo_e_metricas():
    bloco = cr.bloco_risco_para_prompt(_risco_exemplo())
    assert "14/04/2026 a 02/10/2026" in bloco
    assert "56.3%" in bloco and "15.3%" in bloco and "28.5%" in bloco
    assert "Volatilidade anualizada: 11.0%" in bloco
    assert "VaR histórico 95%: 1 dia 1.17%" in bloco
    assert "15%" in bloco and "dentro da tolerância" in bloco
    assert "R$" not in bloco


def test_bloco_indisponivel_diz_o_motivo():
    bloco = cr.bloco_risco_para_prompt({"disponivel": False, "motivo": "sem cotações"})
    assert "indisponível" in bloco and "sem cotações" in bloco


def test_contexto_geral_anexa_risco_e_a_regra():
    carteira = {"posicoes": [], "por_classe": []}
    sem = build_carteira_geral_context(carteira)
    com = build_carteira_geral_context(carteira, risco=_risco_exemplo())
    assert "RISCO DA CARTEIRA" not in sem
    assert "RISCO DA CARTEIRA" in com
    assert cr.REGRA_RISCO in com
    assert com.index("RISCO DA CARTEIRA") < com.index("REGRAS DE LEITURA")


def test_loader_em_mock_nao_consulta_banco(monkeypatch):
    monkeypatch.setattr(cr.settings, "MOCK_MODE", True, raising=False)
    r = cr.get_risco_carteira.__wrapped__() if hasattr(cr.get_risco_carteira, "__wrapped__") \
        else cr.get_risco_carteira()
    assert r["disponivel"] is False and r["data_source"] == "mock"
