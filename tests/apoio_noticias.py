"""Construtores sintéticos para os testes do Motor Conjuntural.

Nada aqui toca rede, banco ou arquivo do usuário. Todos os textos, URLs e
tickers são inventados para o teste, como manda o ``AGENTS.md`` ("usar dados
sintéticos em testes"). O relógio é fixo: notícia "de agora" e notícia "antiga"
precisam ser afirmações estáveis, não função do dia em que a suíte roda.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.noticias.coleta import para_noticia
from core.noticias.provedores.base import (
    ORIGEM_CACHE_VENCIDO,
    ORIGEM_REDE,
    Consulta,
    ItemBruto,
    RespostaProvedor,
)

AGORA = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def quando(horas_atras: float) -> str:
    """Data de publicação em ISO/UTC, a tantas horas do relógio do teste."""
    return (AGORA - timedelta(hours=horas_atras)).isoformat()


def item(titulo: str, url: str, **kw) -> ItemBruto:
    return ItemBruto(titulo=titulo, url=url, **kw)


def noticia(titulo: str, url: str, *, provedor: str = "teste", **kw):
    """Notícia pelo caminho real: item bruto -> normalização -> registro."""
    return para_noticia(item(titulo, url, **kw), provedor, coletado_em=AGORA)


class ProvedorFalso:
    """Provedor que obedece ao contrato e responde o que o teste mandar.

    Existe para provar a afirmação central da camada de abstração: o motor não
    conhece nenhuma API concreta. Se um provedor escrito dentro do teste
    funciona no orquestrador, o acoplamento a uma API específica não existe.
    """

    def __init__(self, nome: str, itens=(), *, erro: Exception | None = None,
                 cache_vencido=None, limitacoes: tuple[str, ...] = ()):
        self.nome = nome
        self._itens = tuple(itens)
        self._erro = erro
        self._cache_vencido = cache_vencido
        self._limitacoes = limitacoes
        self.chamadas = 0

    def disponivel(self) -> bool:
        return True

    def buscar(self, consulta: Consulta) -> RespostaProvedor:
        self.chamadas += 1
        if self._erro is not None:
            raise self._erro
        return RespostaProvedor(
            provedor=self.nome,
            itens=self._itens,
            origem=ORIGEM_REDE,
            consultado_em=AGORA,
            dados_de=AGORA,
            limitacoes=self._limitacoes,
        )

    def do_cache_vencido(self, consulta: Consulta):
        if self._cache_vencido is None:
            return None
        return RespostaProvedor(
            provedor=self.nome,
            itens=tuple(self._cache_vencido),
            origem=ORIGEM_CACHE_VENCIDO,
            consultado_em=AGORA,
            dados_de=AGORA - timedelta(days=2),
            limitacoes=self._limitacoes,
        )
