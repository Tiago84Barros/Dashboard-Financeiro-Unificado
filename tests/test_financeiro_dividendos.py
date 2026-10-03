"""Proventos do mês/ano da visão geral saem de core.proventos (deduplicado,
por data de pagamento, só renda), não de uma soma crua em ``dividends``."""
import inspect
from datetime import date

from core import financeiro
from core.financeiro import dividendos_do_periodo
from core.proventos import _montar_dict


def _ev(ticker, valor, pagamento, ext, tipo="dividend", ex=None):
    return {"id": ext, "ticker": ticker, "nome": ticker, "classe": "Ações",
            "cor": "#000", "tipo": tipo, "label_tipo": tipo,
            "amount_per_unit": valor, "quantity": 1.0, "total_amount": valor,
            "ex_date": ex, "payment_date": pagamento, "external_id": ext}


def test_mes_e_ano_contam_pagamento_duplicado_uma_vez():
    hoje = date(2026, 10, 2)
    eventos = [
        # mesmo pagamento: B3 no lote padrão e XP no fracionário
        _ev("BBAS3", 10.0, date(2026, 10, 1), "b3mov-1", ex=date(2025, 12, 20)),
        _ev("BBAS3F", 10.0, date(2026, 10, 1), "xpcsl-1"),
        _ev("TAEE11", 5.0, date(2026, 3, 15), "b3mov-2"),
        _ev("MXRF11", 7.0, date(2026, 4, 10), "b3mov-3", tipo="amortization"),
        _ev("TAEE11", 4.0, date(2025, 11, 15), "b3mov-4"),  # ano passado
    ]
    prov = {**_montar_dict(eventos, hoje), "data_source": "real"}
    assert dividendos_do_periodo(prov) == (10.0, 15.0)


def test_sem_proventos_reais_fica_indisponivel():
    assert dividendos_do_periodo(None) == (None, None)
    assert dividendos_do_periodo({"data_source": "mock", "total_mes": 9}) == (None, None)


def test_visao_geral_nao_soma_dividends_direto():
    fonte = inspect.getsource(financeiro._visao_geral_real)
    assert "FROM dividends" not in fonte
    assert "dividendos_do_periodo(proventos_migrados)" in fonte
