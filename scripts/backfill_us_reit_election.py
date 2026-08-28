# -*- coding: utf-8 -*-
"""Apura `companies.reit_election` no relatorio anual da SEC (A-156).

Escopo: as companhias cujo cadastro traz o rotulo generico "Real Estate". Nao e
restricao por suspeita -- e o dominio EXATO em que a regra consulta o campo. Em
qualquer outra empresa o veredito nao mudaria decisao nenhuma, e cada apuracao
custa o download de um 10-K inteiro (1 a 16 MB). Onde ha REIT declarado no
cadastro (`is_reit`) a exclusao ja acontece antes, sem precisar do documento.

    python scripts/backfill_us_reit_election.py [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from data_pipeline.us.edgar import SUBMISSIONS_URL, build_edgar_provider  # noqa: E402
from data_pipeline.us.reit_eleicao import apurar_eleicao  # noqa: E402


def _engine():
    from scripts.publish_fii_selection_from_local import _warehouse_url
    return create_engine(
        _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))


def _baixador(prov):
    """Texto cru do documento. O `_get` do provider so devolve JSON."""
    def baixar(url: str) -> str | None:
        resp = prov.session.get(
            url, headers={"User-Agent": prov.user_agent,
                          "Accept-Encoding": "gzip, deflate"}, timeout=120)
        if getattr(resp, "status_code", 0) != 200:
            return None
        return resp.text
    return baixar


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    eng = _engine()
    with eng.connect() as conn:
        q = ("SELECT id, cik, name FROM market_us.companies "
             "WHERE cik IS NOT NULL AND lower(btrim(coalesce(sector,'')))"
             " = 'real estate' ORDER BY id")
        if args.limit:
            q += f" LIMIT {int(args.limit)}"
        empresas = [(r[0], r[1], r[2]) for r in conn.execute(text(q))]
    print(f"companhias com rotulo generico 'Real Estate': {len(empresas)}")

    prov = build_edgar_provider()
    baixar = _baixador(prov)
    vereditos: dict[str, list[tuple[int, str]]] = {}
    nao_apurados = []
    for cid, cik, nome in empresas:
        sub = prov._get(SUBMISSIONS_URL.format(cik=str(cik).zfill(10)))
        veredito = apurar_eleicao(sub, baixar)
        if veredito is None:
            nao_apurados.append(nome)
        else:
            vereditos.setdefault(veredito, []).append((cid, nome))
        print(f"   {nome[:44]:44} -> {veredito or 'nao apurado'}")

    for veredito, linhas in sorted(vereditos.items()):
        print(f"\n{veredito}: {len(linhas)}")
    print(f"nao apurados (seguem excluidos): {len(nao_apurados)}")

    if args.dry_run:
        print("[dry-run] nada gravado.")
        return 0
    with eng.begin() as conn:
        for veredito, linhas in vereditos.items():
            conn.execute(
                text("UPDATE market_us.companies SET reit_election=:v "
                     "WHERE id = ANY(:ids)"),
                {"v": veredito, "ids": [linha[0] for linha in linhas]})
    print("gravado no armazem local.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
