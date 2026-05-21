"""
data_pipeline/importers/investments/nomad_pdf.py
================================================
Parser de notas de corretagem da Nomad (.pdf).

Status atual: STUB.

A integração definitiva exige espelhar a lógica do app individual
Dashboard-Investimentos (`nomad_import_service_v2.py`) — pdfplumber +
normalização de tickers internacionais — e mapear para
`investment_transactions`/`dividends` no app4.

O contrato de retorno e o prefixo de `external_id` já estão definidos.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine

from .common import finalize_summary, make_summary

SOURCE = "nomad_pdf"


def parse(file_bytes: bytes, engine: Engine) -> dict[str, Any]:  # noqa: ARG001
    summary = make_summary(SOURCE)
    summary["status"] = "skipped"
    summary["errors"].append(
        "Importacao Nomad PDF ainda nao implementada nesta rodada. "
        "Estrutura ja preparada para receber o parser baseado em pdfplumber."
    )
    return finalize_summary(summary)
