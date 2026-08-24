"""PROV-Q: o provento exibido responde a pergunta "quanto isso rende?".

A-128 devolucao de capital contada como renda (inflava).
A-129 eco de classe somado nas agregacoes anuais (inflava).
A-130 `min` sobre a data inteira descartava dividendo+JCP legitimos (deflacionava).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import pytest

from core.dividend_types import (
    TIPOS_DEVOLUCAO_CAPITAL,
    TIPOS_RENDA,
    eh_renda,
    sql_apenas_renda,
)


def test_amortizacao_e_restituicao_nao_sao_renda():
    assert eh_renda("AMORTIZAÇÃO") is False
    assert eh_renda("REST CAP DIN") is False
    assert eh_renda("rest cap din") is False


def test_tipos_de_renda_conhecidos_passam():
    for t in TIPOS_RENDA:
        assert eh_renda(t) is True


def test_tipo_desconhecido_conta_como_renda_para_ficar_visivel():
    """Sumir em silencio e o erro caro; aparecer errado e corrigivel."""
    assert eh_renda(None) is True
    assert eh_renda("TIPO QUE A B3 INVENTAR AMANHA") is True


def test_predicado_sql_cita_todos_os_tipos_de_capital():
    sql = sql_apenas_renda()
    for t in TIPOS_DEVOLUCAO_CAPITAL:
        assert t in sql
    assert "type IS NULL OR" in sql
    assert sql_apenas_renda("d.tipo").startswith("(d.tipo IS NULL")


# --- A-130: o agrupamento de core.dossie_b3._dividendos ------------------


def _agregar(eventos):
    """Replica a regra de (data, tipo) sem tocar o banco."""
    from core.dividend_types import eh_renda as _renda

    renda, capital = defaultdict(list), defaultdict(list)
    for dt, tipo, valor in eventos:
        (renda[(dt, tipo)] if _renda(tipo) else capital[dt]).append(valor)
    return (
        sum(min(v) for v in renda.values()),
        sum(min(v) for v in capital.values()),
    )


def test_dividendo_e_jcp_na_mesma_data_somam_em_vez_de_um_sumir():
    r, c = _agregar([("2026-05-10", "DIVIDENDO", 1.0), ("2026-05-10", "JCP", 0.4)])
    assert r == pytest.approx(1.4)
    assert c == 0.0


def test_eco_de_classe_no_mesmo_tipo_nao_dobra_o_provento():
    r, _ = _agregar([("2026-05-10", "DIVIDENDO", 1.0), ("2026-05-10", "DIVIDENDO", 1.0)])
    assert r == pytest.approx(1.0)


def test_amortizacao_sai_do_yield_e_e_reportada_a_parte():
    r, c = _agregar([("2026-05-10", "RENDIMENTO", 0.8), ("2026-05-10", "AMORTIZAÇÃO", 90.0)])
    assert r == pytest.approx(0.8)
    assert c == pytest.approx(90.0)


def test_fii_so_de_amortizacao_rende_zero_e_nao_o_valor_devolvido():
    """RBRI11/2026 exibia 252,20 de 'provento' com renda real zero."""
    r, c = _agregar([("2026-03-01", "AMORTIZAÇÃO", 252.2)])
    assert r == 0.0
    assert c == pytest.approx(252.2)


# --- integracao leve com a funcao real ----------------------------------


def test_dossie_declara_devolucao_de_capital_como_chave_propria(monkeypatch):
    import core.dossie_b3 as dossie

    hoje = date.today()
    dt = (hoje - timedelta(days=30)).isoformat()
    fake = [
        {"dt": dt, "amount": 1.0, "type": "DIVIDENDO"},
        {"dt": dt, "amount": 1.0, "type": "DIVIDENDO"},  # eco de classe
        {"dt": dt, "amount": 0.5, "type": "JCP"},        # evento legitimo
        {"dt": dt, "amount": 40.0, "type": "AMORTIZAÇÃO"},
    ]
    monkeypatch.setattr(dossie, "_rows", lambda *a, **k: fake)
    out = dossie._dividendos("XPTO11", preco=100.0)
    assert out["ult_12m_ps"] == pytest.approx(1.5)
    assert out["devolucao_capital_12m_ps"] == pytest.approx(40.0)
    assert out["dy_12m_pct"] == pytest.approx(1.5)


def test_dossie_sem_preco_nao_inventa_yield(monkeypatch):
    import core.dossie_b3 as dossie

    monkeypatch.setattr(dossie, "_rows", lambda *a, **k: [
        {"dt": date.today().isoformat(), "amount": 1.0, "type": "DIVIDENDO"}])
    out = dossie._dividendos("XPTO3", preco=None)
    assert out["dy_12m_pct"] is None
    assert out["ult_12m_ps"] == pytest.approx(1.0)
