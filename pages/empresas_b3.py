"""
pages/empresas_b3.py
Análise fundamentalista de empresas listadas na B3 (tickers .SA via yfinance).
Status: stub visual (Fase 2) — implementação completa na Fase 9
"""
from design.componentes import container_pagina, em_construcao, estado_vazio


def render() -> None:
    container_pagina("Empresas B3", "Indicadores fundamentalistas de empresas brasileiras", "🏢")
    estado_vazio("Nenhuma empresa selecionada.", "🏢")
    em_construcao(
        "Fase 9",
        "Busca por ticker B3, P/L, P/VP, Dividend Yield, ROE e comparativos setoriais via yfinance.",
    )
