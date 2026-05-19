"""
data_pipeline/
Camada de pipeline de dados do Dashboard Financeiro Unificado.

Responsabilidades:
  - Orquestrar atualizações de fontes externas (BCB, B3, CVM, etc.)
  - Registrar logs de execução em data_update_logs
  - Manter data_freshness_status atualizado
  - Expor status para a UI (views/configuracoes.py)

Uso interno:
    from data_pipeline.orchestrator import run_updates, get_update_status
"""
