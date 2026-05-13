"""
pages/proventos.py
Histórico de dividendos, JCP e rendimentos de FII.
Origem: Tiago84Barros/Dashboard-Investimentos
Status: stub visual (Fase 2) — implementação completa na Fase 5
"""
from design.componentes import container_pagina, em_construcao, estado_vazio


def render() -> None:
    container_pagina("Proventos", "Histórico de dividendos, JCP e rendimentos de FII", "💵")
    estado_vazio("Nenhum provento registrado ainda.", "💵")
    em_construcao("Fase 5", "Calendário de proventos, total no ano e histórico por ativo.")
