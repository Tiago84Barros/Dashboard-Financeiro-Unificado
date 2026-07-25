import pytest

from core.investimentos import _agregar_por_classe, _agregar_por_setor


def _pos(nome, classe, setor, valor):
    return {
        "ticker": nome,
        "classe": classe,
        "setor": setor,
        "valor_mercado": valor,
        "total_investido": valor,
        "cor": "#000000",
    }


def test_percentuais_por_classe_preservam_precisao_e_somam_cem():
    posicoes = [
        _pos("A", "Ações", "S1", 1.0),
        _pos("B", "FII", "S2", 1.0),
        _pos("C", "ETF", "S3", 1.0),
    ]
    classes = _agregar_por_classe(posicoes)
    assert sum(c["pct_carteira"] for c in classes) == pytest.approx(100.0)
    assert classes[0]["pct_carteira"] == pytest.approx(100 / 3)


def test_percentuais_por_setor_preservam_precisao_e_somam_cem():
    posicoes = [
        _pos("A", "Ações", "S1", 1.0),
        _pos("B", "Ações", "S2", 1.0),
        _pos("C", "Ações", "S3", 1.0),
    ]
    setores = _agregar_por_setor(posicoes)
    assert sum(s["pct_carteira"] for s in setores) == pytest.approx(100.0)
