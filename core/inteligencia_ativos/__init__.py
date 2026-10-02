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
- ``calculos``  — pesos, alocação vs alvo, faixas, desvios, concentração
  (HHI) e alertas objetivos: todo número da análise, sem LLM;
- ``contexto``  — política + carteira + cálculos → ``ContextoInvestidor``;
- ``papeis``    — para que o ativo serve;
- ``adequacao`` — faixa desejada, tese, impacto e ação a considerar;
- ``secoes``    — fundamentos, valuation, pares, cenário, notícias,
  relatórios e eventos (hoje provedores pendentes);
- ``analise``   — orquestra tudo numa ``AnaliseAtivo``;
- ``painel``    — resumo da carteira e cartão de cada ativo para o painel
  da aba (puro);
- ``historico`` — fotos das análises, regra de quando salvar, comparação
  com a anterior e campos de auditoria (puro); ``historico_repo`` grava em
  ``user_settings.extra_settings``.

O Cenário de Investimentos do usuário (``core/cenario``) entra no contexto
como premissa só de leitura: ``_cenario`` o lê junto com os sinais de revisão.
Falha na leitura vira "sem cenário", nunca bloqueio da análise.

A leitura por LLM é o Portfolio Fit (``portfolio_fit`` + ``leitura_llm``);
ela registra o modelo que de fato respondeu, para a auditoria.

Coberto por tests/test_inteligencia_ativos.py.
"""
from __future__ import annotations

import logging

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

logger = logging.getLogger(__name__)


def _cenario(engine=None, owner_id=None) -> tuple:
    """(cenário, sinais de revisão). Leitura apenas: nada aqui grava.

    Desde 30/09/2026 o cenário é lido dos dados (``core.cenario.automatico``),
    não perguntado ao usuário. Os sinais de revisão comparavam a premissa do
    usuário com os dados; com o cenário feito dos próprios dados, não há o
    que comparar. ``owner_id`` fica na assinatura: o cenário é o mesmo para
    todos.
    """
    from core.cenario import automatico
    try:
        return automatico.carregar(engine), ()
    except Exception:  # noqa: BLE001 — sem cenário a análise segue
        logger.warning("inteligencia_ativos: cenário ilegível", exc_info=True)
        return None, ()


def _posicao(carteira: dict, ticker: str) -> dict | None:
    """Posição de ``ticker``; o fracionário (BBAS3F) acha a do lote padrão.

    A carteira já chega agrupada pelo ticker-base, então quem pergunta pelo
    ticker com F não pode receber "fora da carteira".
    """
    from core.investimentos import _base_ticker
    alvo = ticker.strip().upper()
    posicoes = carteira.get("posicoes") or []
    exata = next((p for p in posicoes
                  if str(p.get("ticker", "")).strip().upper() == alvo), None)
    if exata is not None:
        return exata
    base = _base_ticker(alvo)
    return next((p for p in posicoes
                 if _base_ticker(str(p.get("ticker", ""))) == base), None)


def _carteira(carteira: dict | None) -> dict:
    if carteira is None:
        from core.investimentos import get_carteira
        carteira = get_carteira()
    return carteira


def analisar_ativo(ticker: str, *, carteira: dict | None = None,
                   faixas: dict | None = None, engine=None,
                   owner_id=None) -> dict:
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

    cenario, sinais = _cenario(engine, owner_id)
    ctx = _contexto.montar(liberacao.politica, carteira, faixas=faixas,
                           cenario=cenario, sinais_cenario=sinais)
    resultado = _analise.analisar(posicao, ctx)
    return {
        **liberacao.como_dict(),
        "asset": resultado.ativo.ticker,
        "policy_context": ctx.texto_politica,
        "analysis": resultado.como_dict(),
        "portfolio_calculations": ctx.calculos.como_dict(),
        "llm_input": _analise.texto_para_llm(resultado, ctx),
    }


def analisar_carteira(*, carteira: dict | None = None,
                      liberacao: portao.Liberacao | None = None,
                      faixas: dict | None = None,
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
    cenario, sinais = _cenario(engine, owner_id)
    ctx = _contexto.montar(liberacao.politica, _carteira(carteira),
                           faixas=faixas, cenario=cenario,
                           sinais_cenario=sinais)
    return {**liberacao.como_dict(), "contexto": ctx,
            "analises": _analise.analisar_carteira(ctx)}
