"""
pages/empresas_eua.py
Análise fundamentalista de empresas listadas em NYSE e NASDAQ (via yfinance).
Status: stub visual (Fase 2) — implementação completa na Fase 9
"""
from design.componentes import container_pagina, em_construcao, estado_vazio


def render() -> None:
    container_pagina("Empresas EUA", "Indicadores fundamentalistas — NYSE e NASDAQ", "🌎")
    estado_vazio("Nenhuma empresa selecionada.", "🌎")
    em_construcao(
        "Fase 9",
        "Busca por ticker US, P/E, EPS, market cap, margens e comparativos setoriais via yfinance.",
    )
