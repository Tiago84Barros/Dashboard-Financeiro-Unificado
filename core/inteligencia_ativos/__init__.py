"""
core/inteligencia_ativos
Serviço da análise individual dos ativos (Investimentos → Inteligência dos
Ativos).

Esta é a porta de entrada que qualquer análise de ativo tem de atravessar.
A ordem é:

1. perguntar ao portão (``core/estrategia/portao.py``) se a estratégia está
   concluída; se não estiver, devolver a resposta de domínio e parar, sem
   carregar carteira nem chamar LLM;
2. montar o ``ContextoInvestidor``: política concluída + carteira completa
   (``contexto.py``);
3. analisar o ativo dentro desse contexto (``analise.py``).

Módulos do pacote:

- ``modelos``   — dataclasses da análise, estados de ação, papéis, perguntas;
- ``contexto``  — política + carteira → ``ContextoInvestidor``;
- ``papeis``    — para que o ativo serve;
- ``adequacao`` — faixa desejada, tese, impacto e ação a considerar;
- ``secoes``    — fundamentos, valuation, pares, cenário, notícias,
  relatórios e eventos (hoje provedores pendentes);
- ``analise``   — orquestra tudo numa ``AnaliseAtivo``.

A redação por LLM ainda não existe: ``analise.texto_para_llm`` já monta a
entrada dela.

Coberto por tests/test_inteligencia_ativos.py.
"""
from __future__ import annotations

from core.estrategia import portao
from core.inteligencia_ativos import analise as _analise
from core.inteligencia_ativos import contexto as _contexto
from core.inteligencia_ativos.contexto import (
    PoliticaNaoConcluida,
    contexto_obrigatorio,
)

__all__ = ["ATIVO_FORA_DA_CARTEIRA", "PoliticaNaoConcluida",
           "contexto_obrigatorio", "analisar_ativo", "analisar_carteira"]

ATIVO_FORA_DA_CARTEIRA = "ASSET_NOT_IN_PORTFOLIO"


def _posicao(carteira: dict, ticker: str) -> dict | None:
    alvo = ticker.strip().upper()
    return next((p for p in carteira.get("posicoes") or []
                 if str(p.get("ticker", "")).strip().upper() == alvo), None)


def _carteira(carteira: dict | None) -> dict:
    if carteira is None:
        from core.investimentos import get_carteira
        carteira = get_carteira()
    return carteira


def analisar_ativo(ticker: str, *, carteira: dict | None = None,
                   engine=None, owner_id=None) -> dict:
    """Analisa um ativo da carteira, se a estratégia permitir.

    ``carteira`` é o dict de ``core.investimentos.get_carteira()``; quem já o
    tem em mãos (a tela) repassa para não ler duas vezes.
    """
    liberacao = portao.verificar(engine=engine, owner_id=owner_id)
    if not liberacao.disponivel:
        return {**liberacao.como_dict(), "asset": ticker}

    carteira = _carteira(carteira)
    posicao = _posicao(carteira, ticker)
    if posicao is None:
        return {**liberacao.como_dict(), "asset": ticker,
                "analysis_available": False,
                "reason": ATIVO_FORA_DA_CARTEIRA}

    ctx = _contexto.montar(liberacao.politica, carteira)
    resultado = _analise.analisar(posicao, ctx)
    return {
        **liberacao.como_dict(),
        "asset": resultado.ativo.ticker,
        "policy_context": ctx.texto_politica,
        "analysis": resultado.como_dict(),
        "llm_input": _analise.texto_para_llm(resultado, ctx),
    }


def analisar_carteira(*, carteira: dict | None = None,
                      liberacao: portao.Liberacao | None = None,
                      engine=None, owner_id=None) -> dict:
    """Todas as posições de uma vez, com um único teste do portão.

    A tela já tem a ``liberacao`` do rerun e a repassa para não ler a
    política duas vezes. Isso não abre brecha: ``contexto.montar`` recusa
    qualquer registro que não esteja COMPLETED.

    Devolve a resposta de domínio e, liberada, ``contexto`` (o
    ``ContextoInvestidor``) e ``analises`` (``list[AnaliseAtivo]``).
    """
    if liberacao is None:
        liberacao = portao.verificar(engine=engine, owner_id=owner_id)
    if not liberacao.disponivel:
        return liberacao.como_dict()
    ctx = _contexto.montar(liberacao.politica, _carteira(carteira))
    return {**liberacao.como_dict(), "contexto": ctx,
            "analises": _analise.analisar_carteira(ctx)}
