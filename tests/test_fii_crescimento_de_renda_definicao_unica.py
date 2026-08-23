"""Achado A-107: ingestão e walk-forward PIT calculavam
``income_growth_per_share_3y`` por fórmulas diferentes, então o certificado PIT
era emitido sobre uma métrica que a produção não computa — ela pesa 0,100 e é
crítica.

Medido em 23/08/2026 sobre 296 FIIs com histórico comparável: correlação de
postos de apenas 0,765, mediana de -24,5% no validador contra -6,1% na
ingestão, 16% dos fundos mudando mais de 20 pontos percentuais de posição e
casos de inversão total de sinal (TRUE11: -99,8% x +100,0%).
"""
from __future__ import annotations

from datetime import date

from core.fii_methodology import income_growth_3y


def _renda(por_mes: float, meses: int, fim: date) -> dict[date, float]:
    serie: dict[date, float] = {}
    for offset in range(meses):
        ano, mes = fim.year, fim.month - offset
        while mes <= 0:
            ano -= 1
            mes += 12
        serie[date(ano, mes, 1)] = por_mes
    return serie


def test_renda_constante_da_crescimento_zero_em_qualquer_mes_de_corte():
    """O defeito do validador: agrupar por ano-calendário fazia o ano corrente
    entrar parcial. Com 100/mês constante, um corte em março media -50,0% e um
    em agosto, -18,4%; só dezembro acertava. A janela de meses corridos é imune
    à fronteira do calendário."""
    for mes in range(1, 13):
        fim = date(2026, mes, 1)
        assert income_growth_3y(_renda(100.0, 36, fim), fim) == 0.0, (
            f"corte em {mes:02d}/2026 não devolveu crescimento zero")


def test_crescimento_real_e_medido_anualizado():
    """Renda que dobra em dois anos é ~41,4% ao ano ((2)**.5 - 1)."""
    fim = date(2026, 8, 1)
    serie = _renda(100.0, 36, fim)
    for chave in list(serie):
        if chave >= date(2025, 9, 1):        # 12 meses mais recentes
            serie[chave] = 200.0
    resultado = income_growth_3y(serie, fim)
    assert resultado is not None
    assert abs(resultado - (2.0 ** .5 - 1.0)) < 1e-9


def test_limite_em_menos_um_e_um():
    """A ingestão limitava a [-1, 1] e o validador não. O limite é da
    definição, não de um dos lados."""
    fim = date(2026, 8, 1)
    serie = _renda(1.0, 36, fim)
    for chave in list(serie):
        if chave >= date(2025, 9, 1):
            serie[chave] = 10_000.0
    assert income_growth_3y(serie, fim) == 1.0


def test_historico_ralo_nao_produz_crescimento():
    """Menos de 24 meses povoados não sustenta a medida — devolve ausência, que
    reduz cobertura, em vez de um número inventado."""
    fim = date(2026, 8, 1)
    serie = _renda(100.0, 36, fim)
    for indice, chave in enumerate(sorted(serie)):
        if indice < 20:
            serie[chave] = 0.0
    assert income_growth_3y(serie, fim) is None
    assert income_growth_3y({}, fim) is None


def test_ingestao_e_validador_usam_a_mesma_funcao():
    """A divergência voltaria em silêncio se cada lado reimplementasse. Ambos
    importam a definição única."""
    import data_pipeline.market.fii_pit as pit
    import data_pipeline.market.fii_v2 as v2

    assert v2.income_growth_3y is income_growth_3y
    assert pit.income_growth_3y is income_growth_3y
