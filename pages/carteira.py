"""
pages/carteira.py
Visualização consolidada da carteira de ativos: posições, alocação e custo médio.
Origem: Tiago84Barros/Dashboard-Investimentos
Status: stub visual (Fase 2) — implementação completa na Fase 5
"""
from design.componentes import container_pagina, em_construcao, estado_vazio


def render() -> None:
    container_pagina("Carteira", "Posições, alocação e custo médio dos seus ativos", "💼")
    estado_vazio("Nenhuma posição cadastrada ainda.", "💼")
    em_construcao("Fase 5", "Cotações via yfinance, custo médio ponderado e pie chart de alocação.")
