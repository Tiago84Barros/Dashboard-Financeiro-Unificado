"""
data_pipeline/jobs/update_noticias.py
=====================================
Coleta recorrente do Motor Conjuntural de notícias.

Nasce **inativo** no registro (``is_active: False``) e é essa a decisão
deliberada: o job consome cota de APIs gratuitas, e ligá-lo por conta própria
gastaria a cota diária do usuário sem que ele tivesse pedido. Para ativar,
basta uma chave configurada e virar a flag em ``data_pipeline/update_registry.py``.

Respeita a cadência registrada em ``core/noticias/frescor_noticias.py``: sem
``--force``, uma execução dentro do intervalo configurado não gasta requisição
nenhuma. Falha de um provedor não derruba o job -- o resultado sai com
``records_failed`` e a lista do que faltou, que é o comportamento pedido para
falha parcial.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TABLE_NAME = "noticias_itens"
SOURCE_NAME = "Motor Conjuntural (provedores de noticias)"
JOB_NAME = "update_noticias"


def run(tickers: tuple[str, ...] = (), *, forcar: bool = False,
        emergencia: bool = False) -> dict:
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

    from core.config import settings
    from core.noticias.armazenamento import gravar
    from core.noticias.cache import Cache
    from core.noticias.coleta import coletar
    from core.noticias.frescor_noticias import RegistroColeta
    from core.noticias.provedores.base import Consulta
    from core.noticias.provedores.registro import construir
    from core.noticias.rate_limit import Orcamento

    registro = RegistroColeta()
    cadencia = (settings.noticias_freq_emergencia_min if emergencia
                else settings.noticias_freq_normal_min)

    if not registro.precisa_coletar(JOB_NAME, cadencia_minutos=cadencia,
                                    forcar=forcar):
        estado = registro.estado(JOB_NAME, cadencia_minutos=cadencia)
        result["status"] = "skipped"
        result["error_message"] = (
            f"Dentro da cadencia de {cadencia:.0f} min ({estado.texto()})")
        return result

    try:
        provedores = construir(
            orcamento=Orcamento(),
            cache=Cache(ttl_s=settings.noticias_cache_ttl_s),
        )
    except Exception as exc:  # noqa: BLE001
        result["status"] = "failed"
        result["error_message"] = f"Falha ao montar provedores: {exc}"
        return result

    if not provedores:
        result["status"] = "failed"
        result["error_message"] = (
            "Nenhum provedor de noticias disponivel "
            "(verifique NOTICIAS_PROVEDORES e as chaves)")
        return result

    resultado = coletar(
        Consulta(tickers=tuple(tickers), limite=settings.noticias_limite),
        provedores,
        registro=registro,
    )

    result["records_failed"] = len(resultado.falhas)
    if resultado.falhas:
        result["error_message"] = "; ".join(f.texto() for f in resultado.falhas)

    if resultado.sem_fonte:
        result["status"] = "failed"
        return result

    try:
        gravacao = gravar(resultado)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "failed"
        result["error_message"] = f"Falha ao gravar noticias: {exc}"
        return result

    result["records_inserted"] = gravacao.get("itens", 0)
    result["records_updated"] = gravacao.get("avaliacoes", 0)
    if not gravacao.get("gravado"):
        # Coletou e avaliou, mas não persistiu. Não é sucesso pleno e o motivo
        # precisa aparecer -- "0 registros" sem explicação lê como fonte vazia.
        # "partial_success" e nao "partial": o orquestrador so aceita
        # {success, partial_success, skipped, failed} e converte qualquer outra
        # coisa em "failed" com "Status de job invalido" -- o motivo real se
        # perderia.
        result["status"] = "partial_success"
        result["error_message"] = "; ".join(filter(None, [
            result["error_message"], str(gravacao.get("motivo", ""))]))

    if resultado.falhas and result["status"] == "success":
        result["status"] = "partial_success"

    registro.registrar_sucesso(JOB_NAME, itens=len(resultado.avaliadas))
    return result
