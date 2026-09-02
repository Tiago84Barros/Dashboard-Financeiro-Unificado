"""Montagem dos provedores a partir da configuração.

É o único lugar que conhece, ao mesmo tempo, os nomes dos provedores e as
chaves deles -- e mesmo aqui as chaves vêm de ``core.config.settings``, nunca
de ``os.environ``. Nenhuma chave é impressa, logada ou devolvida: ``descrever``
informa apenas se existe chave, jamais qual.

A ordem de ``NOTICIAS_PROVEDORES`` é a ordem de tentativa, e é assim que o
requisito de "fallback entre provedores quando configurados" é atendido sem
que o código conheça um provedor preferido: quem decide é a configuração.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from core.noticias.cache import Cache
from core.noticias.provedores.alphavantage import AlphaVantage
from core.noticias.provedores.marketaux import Marketaux
from core.noticias.provedores.rss import feeds_padrao
from core.noticias.rate_limit import Orcamento
from core.noticias.transporte import Transporte, TransporteRequests

logger = logging.getLogger(__name__)

#: Construtores por família. Registrar um provedor novo é acrescentar uma
#: linha aqui -- nenhum outro módulo precisa saber que ele existe.
FABRICAS = {
    "alphavantage": AlphaVantage,
    "marketaux": Marketaux,
}

#: Famílias que produzem várias instâncias (uma por feed).
MULTIPLAS = {"rss": feeds_padrao}

#: Famílias que não exigem chave e por isso servem de piso quando a cota acaba.
SEM_CHAVE = frozenset({"rss"})


@dataclass(frozen=True)
class Situacao:
    """Estado de um provedor configurado, sem revelar segredo algum."""

    nome: str
    familia: str
    disponivel: bool
    exige_chave: bool
    tem_chave: bool
    motivo: str | None = None


def _config():
    from core.config import settings
    return settings


def construir(
    nomes=None,
    *,
    transporte: Transporte | None = None,
    orcamento: Orcamento | None = None,
    cache: Cache | None = None,
    config=None,
    **kwargs,
) -> list:
    """Instancia os provedores configurados, na ordem configurada.

    Provedor sem chave **não** é instanciado: instanciar e falhar depois faria
    a coleta gastar uma tentativa e registrar uma falha de rede para algo que
    é, na verdade, configuração ausente.
    """
    cfg = config if config is not None else _config()
    pedidos = list(nomes) if nomes is not None else list(cfg.provedores_noticias)
    transporte = transporte or TransporteRequests()

    comuns = dict(orcamento=orcamento, cache=cache, **kwargs)
    construidos: list = []

    for nome in pedidos:
        familia = str(nome).strip().lower()
        if not familia:
            continue

        if familia in MULTIPLAS:
            construidos.extend(MULTIPLAS[familia](transporte, **comuns))
            continue

        fabrica = FABRICAS.get(familia)
        if fabrica is None:
            logger.warning("Provedor de noticias desconhecido: %s", familia)
            continue

        chave = cfg.chave_noticias(familia)
        if not chave:
            logger.info("Provedor %s sem chave configurada: ignorado", familia)
            continue

        construidos.append(fabrica(transporte, chave=chave, **comuns))

    return construidos


def descrever(nomes=None, *, config=None) -> list[Situacao]:
    """Situação de cada provedor pedido, para a tela explicar o que falta.

    Não instancia nada e não toca a rede: serve para a UI dizer "Marketaux sem
    chave" antes de qualquer coleta.
    """
    cfg = config if config is not None else _config()
    pedidos = list(nomes) if nomes is not None else list(cfg.provedores_noticias)
    situacoes: list[Situacao] = []

    for nome in pedidos:
        familia = str(nome).strip().lower()
        if not familia:
            continue
        if familia in MULTIPLAS:
            situacoes.append(Situacao(
                nome=familia, familia=familia, disponivel=True,
                exige_chave=False, tem_chave=True,
                motivo="feeds publicos, sem chave"))
            continue
        if familia not in FABRICAS:
            situacoes.append(Situacao(
                nome=familia, familia=familia, disponivel=False,
                exige_chave=False, tem_chave=False,
                motivo="provedor desconhecido"))
            continue
        tem = bool(cfg.chave_noticias(familia))
        situacoes.append(Situacao(
            nome=familia, familia=familia, disponivel=tem,
            exige_chave=True, tem_chave=tem,
            motivo=None if tem else "chave nao configurada"))

    return situacoes
