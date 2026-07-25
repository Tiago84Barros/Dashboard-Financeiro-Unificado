from datetime import date

from core.proventos import _montar_dict


def _evento(ticker: str, total: float, pagamento: date) -> dict:
    return {
        "ticker": ticker,
        "nome": ticker,
        "classe": "FII",
        "cor": "#000000",
        "tipo": "reit_income",
        "label_tipo": "Rendimento FII",
        "total_amount": total,
        "payment_date": pagamento,
    }


def test_por_ativo_12m_nao_mistura_provento_historico():
    hoje = date(2026, 7, 25)
    dados = _montar_dict(
        [
            _evento("FII11", 100.0, date(2025, 7, 24)),
            _evento("FII11", 20.0, date(2026, 7, 1)),
        ],
        hoje,
    )

    assert dados["por_ativo"][0]["total"] == 120.0
    assert dados["por_ativo_12m"][0]["total"] == 20.0
    assert dados["total_12m"] == 20.0


def test_por_ativo_12m_inclui_exatamente_a_data_limite():
    hoje = date(2026, 7, 25)
    dados = _montar_dict([_evento("FII11", 15.0, date(2025, 7, 25))], hoje)
    assert dados["por_ativo_12m"][0]["total"] == 15.0


def test_por_ativo_12m_vazio_e_expresso_como_lista_vazia():
    hoje = date(2026, 7, 25)
    dados = _montar_dict([_evento("FII11", 15.0, date(2024, 1, 1))], hoje)
    assert dados["por_ativo_12m"] == []
    assert dados["total_12m"] == 0.0
