"""
pages/macro.py
Cenário macroeconômico: SELIC, IPCA, câmbio e índices de mercado.
Dados via API BCB/SGS e yfinance.
Status: stub visual (Fase 2) — implementação completa na Fase 9
"""
from design.componentes import container_pagina, em_construcao, estado_vazio


def render() -> None:
    container_pagina(
        "Cenário Macroeconômico",
        "SELIC, IPCA, câmbio e índices de mercado",
        "🌐",
    )
    estado_vazio("Dados macroeconômicos indisponíveis no momento.", "🌐")
    em_construcao(
        "Fase 9",
        "SELIC, IPCA, câmbio (USD/BRL), curva de juros e índices IBOVESPA, S&P 500 via API BCB/yfinance.",
    )
