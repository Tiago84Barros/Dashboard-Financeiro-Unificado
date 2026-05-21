"""
data_pipeline/importers/investments/xp_consolidado.py
=====================================================
Parser do "Relatório Consolidado" exportado pela XP (.xlsx).

Status atual: STUB.

A integração definitiva exige espelhar a lógica do app individual
Dashboard-Investimentos (`xp_import_service.py`) e popular a tabela
`portfolio_position_snapshots` no schema do app4. Esse caminho está
em planejamento para uma rodada posterior — esta rodada cobre B3.

O contrato de retorno e o `external_id` já estão prontos. Quando a
implementação for concluída, basta substituir o corpo de `parse()`
seguindo o mesmo padrão de b3_negociacao.py / b3_movimentacao.py.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine

from .common import finalize_summary, make_summary

SOURCE = "xp_consolidado"


def parse(file_bytes: bytes, engine: Engine) -> dict[str, Any]:  # noqa: ARG001
    summary = make_summary(SOURCE)
    summary["status"] = "skipped"
    summary["errors"].append(
        "Importacao XP Consolidado ainda nao implementada nesta rodada. "
        "Use os relatorios B3 enquanto a integracao XP nao e finalizada."
    )
    return finalize_summary(summary)
