"""Onde a safra da Memória de Mercado mora -- um endereço só, para os dois lados.

Por que este módulo existe
--------------------------
Em 06/09/2026 a safra existia e o leitor dizia que não. O construtor
(``scripts/construir_memoria_mercado.py``) gravava 4.463 eventos no banco
``postgres`` do armazém, porque é lá que estão os preços que ele mede; o leitor
(:mod:`core.noticias.bases_historicas`) procurava em ``engine_acervo()``, que
resolve para o banco ``noticias`` do **mesmo** container. Dois bancos, mesma
porta, nenhum erro.

O modo de falha é o pior que este repositório conhece: ``carregar_eventos``
chama ``garantir_schema``, então a leitura no lugar errado **criava** a tabela
vazia e devolvia "memória de mercado sem safra construída" -- uma frase
verdadeira sobre o banco errado, indistinguível de "ainda não foi construída".
É ``memoria: verificador-e-escritor-listas-diferentes`` outra vez, e a correção
tem de ser estrutural: não adianta consertar o endereço nos dois lugares, porque
nada impede que eles voltem a divergir. Passa a existir **uma** função que
responde onde a safra mora, e os dois lados a chamam.

A escolha do endereço
---------------------
O default é o mesmo do acervo de notícias, e não o banco de preços, por causa de
quem consome: o único consumidor em produção da safra é o portão quantitativo do
Motor Conjuntural, que já lê o acervo. Preço é a *evidência* que o construtor
mede, não o lugar onde a medição precisa morar -- e o construtor sabe abrir as
duas conexões, enquanto os três chamadores de produção não passam engine
nenhuma.

``MEMORIA_LOCAL_DB_URL`` existe para quem quiser separar depois sem tocar em
chamador nenhum, exatamente como ``NOTICIAS_LOCAL_DB_URL`` nasceu ao lado de
``MACRO_LOCAL_DB_URL``. Enquanto estiver vazia, memória e acervo dividem o
banco -- e dividem **por decisão registrada aqui**, não por acidente.
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine

logger = logging.getLogger(__name__)

#: Descrição usada nas recusas de destino remoto.
O_QUE = "a safra da Memória de Mercado"


def url_memoria() -> str:
    """URL da safra local, ou string vazia quando não há configuração."""
    from core.config import settings
    from core.noticias.destino import url_acervo

    # `getattr` com default, e nao acesso direto: settings e substituido por
    # duplos em teste e por objetos de configuracao reduzidos em job. Um
    # AttributeError ali derrubaria a coleta inteira por causa de um ajuste
    # OPCIONAL que ninguem configurou -- e "ausente" e "vazio" querem dizer a
    # mesma coisa aqui: mora junto do acervo.
    return getattr(settings, "MEMORIA_LOCAL_DB_URL", "") or url_acervo()


def rotulo_do_destino() -> str:
    """Banco onde a safra é procurada, sem credencial -- para mensagem de erro.

    Uma limitação que diz "sem safra construída" sem dizer *onde* procurou é o
    que fez esta divergência sobreviver: a frase estava certa e apontava para o
    lugar errado.

    A resolução da URL entra **dentro** do ``try``, e não antes dele. Em
    06/09/2026 o ``except`` cobria só o ``make_url`` e ``url_memoria()`` ficava
    de fora: uma configuração reduzida sem ``NOTICIAS_LOCAL_DB_URL`` levantava
    ``AttributeError`` aqui e derrubava o ciclo inteiro de coleta -- por causa
    do *rótulo* de uma limitação que o ciclo ia declarar de qualquer forma. O
    comentário já dizia "nunca derruba leitura"; a guarda cobria metade da
    função. Quem compõe texto de erro não pode ser o que quebra.
    """
    try:
        url = url_memoria()
        if not url:
            return "(nenhum armazém local configurado)"

        from sqlalchemy.engine import make_url

        alvo = make_url(url)
        return f"{alvo.host or 'localhost'}:{alvo.port or 5432}/{alvo.database}"
    except Exception:  # noqa: BLE001 - rótulo é cosmético, nunca derruba leitura
        return "(endereço ilegível)"


def engine_memoria():
    """Engine da safra local, ou ``None`` quando não configurada.

    ``None`` é ausência declarada, não erro: sem armazém a Memória de Mercado
    roda inteira em memória, que é como ela roda nos testes. Quem recebe a
    engine é dono dela e a descarta -- aqui não há cache, pelo mesmo motivo
    documentado em :func:`core.macro_data.database.get_local_macro_engine`.
    """
    url = url_memoria()
    if not url:
        logger.info("Memória de Mercado sem banco local configurado")
        return None
    return create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=1,
                         connect_args={"connect_timeout": 10})
