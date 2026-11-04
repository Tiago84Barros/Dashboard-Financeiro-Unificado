"""Razões que passam de 100% chegam à IA na escala certa.

O contexto do chat de FIIs formatava toda métrica percentual com a heurística
``abs(x) > 1 -> /100``, pensada para a vitrine que grava percentual ora em
fração (0.18), ora em ponto (18.0). Para alavancagem, LTV e crescimento da
renda por cota, que chegam sempre em fração e podem passar de 1, ela mandava
"leverage=1,20%" para um fundo com passivo de 120% do ativo.
"""
import pytest

from core.llm_context_fii import _fmt, _fund_block


def _fundo(**metricas):
    return {"ticker": "TEST11", "tipo": "papel", "sector": "recebiveis", **metricas}


@pytest.mark.parametrize(("metrica", "valor", "esperado"), [
    ("leverage", 1.2, "leverage=120.00%"),
    ("ltv", 1.1, "ltv=110.00%"),
    ("income_growth_per_share_3y", 1.5, "income_growth_per_share_3y=150.00%"),
    ("leverage", .35, "leverage=35.00%"),
])
def test_razao_acima_de_um_nao_e_lida_como_ponto(metrica, valor, esperado):
    assert esperado in _fund_block(_fundo(**{metrica: valor}), selected=True)


def test_percentual_limitado_a_cem_continua_aceitando_ponto():
    # Vacância gravada em ponto (12.0) segue lida como 12%.
    assert "vacancia_fisica=12.00%" in _fund_block(_fundo(vacancia_fisica=12.0), selected=True)
    assert "vacancia_fisica=12.00%" in _fund_block(_fundo(vacancia_fisica=.12), selected=True)


def test_fmt_fracao_desliga_a_adivinhacao():
    assert _fmt(1.2, percent=True) == "1.20%"
    assert _fmt(1.2, percent=True, fracao=True) == "120.00%"


def test_relatorio_da_selecao_tambem_le_razao_em_fracao():
    from core.fii_selection_explanations import _metric_line, build_selection_reports

    assert _metric_line("alavancagem", 1.2, fracao=True) == "alavancagem: 120.0%"
    assert _metric_line("vacância física", 12.0) == "vacância física: 12.0%"

    fundo = {"ticker": "PAPR11", "tipo": "papel", "ltv": 1.1, "dy_12m": .11,
             "income_recurrence": .9, "income_growth_per_share_3y": 1.5,
             "pvp": .95, "liquidez_diaria": 2_000_000}
    relatorio = build_selection_reports([fundo], [fundo])[0]
    texto = " ".join(relatorio["facts"] + relatorio["structure"] + relatorio["operating"])

    assert "LTV: 110.0%" in texto
    assert "crescimento da renda por cota +150.0%" in texto
