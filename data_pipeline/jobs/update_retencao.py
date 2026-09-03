"""
data_pipeline/jobs/update_retencao.py
Varredura de retenção da trilha de auditoria.

Por que existe um job só para isto
----------------------------------
:func:`core.auditoria.trilha.expurgar` foi escrito junto com a trilha e nunca
teve quem o chamasse. Uma política de retenção que ninguém executa não é
política: é um parágrafo de documentação em cima de uma tabela que só cresce,
num Supabase medido em 427 MB de 500.

O irmão dela (``core.noticias.estado_coleta.expurgar``) roda dentro do job de
notícias -- mas esse job nasce inativo e o workflow dele depende de uma variável
que não existe. Pendurar a trilha ali seria repetir o mesmo defeito com outro
nome. A trilha cresce com o **uso do app**, não com a coleta, e por isso mora
no cron diário que realmente executa (``.github/workflows/data_pipeline.yml``).

Simula por omissão
------------------
Apaga de verdade só com ``AUDITORIA_EXPURGO_APLICAR=true``, no mesmo espírito
de ``AUDIT_HEAL_APPLY`` em :mod:`data_pipeline.jobs.audit_and_heal`. Apagar
registro de auditoria é irreversível e é decisão de quem opera, não do job.

Mas simular **não** é não fazer nada: o alcance da janela é contado e devolvido
em toda execução. Assim a dívida aparece medida todo dia, em vez de aparecer no
dia em que o banco encher.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

TABLE_NAME = "recomendacao_auditoria"
SOURCE_NAME = "Retencao da trilha de auditoria"
JOB_NAME = "update_retencao"

VAR_APLICAR = "AUDITORIA_EXPURGO_APLICAR"


def aplicar_ligado(valor: str | None) -> bool:
    """Só ``true``/``1``/``yes`` ligam. Função pura para o teste alcançar.

    Qualquer outra coisa -- inclusive erro de digitação como ``"ture"`` -- cai
    no lado seguro. Ler um valor desconhecido como "sim" seria deixar um erro
    de digitação apagar auditoria.
    """
    return str(valor or "").strip().lower() in {"1", "true", "yes"}


def run() -> dict:
    result = {
        "status": "success",
        "table_name": TABLE_NAME,
        "source_name": SOURCE_NAME,
        "job_name": JOB_NAME,
        "records_inserted": 0,
        "records_updated": 0,
        "records_failed": 0,
        "error_message": None,
    }

    from core.auditoria import trilha
    from data_pipeline.utils.db_utils import get_pipeline_engine

    engine = get_pipeline_engine()
    if engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco nao conectado"
        return result

    aplicar = aplicar_ligado(os.getenv(VAR_APLICAR))
    try:
        expurgo = trilha.expurgar(engine=engine, aplicar=aplicar)
    except Exception as exc:  # noqa: BLE001 - fronteira do job
        # A tabela pode ainda não existir numa instalação que nunca recomendou.
        # Isso não é falha do job: é ausência do que expurgar.
        logger.info("Expurgo da trilha nao executado (%s)", exc)
        result["status"] = "skipped"
        result["error_message"] = str(exc)
        return result

    if expurgo.get("recusado"):
        result["status"] = "partial_success"
        result["error_message"] = expurgo["recusado"]
        return result

    alcance = int(expurgo.get("alcance", 0))
    removidos = int(expurgo.get("removidos", 0))
    result["records_updated"] = removidos
    if alcance and not aplicar:
        # Sai como sucesso: a varredura fez o que lhe cabia. O que falta é
        # autorização, e ela precisa estar escrita onde alguém leia.
        result["error_message"] = (
            f"{alcance} registro(s) alem de {trilha.RETENCAO_DIAS} dias; nada "
            f"removido porque {VAR_APLICAR} nao esta ligado")
        logger.warning("Retencao da trilha pendente: %s", result["error_message"])
    return result
