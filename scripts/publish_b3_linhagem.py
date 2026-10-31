"""Publica a procedência das demonstrações B3 em ``data/public/b3_linhagem.json``.

Lê o armazém local (Docker ``dfu_warehouse``), só leitura. Para cada tabela de
demonstração (DRE, balanço, DFC), mede com
``core.b3_validation.medir_linhagem``, a mesma junção que a validação faz
contra ``market.brapi_raw_payloads``: rastreada, ponteiro órfão e payload
posterior à linha.

Por que existe (INF-A2): ``market.brapi_raw_payloads`` é trilha de auditoria do
ETL e a próxima tabela a sair do Supabase free. Sem ela no banco conectado,
``core.b3_validation.lineage_counts`` lê este arquivo e diz que a medida veio do
armazém, e de quando. Sem o arquivo, a validação mostra procedência
"indisponível", nunca "toda linha sem origem".

Saída 0 grava (ou só mede com ``--dry-run``). Saída 1 recusa: armazém sem os
payloads ou sem nenhuma demonstração, situação em que publicar contagem zero
pareceria medida. Saída 2: armazém inalcançável.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TABELAS = (
    "market.income_statements",
    "market.balance_sheets",
    "market.cash_flow_statements",
)


def medir(conn, *, agora: datetime | None = None) -> dict:
    """Monta o artefato a partir de uma conexão com os payloads (só leitura)."""
    from sqlalchemy import text

    from core import b3_validation as bv

    if not bv._table_exists(conn, "market.brapi_raw_payloads"):
        raise LookupError("market.brapi_raw_payloads ausente no armazém")
    tabelas = {t: bv.medir_linhagem(conn, t)
               for t in TABELAS if bv._table_exists(conn, t)}
    if not tabelas:
        raise LookupError("nenhuma tabela de demonstração no armazém")
    payloads = conn.execute(text("""
        SELECT count(*) AS n, min(fetched_at) AS de, max(fetched_at) AS ate
        FROM market.brapi_raw_payloads
    """)).mappings().one()
    return {
        "schema": bv.LINHAGEM_SCHEMA,
        "gerado_em": (agora or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "base": "armazém local (dfu_warehouse)",
        "metodo": ("demonstrações anuais (period='annual') juntadas a "
                   "market.brapi_raw_payloads pelo raw_payload_id; rastreada = "
                   "payload existe e é anterior a first_seen_at"),
        "payloads": {
            "linhas": int(payloads["n"] or 0),
            "fetched_de": payloads["de"].isoformat() if payloads["de"] else None,
            "fetched_ate": payloads["ate"].isoformat() if payloads["ate"] else None,
        },
        "tabelas": tabelas,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from sqlalchemy import create_engine

    from core import b3_validation as bv
    from scripts.publish_fii_selection_from_local import _warehouse_url

    try:
        engine = create_engine(_warehouse_url())
        conn = engine.connect()
    except Exception as exc:  # noqa: BLE001 - relata e sai com código próprio
        print(f"armazém local inalcançável: {exc}")
        return 2
    try:
        artefato = medir(conn)
    except LookupError as exc:
        print(f"recusado: {exc}")
        return 1
    finally:
        conn.close()
        engine.dispose()

    saida = Path(args.saida or bv.ARTEFATO_LINHAGEM)
    print(json.dumps({"saida": str(saida), "dry_run": args.dry_run,
                      "payloads": artefato["payloads"],
                      "tabelas": artefato["tabelas"]},
                     ensure_ascii=False, indent=2))
    if not args.dry_run:
        saida.parent.mkdir(parents=True, exist_ok=True)
        saida.write_text(json.dumps(artefato, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
