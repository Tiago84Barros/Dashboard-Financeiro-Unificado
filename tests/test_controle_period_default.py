from views.controle_financeiro import _indice_periodo_com_dados


def _opcoes():
    return [
        {"label": "Ago/2026", "ano": 2026, "mes": 8},
        {"label": "Jul/2026", "ano": 2026, "mes": 7},
        {"label": "Jun/2026", "ano": 2026, "mes": 6},
    ]


def test_periodo_padrao_usa_mes_mais_recente_com_dados():
    historico = [
        {"ano": 2026, "mes": 6, "receitas": 8_000},
        {"ano": 2026, "mes": 7, "receitas": 9_000},
    ]

    assert _indice_periodo_com_dados(_opcoes(), historico) == 1


def test_periodo_padrao_nao_exige_saldo_diferente_de_zero():
    historico = [{"ano": 2026, "mes": 7, "receitas": 0, "despesas": 0}]

    assert _indice_periodo_com_dados(_opcoes(), historico) == 1


def test_periodo_padrao_cai_no_mes_atual_sem_historico_valido():
    historico = [
        {"ano": "inválido", "mes": None},
        {"ano": 2025, "mes": 1},
    ]

    assert _indice_periodo_com_dados(_opcoes(), historico) == 0
