"""
data_pipeline/jobs/heal_fundamentals.py
Job de SANEAMENTO do universo B3 (agendável).

Para cada lote de tickers de `public.setores`, roda o saneamento cruzado
(core.data_healing): busca Fundamentus + Status Invest, exige ≥2 fontes
concordantes, corrige/preenche o banco (com backup + auditoria) e respeita
rate-limit entre lotes. `apply=False` por padrão (dry-run).

Uso (orquestrador) — run(apply=True, batch_size=40, max_tickers=None).
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

JOB_NAME = "heal_fundamentals"


def run(apply: bool = False, batch_size: int = 40, max_tickers: int | None = None,
        sleep_between_batches: float = 2.0) -> dict:
    result = {
        "status": "success", "job_name": JOB_NAME,
        "tickers_processados": 0, "propostas": 0, "gravados": 0,
        "error_message": None,
    }
    try:
        from sqlalchemy import text
        from data_pipeline.utils.db_utils import get_pipeline_engine
        import core.data_healing as healing
        import pandas as pd  # noqa: F401
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"dependência: {exc}"
        return result

    engine = get_pipeline_engine()
    if engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco não conectado"
        return result

    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT ticker FROM public.setores WHERE ticker IS NOT NULL ORDER BY ticker"
            )).fetchall()
        tickers = [str(r[0]).strip().upper().replace(".SA", "") for r in rows if r[0]]
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"listar tickers: {exc}"
        return result

    if max_tickers:
        tickers = tickers[:max_tickers]
    if not tickers:
        result["status"] = "skipped"
        result["error_message"] = "Nenhum ticker em setores"
        return result

    total_prop = total_grav = 0
    for i in range(0, len(tickers), batch_size):
        lote = tickers[i:i + batch_size]
        try:
            preview = healing.preview_healing(lote)
            graváveis = 0
            if not preview.empty:
                graváveis = int(
                    preview["Acao"].isin(["corrigido", "preenchido"]).sum()
                )
                total_prop += graváveis
                if apply and graváveis:
                    out = healing.apply_healing(preview)
                    total_grav += int(out.get("gravados", 0) or 0)
                    if out.get("erro"):
                        logger.warning("heal lote %s: %s", i // batch_size, out["erro"])
            result["tickers_processados"] += len(lote)
        except Exception as exc:
            logger.warning("heal_fundamentals lote %s falhou: %s", i // batch_size, exc)
        if sleep_between_batches:
            time.sleep(sleep_between_batches)

    result["propostas"] = total_prop
    result["gravados"] = total_grav
    if not apply:
        result["error_message"] = f"dry-run: {total_prop} propostas (nada gravado)"
    return result
