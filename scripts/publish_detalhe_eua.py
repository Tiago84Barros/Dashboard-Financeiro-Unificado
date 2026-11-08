"""Publica trimestres, exercícios e proventos dos EUA em ``data/public/detalhe_eua.json.gz``.

O chat da empresa americana recebia só a CONTAGEM de demonstrativos
("Demonstrações: 9 períodos no banco") e respondia sobre o último trimestre
sem o número dele. Os trimestrais moram no armazém local, que o app publicado
não alcança; o túnel até ele só existe com o PC ligado.

Arquivo, e não tabela no Supabase: decisão do usuário em 08/11/2026, com o
Supabase perto dos 500 MB. O conteúdo são as MESMAS linhas cruas de
``core.us_detalhe_armazem.ler_detalhe`` (8 trimestres, 3 exercícios, 5 anos de
proventos), sem o preço diário -- a conta continua em
``resumo_para_prompt``, no app, como para o túnel.

Lê o armazém só para leitura; grava por omissão (``--dry-run`` só mede). Uso:
    python scripts/publish_detalhe_eua.py
    python scripts/publish_detalhe_eua.py --dry-run --simbolo AAPL --simbolo MSFT
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import us_detalhe_armazem as det  # noqa: E402

CAMINHO_PADRAO = det.ARQUIVO
VERSAO = 1
LOTE = 400

_SQL_UNIVERSO = """
    SELECT symbol FROM market_us.company_snapshots
     WHERE is_active IS TRUE AND symbol IS NOT NULL
     ORDER BY symbol
"""


def montar(engine, simbolos: list[str], *, hoje: date | None = None,
           agora: datetime | None = None, lote: int = LOTE) -> dict:
    """Payload do arquivo: ``ler_detalhe`` em lotes, sem preço e sem teto."""
    hoje = hoje or date.today()
    agora = agora or datetime.now(timezone.utc)
    por_ticker: dict[str, dict] = {}
    for inicio in range(0, len(simbolos), lote):
        bruto = det.ler_detalhe(engine, simbolos[inicio:inicio + lote], hoje=hoje,
                                limite=None, com_precos=False)
        por_ticker.update(det.para_arquivo(bruto))
    datas = [t.get("available_at") for d in por_ticker.values()
             for t in d.get("trimestres", []) if t.get("available_at")]
    return {"versao": VERSAO, "gerado_em": agora.isoformat(timespec="seconds"),
            "base_ate": max(datas) if datas else None,
            "fonte": "armazém local (market_us.income_statements, "
                     "cash_flow_statements, dividends)",
            "por_ticker": por_ticker}


def serializar(payload: dict, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    bruto = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
    with open(caminho, "wb") as fh, gzip.GzipFile(
            filename="", mode="wb", fileobj=fh, mtime=0) as gz:
        gz.write(bruto)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, default=CAMINHO_PADRAO)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--simbolo", action="append", dest="simbolos")
    args = p.parse_args(argv)

    from sqlalchemy import create_engine, text

    from scripts.publish_fii_selection_from_local import _warehouse_url
    try:
        engine = create_engine(_warehouse_url())
        with engine.connect() as conn:
            universo = [str(r[0]).upper() for r in conn.execute(text(_SQL_UNIVERSO))]
    except Exception as exc:  # noqa: BLE001
        print(f"armazém local indisponível: {type(exc).__name__}: {exc}")
        return 2
    simbolos = det.normalizar_simbolos(args.simbolos) if args.simbolos else universo
    payload = montar(engine, simbolos)
    por = payload["por_ticker"]
    resumo = {"simbolos": len(simbolos), "com_dado": len(por),
              "com_trimestre": sum(1 for d in por.values() if d.get("trimestres")),
              "base_ate": payload["base_ate"]}
    if not por or payload["base_ate"] is None:
        resumo["erro"] = "nenhum trimestre validado no armazém"
        print(json.dumps(resumo, ensure_ascii=False, indent=2))
        return 1
    if not args.dry_run:
        serializar(payload, args.saida)
        resumo["arquivo"] = str(args.saida)
        resumo["bytes"] = args.saida.stat().st_size
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
