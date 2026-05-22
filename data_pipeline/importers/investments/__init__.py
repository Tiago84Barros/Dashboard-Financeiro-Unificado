"""
data_pipeline/importers/investments/
====================================
Importadores manuais de dados de investimento.

Fontes suportadas:
  - b3_negociacao   B3 Área do Investidor → Negociação (.xlsx)
  - b3_movimentacao B3 Área do Investidor → Movimentação (.xlsx)
  - xp_consolidado  XP Investimentos → Posição Consolidada (.xlsx) — stub
  - nomad_pdf       Nomad → notas de corretagem (.pdf) — stub

Cada parser expõe `parse(file_bytes: bytes, engine) -> dict`. O dict de retorno
segue o contrato em `.claude/skills/investment-imports/SKILL.md`.

Imports lazy:
  Os parsers usam dependências opcionais (openpyxl, pdfplumber). Mantemos o
  `__init__.py` puro para permitir importar apenas `common` em testes ou em
  ambientes sem essas dependências instaladas.
"""
from __future__ import annotations

from typing import Any, Callable

__all__ = [
    "parse_b3_negociacao",
    "parse_b3_movimentacao",
    "parse_xp_consolidado",
    "parse_nomad_pdf",
]


def parse_b3_negociacao(file_bytes: bytes, engine) -> dict[str, Any]:
    from .b3_negociacao import parse
    return parse(file_bytes, engine)


def parse_b3_movimentacao(file_bytes: bytes, engine) -> dict[str, Any]:
    from .b3_movimentacao import parse
    return parse(file_bytes, engine)


def parse_xp_consolidado(
    payload: "bytes | tuple[str, bytes]", engine,
) -> dict[str, Any]:
    """
    XP aceita bytes diretos OU (filename, bytes). O filename é usado para
    inferir a `report_date` do snapshot ("mensal-2026-janeiro" → 31/01/2026).
    """
    from .xp_consolidado import parse
    return parse(payload, engine)


def parse_nomad_pdf(
    files: "list[tuple[str, bytes]] | bytes", engine,
) -> dict[str, Any]:
    """
    Nomad aceita tanto um único PDF (bytes) quanto vários ao mesmo tempo
    (list[(filename, bytes)]). A normalização fica dentro do módulo
    nomad_pdf.parse().
    """
    from .nomad_pdf import parse
    return parse(files, engine)
