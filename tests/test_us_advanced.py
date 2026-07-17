"""Testes da Análise Avançada (Piotroski, Altman, Sloan, ROIC incremental)."""
import pytest

import core.us_advanced as adv


# ── Piotroski ─────────────────────────────────────────────────────────────────
def _cur_prev_perfeitos():
    prev = {"net_income": 80, "total_assets": 1000, "operating_cash_flow": 90,
            "current_assets": 400, "current_liabilities": 200, "long_term_debt": 300,
            "shares_outstanding": 100, "gross_profit": 300, "revenue": 900}
    cur = {"net_income": 120, "total_assets": 1000, "operating_cash_flow": 200,
           "current_assets": 500, "current_liabilities": 200, "long_term_debt": 250,
           "shares_outstanding": 100, "gross_profit": 400, "revenue": 1000}
    return cur, prev


def test_piotroski_nota_maxima():
    cur, prev = _cur_prev_perfeitos()
    r = adv.piotroski_f_score(cur, prev)
    assert r["score"] == 9 and r["evaluable"] == 9 and r["partial"] is False
    assert all(v is True for v in r["signals"].values())


def test_piotroski_criterios_falhos():
    cur, prev = _cur_prev_perfeitos()
    cur["net_income"] = -10          # ROA negativo e decrescente
    cur["shares_outstanding"] = 130  # emitiu ações
    cur["long_term_debt"] = 400      # alavancagem subiu
    r = adv.piotroski_f_score(cur, prev)
    assert r["signals"]["roa_positivo"] is False
    assert r["signals"]["roa_crescente"] is False
    assert r["signals"]["sem_emissao_acoes"] is False
    assert r["signals"]["alavancagem_caiu"] is False
    assert r["score"] < 9


def test_piotroski_dado_ausente_nao_conta_como_atendido():
    cur, prev = _cur_prev_perfeitos()
    del cur["gross_profit"]           # margem bruta não avaliável
    del prev["current_assets"]        # liquidez não avaliável
    r = adv.piotroski_f_score(cur, prev)
    assert r["signals"]["margem_bruta_subiu"] is None
    assert r["signals"]["liquidez_subiu"] is None
    assert r["evaluable"] == 7 and r["partial"] is True
    assert r["score"] == 7            # só os avaliáveis atendidos
    assert set(r["missing"]) == {"margem_bruta_subiu", "liquidez_subiu"}


# ── Altman ────────────────────────────────────────────────────────────────────
def test_altman_zona_segura():
    balance = {"total_assets": 1000, "current_assets": 500, "current_liabilities": 200,
               "retained_earnings": 400, "total_liabilities": 300}
    income = {"ebit": 200, "revenue": 1200}
    r = adv.altman_z_score(balance, income, market_cap=2000)
    # X1=.3 X2=.4 X3=.2 X4=6.667 X5=1.2 → 1.2*.3+1.4*.4+3.3*.2+0.6*6.667+1.0*1.2
    assert r["z"] == pytest.approx(1.2*0.3 + 1.4*0.4 + 3.3*0.2 + 0.6*(2000/300) + 1.2, rel=1e-3)
    assert r["zone"] == "segura"


def test_altman_zona_aflicao():
    balance = {"total_assets": 1000, "current_assets": 100, "current_liabilities": 400,
               "retained_earnings": -200, "total_liabilities": 900}
    income = {"ebit": -50, "revenue": 300}
    r = adv.altman_z_score(balance, income, market_cap=100)
    assert r["z"] < 1.81 and r["zone"] == "aflição"


def test_altman_sem_retained_earnings_ou_mcap():
    balance = {"total_assets": 1000, "current_assets": 500, "current_liabilities": 200,
               "total_liabilities": 300}          # sem retained_earnings
    income = {"ebit": 200, "revenue": 1200}
    r = adv.altman_z_score(balance, income, market_cap=2000)
    assert r["z"] is None and r["zone"] is None and "x2" in r["missing"]
    # sem market cap → X4 ausente
    balance["retained_earnings"] = 400
    r2 = adv.altman_z_score(balance, income, market_cap=None)
    assert r2["z"] is None and "x4" in r2["missing"]


# ── Sloan ─────────────────────────────────────────────────────────────────────
def test_sloan_accruals():
    # lucro 100, CFO 60 → accruals altos (ruim)
    a = adv.sloan_accruals(100, 60, 1000, 1000)
    assert a == pytest.approx(0.04)
    # lucro totalmente em caixa → accruals zero
    assert adv.sloan_accruals(100, 100, 1000) == pytest.approx(0.0)
    assert adv.sloan_accruals(None, 60, 1000) is None


# ── ROIC incremental ──────────────────────────────────────────────────────────
def test_incremental_roic():
    cur = {"ebit": 300, "invested_capital": 1500}
    prev = {"ebit": 200, "invested_capital": 1000}
    # ΔNOPAT = 100*0.79 = 79 ; ΔIC = 500 → 15.8%
    assert adv.incremental_roic(cur, prev) == pytest.approx(79 / 500)


def test_incremental_roic_sem_capital_novo():
    cur = {"ebit": 300, "invested_capital": 900}
    prev = {"ebit": 200, "invested_capital": 1000}
    assert adv.incremental_roic(cur, prev) is None   # ΔIC <= 0 → não interpretável
    assert adv.incremental_roic({"ebit": 1}, {"ebit": 1}) is None   # faltam dados


# ── Snapshot consolidado ──────────────────────────────────────────────────────
def test_advanced_snapshot():
    income = [{"fiscal_year": 2022, "net_income": 80, "revenue": 900, "ebit": 150,
               "gross_profit": 300},
              {"fiscal_year": 2023, "net_income": 120, "revenue": 1000, "ebit": 200,
               "gross_profit": 400}]
    balance = [{"fiscal_year": 2022, "total_assets": 1000, "current_assets": 400,
                "current_liabilities": 200, "long_term_debt": 300,
                "shares_outstanding": 100, "retained_earnings": 300,
                "total_liabilities": 350, "invested_capital": 1000},
               {"fiscal_year": 2023, "total_assets": 1000, "current_assets": 500,
                "current_liabilities": 200, "long_term_debt": 250,
                "shares_outstanding": 100, "retained_earnings": 400,
                "total_liabilities": 300, "invested_capital": 1500}]
    cashflow = [{"fiscal_year": 2022, "operating_cash_flow": 90},
                {"fiscal_year": 2023, "operating_cash_flow": 200}]
    s = adv.advanced_snapshot(income, balance, cashflow, market_cap=2000)
    assert s["f_score"] == 9 and s["f_partial"] is False
    assert s["z_score"] is not None and s["z_zone"] == "segura"
    assert s["sloan_accruals"] == pytest.approx((120 - 200) / 1000)
    assert s["incremental_roic"] == pytest.approx(50 * 0.79 / 500)


def test_advanced_snapshot_sem_dados():
    s = adv.advanced_snapshot([], [], [])
    assert s["f_score"] is None or s["f_evaluable"] == 0
    assert s["z_score"] is None
