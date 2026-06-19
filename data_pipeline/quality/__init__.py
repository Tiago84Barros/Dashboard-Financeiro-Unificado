"""
data_pipeline/quality/
Camada de Data Quality do pipeline — auditoria, comparação, saneamento,
score de confiabilidade, scheduler incremental e relatórios.

Responsabilidade única por módulo:
  validator.py  — audita qualidade (reusa core.data_quality)
  comparer.py   — compara fontes (reusa core.data_healing)
  sanitizer.py  — política de saneamento (corrigir/ignorar/revisar…)
  updater.py    — grava correções + histórico (reusa core.data_healing)
  scheduler.py  — seleção incremental (cursor, prioridade, anti-bloqueio)
  score.py      — score de confiabilidade por (ticker, campo)
  report.py     — relatório de execução (banco + JSON/CSV)

Tudo reaproveita core/data_quality.py e core/data_healing.py — sem duplicar lógica.
"""
from __future__ import annotations
