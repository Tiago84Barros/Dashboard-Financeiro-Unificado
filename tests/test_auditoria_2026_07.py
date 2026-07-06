"""Regressões da auditoria cruzada 2026-07-04 (parecer Codex + verificação Claude).

Cobre os defeitos semânticos que a suíte não pegava:
  - pesos setoriais com acentos/palavra parcial (Consumo não Cíclico → perfil errado)
  - flag_survivorship_universe reportando viés zero por bug de timedelta
  - "Ledoit-Wolf" que não dependia de T (heurística ad hoc)
  - min-variance "ótimo" que era projeção heurística (sem solver exato)
  - anualização 252-vs-12 no Black-Litterman → Markowitz
  - escala de alpha_selic (gravado em pontos percentuais pela Criação)
"""
import datetime as dt

import numpy as np
import pytest


# ── Pesos setoriais ──────────────────────────────────────────────────────────

def test_pesos_consumo_nao_ciclico_nao_recebe_perfil_ciclico():
    from views.empresas_b3 import _PESOS_SETOR, _get_pesos_setor, _norm_pesos
    got = _get_pesos_setor("Consumo não Cíclico")
    assert got == _norm_pesos(_PESOS_SETOR["consumo nao ciclico"])
    assert got != _norm_pesos(_PESOS_SETOR["consumo ciclico"])


def test_pesos_consumo_ciclico_mantem_perfil():
    from views.empresas_b3 import _PESOS_SETOR, _get_pesos_setor, _norm_pesos
    assert _get_pesos_setor("Consumo Cíclico") == _norm_pesos(
        _PESOS_SETOR["consumo ciclico"])


@pytest.mark.parametrize("setor,chave", [
    ("Saúde", "saude"),
    ("Comunicações", "comunicacoes"),
    ("Utilidade Pública", "utilidade publica"),
    ("Petróleo, Gás e Biocombustíveis", "petroleo"),
    ("Materiais Básicos", "materiais basicos"),
    ("Bens Industriais", "bens industriais"),
    ("Financeiro e Outros", "financeiro"),
    ("Tecnologia da Informação", "tecnologia"),
])
def test_pesos_setores_acentuados_mapeiam_para_perfil(setor, chave):
    from views.empresas_b3 import _PESOS_SETOR, _get_pesos_setor, _norm_pesos
    assert _get_pesos_setor(setor) == _norm_pesos(_PESOS_SETOR[chave])


def test_pesos_setor_desconhecido_cai_no_generico():
    from views.empresas_b3 import _PESOS_GENERICO, _get_pesos_setor, _norm_pesos
    assert _get_pesos_setor("Setor Inexistente XYZ") == _norm_pesos(_PESOS_GENERICO)


# ── Survivorship ─────────────────────────────────────────────────────────────

def test_flag_survivorship_hoje_zero_por_construcao():
    from core.survivorship import flag_survivorship_universe
    res = flag_survivorship_universe(["PETR4", "VALE3"], data_ref=dt.date.today())
    assert res["n_delisted"] == 0


def test_flag_survivorship_passado_detecta_faltantes():
    # Bug corrigido: data_ref - (data_ref - data_ref) == data_ref fazia a
    # auditoria reportar viés zero sempre que data_ref era hoje; com data_ref
    # no passado a lista curada 2010-2025 TEM deslistadas vivas naquela data.
    from core.survivorship import flag_survivorship_universe
    res = flag_survivorship_universe(["PETR4", "VALE3"], data_ref=dt.date(2013, 1, 1))
    assert res["n_delisted"] > 0
    assert res["cobertura_lista_curada"] < 1.0
    assert res["cobertura_estimada"] is None
    assert res["vies_estimado_bps"] is None
    assert res["universo_completo"] is False


# ── Ledoit-Wolf real ─────────────────────────────────────────────────────────

def test_ledoit_wolf_intensidade_decresce_com_T():
    from core.markowitz import ledoit_wolf_shrinkage
    rng = np.random.default_rng(42)
    K = 8
    A = rng.normal(size=(K, K))
    cov_true = A @ A.T / K + np.eye(K)
    L = np.linalg.cholesky(cov_true)

    def draw(T):
        return (L @ rng.normal(size=(K, T))).T

    _, a_curta = ledoit_wolf_shrinkage(draw(15))
    _, a_longa = ledoit_wolf_shrinkage(draw(3000))
    assert 0.0 <= a_longa <= 1.0 and 0.0 <= a_curta <= 1.0
    # propriedade LW: erro de amostragem (b²) cai ~1/T ⇒ menos shrinkage
    assert a_longa < a_curta
    assert a_longa < 0.15


def test_ledoit_wolf_matriz_simetrica_e_psd():
    from core.markowitz import ledoit_wolf_shrinkage
    rng = np.random.default_rng(7)
    sigma, alpha = ledoit_wolf_shrinkage(rng.normal(size=(36, 6)))
    assert np.allclose(sigma, sigma.T, atol=1e-10)
    assert np.linalg.eigvalsh(sigma).min() > -1e-10


# ── Min-variance com solver exato ────────────────────────────────────────────

def test_min_variance_solver_exato_e_restricoes():
    from core.markowitz import min_variance_capped
    rng = np.random.default_rng(11)
    rets = rng.normal(0.01, 0.05, size=(60, 4))
    res = min_variance_capped(list("ABCD"), rets, cap=0.40)
    w = np.array([res.weights[t] for t in "ABCD"])
    assert abs(w.sum() - 1.0) < 1e-6
    assert (w >= -1e-12).all() and (w <= 0.40 + 1e-9).all()
    # scipy está em requirements: o solver exato deve assumir (cvxpy opcional)
    assert res.method in ("cvxpy", "slsqp")
    assert res.converged
    # otimalidade fraca: variância não pode ser pior que equal-weight
    from core.markowitz import ledoit_wolf_shrinkage
    cov, _ = ledoit_wolf_shrinkage(rets)
    w_eq = np.full(4, 0.25)
    assert res.expected_variance <= float(w_eq @ cov @ w_eq) + 1e-12


def test_min_variance_cap_inviavel_falha_explicitamente():
    # Não relaxa silenciosamente uma restrição que a UI promete respeitar.
    from core.markowitz import min_variance_capped
    from core.portfolio_constraints import InfeasiblePortfolioConstraint
    rng = np.random.default_rng(3)
    with pytest.raises(InfeasiblePortfolioConstraint):
        min_variance_capped(
            ["A", "B"], rng.normal(0, 0.05, size=(40, 2)), cap=0.30
        )


# ── Black-Litterman: anualização coerente com a frequência ───────────────────

def test_bl_vol_escala_com_periods_per_year():
    from core.black_litterman import BLView, apply_bl_to_markowitz
    rng = np.random.default_rng(5)
    tickers = ["AAA", "BBB", "CCC"]
    rets = rng.normal(0.01, 0.06, size=(36, 3))          # retornos MENSAIS
    prior = np.full(3, 0.10)
    view = BLView(view_type="absolute", tickers=["AAA"], weights=[1.0],
                  expected_return=0.15, confidence=0.6)
    opt12 = apply_bl_to_markowitz(tickers, prior, rets, [view],
                                  cap=0.40, periods_per_year=12)
    opt252 = apply_bl_to_markowitz(tickers, prior, rets, [view],
                                   cap=0.40, periods_per_year=252)
    ratio = opt252["expected_portfolio_vol"] / max(opt12["expected_portfolio_vol"], 1e-12)
    # 252/12 = 21 ⇒ vol √21 ≈ 4,58x — era exatamente a inflação exibida na UI
    assert ratio == pytest.approx(np.sqrt(252 / 12), rel=1e-6)


# ── Escala do alpha_selic ────────────────────────────────────────────────────

def test_margem_pct_grava_em_pontos_percentuais():
    # Contrato de escala: a Criação grava alpha_selic JÁ em p.p. — a
    # Avaliação (LLM) não pode multiplicar por 100 de novo.
    from views.portfolio_b3 import _margem_pct
    assert _margem_pct(112.5, 100.0) == pytest.approx(12.5)
    assert _margem_pct(100.0, 100.0) == pytest.approx(0.0)
    assert _margem_pct(float("nan"), 100.0) == 0.0
