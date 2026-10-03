from core.investimentos import ganho_total


def test_provento_entra_uma_vez_so():
    # Números da tela de 03/10/2026: o card antigo somava mercado + proventos
    # (R$ 411.312,77), contando de novo o provento que virou cota.
    g = ganho_total({"total_mercado": 395569.42, "total_investido": 387841.29,
                     "total_dividendos": 15743.35})
    assert round(g["valorizacao"], 2) == 7728.13
    assert round(g["ganho"], 2) == 23471.48
    assert round(g["ganho_pct"], 4) == round(23471.48 / 387841.29, 4)


def test_sem_custo_nao_inventa_ganho():
    assert ganho_total({"total_mercado": 100.0, "total_investido": 0.0}) is None
    assert ganho_total({}) is None
