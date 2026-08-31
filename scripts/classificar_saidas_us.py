# -*- coding: utf-8 -*-
"""Apura POR QUE cada empresa americana ja ingerida saiu da bolsa.

`market_us.assets` tem 1.607 linhas com `delisted_date` preenchida, e ate aqui
"saiu" era rotulo unico para dois desfechos opostos. Quem foi comprada com
premio devolveu capital; quem quebrou destruiu. Enquanto os dois moram no mesmo
balde, qualquer convencao de retorno de deslistagem e chute.

A regra mora em `core.us_saida_causa` e nao se repete aqui: item 1.03 do 8-K
(falencia, em qualquer momento) tem precedencia sobre 2.01 (aquisicao, so na
janela final), porque venda de ativos dentro da recuperacao judicial tambem
arquiva 2.01.

A fonte e o `submissions` da SEC -- nao o companyfacts. So precisamos de formas
e itens, e o companyfacts de uma empresa passa facilmente de 10 MB. Cada
resposta fica em cache no disco: reclassificar depois de uma correcao de regra
nao pode custar 1.607 requisicoes de novo.

`indefinido` e gravado como valor, nunca deixado NULL: NULL significa "ainda nao
perguntei" e indefinido significa "perguntei e a SEC nao responde". Confundir os
dois faz a proxima rodada reprocessar 1.100 empresas para chegar ao mesmo lugar.

    python scripts/classificar_saidas_us.py --dry-run --limit 20
    python scripts/classificar_saidas_us.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from core.us_saida_causa import DIAS_FIM, classificar, itens_de_8k  # noqa: E402

AGENTE = "Dashboard Financeiro Unificado tsbcorporation84@gmail.com"
URL_SUB = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
CACHE = ROOT / "local_staging" / "cache_us_saidas"
# A SEC pede no maximo 10 requisicoes por segundo. Passar disso devolve 403 e o
# bloqueio nao se anuncia: as respostas simplesmente viram erro.
PAUSA = 0.12

_SQL_ALVOS = """
  SELECT a.id, a.symbol, c.cik, a.delisted_date, a.delisting_cause
  FROM market_us.assets a
  JOIN market_us.companies c ON c.id = a.company_id
  WHERE a.delisted_date IS NOT NULL
  ORDER BY a.symbol
"""

_SQL_GRAVA = """
  UPDATE market_us.assets
     SET delisting_cause = :causa,
         delisting_cause_at = NOW(),
         updated_at = NOW()
   WHERE id = :id
"""


def _engine():
    from scripts.publish_fii_selection_from_local import _warehouse_url
    return create_engine(
        _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))


def _submissions(cik: int) -> dict | None:
    """Formas e itens de 8-K do CIK, com cache em disco.

    O cache guarda tambem o fracasso (`{}`), senao a empresa sem submissions e
    reperguntada a cada execucao e a rodada nunca fica mais barata.
    """
    alvo = CACHE / "{}.json".format(cik)
    if alvo.exists():
        try:
            return json.loads(alvo.read_text(encoding="utf-8")) or None
        except Exception:  # noqa: BLE001
            return None
    alvo.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(URL_SUB.format(cik=cik),
                                     headers={"User-Agent": AGENTE})
        sub = json.loads(urllib.request.urlopen(req, timeout=90).read())
    except Exception:  # noqa: BLE001
        alvo.write_text("{}", encoding="utf-8")
        return None
    recentes = (sub.get("filings") or {}).get("recent") or {}
    pacote = {"formas": sorted({str(f) for f in (recentes.get("form") or [])})}
    pacote.update(itens_de_8k(recentes, DIAS_FIM))
    alvo.write_text(json.dumps(pacote), encoding="utf-8")
    return pacote


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="classifica e imprime, sem gravar")
    ap.add_argument("--refazer", action="store_true",
                    help="reclassifica tambem quem ja tem causa gravada")
    args = ap.parse_args(argv)

    engine = _engine()
    with engine.connect() as conn:
        alvos = [dict(r._mapping) for r in conn.execute(text(_SQL_ALVOS))]
    if not args.refazer:
        alvos = [a for a in alvos if not a["delisting_cause"]]
    if args.limit:
        alvos = alvos[:args.limit]

    contagem: Counter[str] = Counter()
    sem_fonte = 0
    t0 = time.time()
    for i, alvo in enumerate(alvos, 1):
        try:
            cik = int(str(alvo["cik"]).lstrip("0") or 0)
        except Exception:  # noqa: BLE001
            cik = 0
        pacote = _submissions(cik) if cik else None
        if pacote is None:
            sem_fonte += 1
            pacote = {}
        else:
            time.sleep(PAUSA)
        causa = classificar(pacote.get("formas"), pacote.get("itens_finais"),
                            pacote.get("itens_todos"))
        contagem[causa] += 1
        if not args.dry_run:
            with engine.begin() as conn:
                conn.execute(text(_SQL_GRAVA), {"causa": causa, "id": alvo["id"]})
        if i % 100 == 0:
            print("[{}/{}] {} ({:.0f}s)".format(i, len(alvos), dict(contagem),
                                                time.time() - t0), flush=True)

    print(json.dumps({"alvos": len(alvos), "causas": dict(contagem),
                      "sem_submissions": sem_fonte, "gravado": not args.dry_run,
                      "segundos": round(time.time() - t0, 1)}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
