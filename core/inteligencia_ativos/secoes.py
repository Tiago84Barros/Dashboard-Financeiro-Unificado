"""
core/inteligencia_ativos/secoes.py
Seções de dados externos da análise: fundamentos, valuation, pares, cenário,
notícias, relatórios e próximos eventos.

Cada seção tem um provedor com a mesma assinatura::

    provedor(info: InfoBasica, ctx: ContextoInvestidor) -> Secao

Hoje todos são ``_pendente``: devolvem ``estado=PENDENTE`` e o que a seção
vai trazer. Implementar uma etapa é trocar a entrada dela em ``PROVEDORES``
por um provedor real; nada mais na análise ou na tela precisa mudar. Um
provedor que falha não derruba a análise: ``coletar`` converte a exceção em
``SEM_DADOS`` com o motivo.

Fontes que já existem no projeto e devem alimentar estas seções:

- fundamentos/valuation B3: vitrine de Empresas B3 (score e múltiplos);
- fundamentos/valuation FII: ``fii_selection_snapshot`` (P/VP, DY, vacância);
- EUA: vitrine de Empresas Americanas;
- notícias: acervo de notícias com ``ticker_sentiment`` (corte 0,70);
- relatórios: RAG de relatórios da B3 / FII;
- cenário: insumos macro publicados (Selic, IPCA, câmbio).
"""
from __future__ import annotations

import logging
from typing import Callable

from core.inteligencia_ativos.modelos import (
    PENDENTE,
    SEM_DADOS,
    ContextoInvestidor,
    InfoBasica,
    Secao,
)

logger = logging.getLogger(__name__)

Provedor = Callable[[InfoBasica, ContextoInvestidor], Secao]

# chave → (título, o que a seção vai trazer)
SECOES: dict[str, tuple[str, str]] = {
    "fundamentos": ("Fundamentos",
                    "Qualidade operacional, endividamento, rentabilidade e "
                    "histórico de proventos."),
    "valuation": ("Valuation",
                  "Múltiplos atuais contra o histórico do próprio ativo "
                  "(P/L, P/VP, dividend yield)."),
    "pares": ("Comparação com pares",
              "O ativo contra empresas ou fundos do mesmo segmento."),
    "cenario": ("Cenário",
                "Como juros, inflação e câmbio afetam este tipo de ativo."),
    "noticias": ("Notícias",
                 "Notícias recentes em que o ativo é o assunto, não só citado."),
    "relatorios": ("Relatórios",
                   "Resultados trimestrais, relatórios gerenciais e fatos "
                   "relevantes."),
    "eventos": ("Próximos eventos",
                "Data-com, divulgação de resultados, assembleias e "
                "vencimentos."),
}


def _pendente(chave: str) -> Provedor:
    titulo, promessa = SECOES[chave]

    def provedor(info: InfoBasica, ctx: ContextoInvestidor) -> Secao:
        return Secao(chave=chave, titulo=titulo, estado=PENDENTE,
                     resumo=f"Em preparação. Vai trazer: {promessa}")
    return provedor


PROVEDORES: dict[str, Provedor] = {c: _pendente(c) for c in SECOES}


def coletar(info: InfoBasica, ctx: ContextoInvestidor) -> dict[str, Secao]:
    saida = {}
    for chave, provedor in PROVEDORES.items():
        try:
            saida[chave] = provedor(info, ctx)
        except Exception as exc:  # um provedor ruim não derruba a análise
            logger.warning("[inteligencia_ativos] seção %s falhou para %s: %s",
                           chave, info.ticker, type(exc).__name__)
            saida[chave] = Secao(chave=chave, titulo=SECOES[chave][0],
                                 estado=SEM_DADOS,
                                 resumo="Não foi possível obter os dados "
                                        "desta seção agora.")
    return saida
