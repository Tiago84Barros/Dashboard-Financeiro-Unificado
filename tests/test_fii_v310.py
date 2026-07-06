"""Score FII v3.1.0 — correções da auditoria 2026-07-05.

Cobre: DY ex-amortizações, P/VP efetivo (VPA CVM), liquidez com janela
curta e o contrato do snapshot mensal de score (migração 020).
"""
import datetime as dt

import pytest

from data_pipeline.market import fii
from data_pipeline.market import repository as repo

_REF = dt.date(2026, 7, 1)


def _div(days_ago: int, rate: float, label: str) -> dict:
    d = _REF - dt.timedelta(days=days_ago)
    return {"paymentDate": d.isoformat(), "rate": rate, "label": label}


# ── DY ex-amortizações ───────────────────────────────────────────────────────

def test_dy_12m_exclui_amortizacao_variantes():
    divs = [
        _div(30, 1.0, "RENDIMENTO"),
        _div(60, 5.0, "AMORTIZACAO"),       # sem acento
        _div(90, 3.0, "Amortização"),       # com acento
        _div(120, 0.5, "JCP"),
    ]
    # só rendimento (1.0) + JCP (0.5) contam; amortizações (8.0) ficam fora
    assert fii.dy_12m(divs, 100.0, _REF) == pytest.approx(0.015)


def test_dy_12m_sem_label_conta_como_rendimento():
    # payload incompleto (sem label) não pode zerar o DY do fundo
    divs = [{"paymentDate": (_REF - dt.timedelta(days=30)).isoformat(), "rate": 1.0}]
    assert fii.dy_12m(divs, 100.0, _REF) == pytest.approx(0.01)


def test_dy_12m_so_amortizacao_fica_none():
    divs = [_div(30, 5.0, "AMORTIZACAO")]
    assert fii.dy_12m(divs, 100.0, _REF) is None


# ── P/VP efetivo ─────────────────────────────────────────────────────────────

def test_pvp_efetivo_prefere_vpa_cvm():
    assert fii.pvp_efetivo(90.0, 100.0, 1.5) == pytest.approx(0.90)


def test_pvp_efetivo_fallback_brapi_sem_vpa():
    assert fii.pvp_efetivo(90.0, None, 1.5) == pytest.approx(1.5)
    assert fii.pvp_efetivo(90.0, 0.0, 1.5) == pytest.approx(1.5)


def test_pvp_efetivo_sem_nada_none():
    assert fii.pvp_efetivo(None, None, None) is None


# ── Liquidez com janela curta ────────────────────────────────────────────────

def test_liquidez_janela_curta_reflete_liquidez_recente():
    # 12 meses antigos com volume alto + 6 recentes secos: a mediana da
    # janela default (6 meses) deve refletir SÓ os recentes.
    antigos = [{"close": 10, "volume": 1_000_000}] * 12
    recentes = [{"close": 10, "volume": 1_000}] * 6
    liq = fii.liquidez_diaria(antigos + recentes)
    esperado = 10 * 1_000 / 21          # mediana dos 6 recentes ÷ pregões
    assert liq == pytest.approx(esperado, rel=1e-6)


# ── Snapshot mensal (contrato com o repositório) ─────────────────────────────

def test_snapshot_mensal_colunas_no_update():
    # o upsert do snapshot precisa poder ATUALIZAR score+inputs no conflito
    cols = repo._UPDATE_COLS["fii_metrics_monthly"]
    for c in ("score", "score_version", "price", "dy_12m", "pvp", "liquidez_diaria"):
        assert c in cols
    # e as colunas CVM continuam lá (o snapshot não pode reivindicar a linha)
    for c in ("vpa", "patrimonio_liquido", "num_cotistas"):
        assert c in cols


def test_score_version_bump():
    assert fii.SCORE_VERSION == "3.1.0"
