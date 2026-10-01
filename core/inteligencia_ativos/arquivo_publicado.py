"""
core/inteligencia_ativos/arquivo_publicado.py
Leitura compartilhada dos artefatos ``.json.gz`` publicados em ``data/public``.

Por que não ``st.cache_data``: ele devolve uma cópia por pickle a cada acerto.
A análise da carteira consulta o arquivo de informações recentes (2,2 MB) uma
vez por ativo e por seção — 87 vezes num rerun de Investimentos, ~2,4 s de CPU
só desserializando cópias. Aqui o arquivo é lido uma vez e o mesmo dict é
devolvido até o arquivo mudar no disco (data de modificação ou tamanho).

O dict é compartilhado entre sessões: quem o recebe só lê. Os consumidores
(``fontes_informacoes``, ``fontes_valuation``) montam objetos novos a partir
dele e nunca o alteram.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MEMO: dict[str, tuple[tuple[int, int], dict]] = {}


def _assinatura(caminho: str) -> tuple[int, int] | None:
    try:
        st = os.stat(caminho)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def ler(caminho: str, rotulo: str) -> dict:
    """Conteúdo do ``.json.gz``; ``{}`` se ausente ou ilegível (nunca levanta)."""
    assinatura = _assinatura(caminho)
    if assinatura is None:
        logger.info("[%s] arquivo ausente: %s", rotulo, caminho)
        return {}
    with _LOCK:
        visto = _MEMO.get(caminho)
        if visto is not None and visto[0] == assinatura:
            return visto[1]
        try:
            with gzip.open(caminho, "rb") as fh:
                dado = json.loads(fh.read().decode("utf-8"))
        except Exception as exc:  # arquivo corrompido não derruba a seção
            logger.warning("[%s] arquivo ilegível: %s", rotulo,
                           type(exc).__name__)
            return {}
        _MEMO[caminho] = (assinatura, dado)
        return dado
