# -*- coding: utf-8 -*-
"""Mede o A-132 no armazem local e grava ``data/fii_integridade.json``.

A fita oficial da B3 (`market.fii_b3_security_history`, 243 MB) nao cabe no
Supabase e foi retirada de la de proposito. Sem ela o check de provento
implausivel nao tem denominador, e em producao a consulta so sabe levantar
`ProgrammingError`. Quem tem o armazem mede aqui; a tela le o arquivo.

O que se grava e a LISTA de fundos acusados, nao a contagem -- producao
intersecta com o proprio universo investivel, para que numerador e denominador
saiam da mesma base. Ver `core/fii_integridade.py`.

    python scripts/medir_integridade_fii.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402

from core.fii_integridade import (  # noqa: E402
    CAMINHO_MEDICAO,
    gravar_medicao,
    medicao_coerente,
    medir,
)
from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402


def main() -> int:
    engine = create_engine(
        _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))
    try:
        medicao = medir(engine)
    finally:
        engine.dispose()
    if not medicao_coerente(medicao):
        print("medicao reprovada no contrato; nada gravado")
        print(json.dumps(medicao, indent=2, ensure_ascii=False))
        return 1
    print(json.dumps(medicao, indent=2, ensure_ascii=False))
    print("gravado em", gravar_medicao(medicao, CAMINHO_MEDICAO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
