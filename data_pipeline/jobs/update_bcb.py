"""
data_pipeline/jobs/update_bcb.py
Atualiza public.macro com dados macro do BCB/SGS (Selic, IPCA, câmbio, PIB, etc.).

Reutiliza a lógica de scripts/seed_macro_bcb.py mas de forma headless.

Em 03/10/2026 o host da API REST do SGS (``api.bcb.gov.br``) sumiu do DNS e
as sete séries passaram a voltar com 0 pontos, sob um workflow verde: o
orquestrador somava a falha deste job às outras e saía ``partial_success``.
Agora a coleta cai no SOAP do SGS (``core.bcb_sgs``) e, quando mesmo assim
nenhuma série vem, o job devolve ``fatal``: ``run_data_updates.py`` sai com
código 1 em qualquer resultado fatal e o workflow fica vermelho.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

TABLE_NAME  = "macro"
SOURCE_NAME = "Banco Central do Brasil (BCB/SGS)"
JOB_NAME    = "update_bcb"


def avaliar_contagens(counts: dict[str, int]) -> tuple[str, str | None]:
    """Status do job pelo número de pontos de cada série.

    Todas vazias é falha total, nunca sucesso parcial. Algumas vazias é
    ``partial_success`` com as séries nomeadas: o UPSERT preserva o valor
    gravado das colunas que não vieram.
    """
    vazias = sorted(nome for nome, n in counts.items() if not n)
    if not counts or len(vazias) == len(counts):
        return "failed", (
            f"BCB/SGS devolveu 0 pontos em todas as {len(counts)} séries "
            "(nem a API REST nem o SOAP do SGS responderam)"
        )
    if vazias:
        return "partial_success", "Séries sem pontos: " + ", ".join(vazias)
    return "success", None


def run() -> dict:
    """Baixa séries SGS do BCB e faz UPSERT em public.macro."""
    result = {
        "status":           "success",
        "table_name":       TABLE_NAME,
        "source_name":      SOURCE_NAME,
        "job_name":         JOB_NAME,
        "records_inserted": 0,
        "records_updated":  0,
        "records_failed":   0,
        "error_message":    None,
    }

    try:
        __import__("pandas")
        __import__("requests")
    except ImportError as exc:
        result["status"] = "failed"
        result["error_message"] = f"Dependência ausente: {exc}"
        return result

    from data_pipeline.utils.db_utils import get_pipeline_engine
    engine = get_pipeline_engine()
    if engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco não conectado"
        return result

    # Importa lógica do script existente sem executar main()
    try:
        import sys
        from pathlib import Path
        _root = Path(__file__).resolve().parents[2]
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))

        from scripts.seed_macro_bcb import _fetch_macro, _upsert_macro
    except ImportError as exc:
        result["status"] = "failed"
        result["error_message"] = f"Não foi possível importar seed_macro_bcb: {exc}"
        return result

    try:
        start = date(2010, 1, 1)
        end   = datetime.today().date() - timedelta(days=2)
        df, counts = _fetch_macro(start, end)
        result["series_pontos"] = counts

        status, mensagem = avaliar_contagens(counts)
        if status == "failed" or df.empty:
            result["status"] = "failed"
            result["fatal"] = True
            result["error_message"] = mensagem or "Nenhuma série macro retornou dados"
            return result

        with engine.begin() as conn:
            count = _upsert_macro(conn, df, apply=True)

        result["status"] = status
        result["error_message"] = mensagem
        result["records_inserted"] = count
        logger.info("update_bcb: %d anos gravados em public.macro", count)

    except Exception as exc:
        logger.exception("update_bcb falhou")
        result["status"] = "failed"
        result["error_message"] = str(exc)

    return result
