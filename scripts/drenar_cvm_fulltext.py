"""
Drena a fila de extração de texto completo CVM/IPE no armazém LOCAL.

Por que existe
--------------
`data_pipeline/jobs/update_cvm_fulltext.py` é um GOTEJAMENTO deliberado: 12
documentos por execução, pausa entre downloads, disjuntor em 3 bloqueios
seguidos. Essa cadência é correta para o job noturno, que roda contra a CVM em
horário compartilhado e não pode ser visto como abuso.

Ela não serve para uma carga inicial. Depois de um backfill de milhares de
metadados (`scripts/backfill_cvm_ipe.py`), a fila levaria centenas de dias para
drenar a 12/dia — e até lá o corpus fica cheio de chunk de metadado, que é
exatamente o ruído que o backfill recortado tenta evitar.

Este script chama o MESMO job em laço, contra o armazém local, até a fila
esvaziar ou o teto de execuções acabar. Ele não reimplementa a extração: herda
o disjuntor, o backoff e a priorização do job. O que ele acrescenta é
persistência entre execuções e um relatório de progresso.

Seguro por construção
---------------------
* destino verificado por `exigir_local` — nunca grava no Supabase;
* idempotente: o job só pega `extraction_version = 'ipe_meta_v1'`, então
  interromper e rodar de novo continua de onde parou;
* para sozinho quando o job devolve zero pendências ou sinaliza bloqueio.

Uso:
  python scripts/drenar_cvm_fulltext.py --ciclos 50 --por-ciclo 20 --delay 1.5
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from core.destino_local import exigir_local  # noqa: E402
from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402

logger = logging.getLogger("drenar_cvm_fulltext")

# pypdf despeja uma linha por objeto malformado em PDF escaneado da CVM. São
# dezenas de milhares por ciclo, e afogam o progresso real no log.
for _ruidoso in ("pypdf", "pypdf._reader", "PyPDF2", "pdfminer"):
    logging.getLogger(_ruidoso).setLevel(logging.CRITICAL)


def _pendentes(url: str) -> int:
    eng = create_engine(url, connect_args={"connect_timeout": 15})
    try:
        with eng.connect() as conn:
            return int(conn.execute(text(
                "SELECT count(*) FROM public.docs_corporativos "
                "WHERE extraction_version = 'ipe_meta_v1'")).scalar() or 0)
    finally:
        eng.dispose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--ciclos", type=int, default=50, help="Máximo de execuções do job.")
    ap.add_argument("--por-ciclo", type=int, default=20, help="Documentos por execução.")
    ap.add_argument("--delay", type=float, default=1.5, help="Segundos entre downloads.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    url = _warehouse_url()
    if not url:
        logger.error("Armazém local indisponível (container dfu_warehouse no ar?).")
        return 1
    eng = create_engine(url, connect_args={"connect_timeout": 15})
    try:
        exigir_local(eng, o_que="extração de texto completo CVM/IPE")
    finally:
        eng.dispose()

    os.environ["SUPABASE_DB_URL_B3"] = url
    os.environ["CVM_FULLTEXT_MAX"] = str(args.por_ciclo)
    os.environ["CVM_FULLTEXT_DELAY"] = str(args.delay)

    from data_pipeline.jobs.update_cvm_fulltext import run

    inicio = time.time()
    restam = _pendentes(url)
    logger.info("pendentes no início: %d", restam)
    if not restam:
        return 0

    for i in range(1, args.ciclos + 1):
        t0 = time.time()
        r = run()
        restam = _pendentes(url)
        logger.info("ciclo %d/%d | %s | %.0fs | restam %d",
                    i, args.ciclos, r.get("error_message") or r.get("status"),
                    time.time() - t0, restam)
        if r.get("status") == "failed":
            logger.error("job falhou — interrompendo para não insistir contra a CVM.")
            break
        if not restam:
            logger.info("fila drenada.")
            break

    logger.info("FIM: restam %d | %.1f min", restam, (time.time() - inicio) / 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
