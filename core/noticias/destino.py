"""Onde o acervo de notícias é gravado -- e por que não é no Supabase.

A conta que decide isto não é de gosto. O universo tem ~3.700 ativos; para
qualquer um passar do piso de três itens numa janela de 30 dias, o acervo
precisa de ~11 mil itens por janela. Com ``titulo``, ``resumo``, duas URLs,
``entidades`` em JSONB e três índices, a linha efetiva fica perto de 2 KB --
~22 MB por janela, **acumulando**, porque notícia é histórico e não foto.
O Supabase tem 23 MB de folga (477 de 500, medidos em 05/09/2026 --
eram 429 em agosto; a folga encolhe sozinha). O acervo estoura isso em pouco mais
de três meses e não para.

Então o acervo inteiro mora no armazém local, e para a produção vai uma vitrine
com a leitura corrente por ativo -- que é tudo o que o prompt consome.

Este módulo não inventa guarda nova: usa :mod:`core.destino_local`, a mesma que
protege a Memória de Mercado.
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine

logger = logging.getLogger(__name__)

#: Descrição usada nas mensagens de recusa. Um "destino recusado" sem dizer o
#: que se tentava gravar manda quem lê o log procurar no código.
O_QUE = "o acervo de notícias"


def url_acervo() -> str:
    """URL do acervo local, ou string vazia quando não há configuração.

    Cai para ``MACRO_LOCAL_DB_URL`` porque é o mesmo servidor -- o armazém do
    Docker na 5433. Ter as duas chaves permite separar os bancos depois sem
    mexer em quem chama.
    """
    from core.config import settings

    return (settings.NOTICIAS_LOCAL_DB_URL
            or settings.MACRO_LOCAL_DB_URL or "")


def engine_acervo():
    """Engine do acervo local, ou ``None`` quando não configurado.

    ``None`` é ausência declarada, não erro: sem armazém o motor de notícias
    roda inteiro em memória, que é como ele roda nos testes. Quem recebe a
    engine é dono dela e a descarta -- aqui não há cache, pelo mesmo motivo
    documentado em :func:`core.macro_data.database.get_local_macro_engine`.
    """
    url = url_acervo()
    if not url:
        logger.info("acervo de notícias sem banco configurado: só em memória")
        return None
    return create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=1,
                         connect_args={"connect_timeout": 10})
