"""
core/inteligencia_ativos.py
Serviço da análise individual dos ativos (Investimentos → Inteligência dos
Ativos).

Esta é a porta de entrada que qualquer análise de ativo tem de atravessar.
A ordem é:

1. perguntar ao portão (``core/estrategia/portao.py``) se a estratégia está
   concluída; se não estiver, devolver a resposta de domínio e parar, sem
   carregar carteira nem chamar LLM;
2. localizar o ativo na carteira do usuário;
3. montar a premissa da LLM a partir da política vigente.

A política vem ANTES da carteira, ao contrário do roteiro conceitual
"carregar portfólio → carregar política": o resultado é o mesmo, e assim um
usuário bloqueado não paga a leitura da carteira.

A análise em si (a chamada à LLM) ainda não existe: ``analisar_ativo``
devolve ``analysis = None`` com a premissa já montada. O que já vale é o
contrato: nada analisa sem política concluída, e a premissa só pode ser
montada a partir de uma.

Coberto por tests/test_inteligencia_ativos.py.
"""
from __future__ import annotations

from core.estrategia import politica as pol
from core.estrategia import portao
from core.estrategia import repositorio as repo

ATIVO_FORA_DA_CARTEIRA = "ASSET_NOT_IN_PORTFOLIO"


class PoliticaNaoConcluida(RuntimeError):
    """Tentativa de montar a premissa com política que não está concluída."""


def contexto_obrigatorio(registro: repo.Registro | None) -> str:
    """Premissa que toda análise de ativo leva à LLM.

    Levanta em vez de devolver texto vazio: uma análise sem a estratégia do
    usuário seria genérica, e parecer personalizada sem ser é pior do que
    não responder.
    """
    if registro is None or registro.status != pol.COMPLETED:
        raise PoliticaNaoConcluida(
            "A análise de ativos exige uma estratégia concluída.")
    return pol.texto_da_politica(registro.politica, versao=registro.version,
                                 status=registro.status)


def _posicao(carteira: dict, ticker: str) -> dict | None:
    alvo = ticker.strip().upper()
    return next((p for p in carteira.get("posicoes") or []
                 if str(p.get("ticker", "")).strip().upper() == alvo), None)


def analisar_ativo(ticker: str, *, carteira: dict | None = None,
                   engine=None, owner_id=None) -> dict:
    """Analisa um ativo da carteira, se a estratégia permitir.

    ``carteira`` é o dict de ``core.investimentos.get_carteira()``; quem já o
    tem em mãos (a tela) repassa para não ler duas vezes.
    """
    liberacao = portao.verificar(engine=engine, owner_id=owner_id)
    if not liberacao.disponivel:
        return {**liberacao.como_dict(), "asset": ticker}

    if carteira is None:
        from core.investimentos import get_carteira
        carteira = get_carteira()
    posicao = _posicao(carteira, ticker)
    if posicao is None:
        return {**liberacao.como_dict(), "asset": ticker,
                "analysis_available": False,
                "reason": ATIVO_FORA_DA_CARTEIRA}

    return {
        **liberacao.como_dict(),
        "asset": posicao["ticker"],
        "position": {
            "nome": posicao.get("nome"),
            "classe": posicao.get("classe"),
            "pct_carteira": posicao.get("pct_carteira"),
        },
        "policy_context": contexto_obrigatorio(liberacao.politica),
        "analysis": None,
    }
