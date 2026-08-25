# -*- coding: utf-8 -*-
"""Aplica UM arquivo de supabase_unificado/schema/ no banco de producao.

Existe porque a convencao do projeto -- colar SQL no editor do Supabase --
nao deixa rastro no repositorio de que a migration foi aplicada, e porque a
divergencia entre o schema declarado e o remoto ja custou caro: a 021 declara
os defaults de market.calculated_metric_vintages, a tabela remota nunca os
recebeu, e a ingestao diaria de precos da B3 abortava em 100% dos tickers.

Limites deliberados:

* so aceita caminho dentro de supabase_unificado/schema/ -- nao roda SQL
  arbitrario vindo da linha de comando;
* recusa DROP e TRUNCATE. Migration destrutiva continua sendo decisao humana
  no editor do Supabase, com o usuario olhando;
* --dry-run (padrao) imprime o SQL e sai sem tocar no banco. Gravar exige
  --apply explicito.

Uso:
    python scripts/apply_supabase_migration.py 050_metric_vintages_defaults.sql
    python scripts/apply_supabase_migration.py 050_...sql --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SCHEMA_DIR = RAIZ / "supabase_unificado" / "schema"
PROIBIDO = re.compile(r"\b(DROP|TRUNCATE)\s+(TABLE|SCHEMA|DATABASE|COLUMN)\b",
                      re.IGNORECASE)


def _resolver(nome: str) -> Path:
    caminho = (SCHEMA_DIR / nome).resolve()
    if not str(caminho).startswith(str(SCHEMA_DIR.resolve())):
        raise SystemExit(f"fora de supabase_unificado/schema/: {nome}")
    if not caminho.is_file():
        raise SystemExit(f"nao encontrado: {caminho}")
    return caminho


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("arquivo", help="nome do .sql em supabase_unificado/schema/")
    p.add_argument("--apply", action="store_true",
                   help="grava de fato; sem isso, apenas mostra o SQL")
    args = p.parse_args(argv)

    caminho = _resolver(args.arquivo)
    sql = caminho.read_text(encoding="utf-8")

    achado = PROIBIDO.search(sql)
    if achado:
        raise SystemExit(
            f"recusado: contem {achado.group(0)!r}. Migration destrutiva vai "
            "para o editor do Supabase, com decisao humana na frente.")

    print(f"-- {caminho.relative_to(RAIZ)}")
    print(sql)
    if not args.apply:
        print("[dry-run] nada gravado. Use --apply para executar.")
        return 0

    sys.path.insert(0, str(RAIZ))
    from sqlalchemy import text

    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise SystemExit("DATABASE_URL nao configurada.")
    with engine.begin() as conn:
        conn.execute(text(sql))
    print(f"[aplicado] {caminho.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
