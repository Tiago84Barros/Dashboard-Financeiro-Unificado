"""
core/inteligencia_ativos/secoes.py
Seções de dados externos da análise: fundamentos, valuation, pares, cenário,
notícias, relatórios e próximos eventos.

Cada seção tem um provedor com a mesma assinatura::

    provedor(info: InfoBasica, ctx: ContextoInvestidor) -> Secao

``fundamentos`` já é real (``provedor_fundamentos``: catálogo por classe
em ``fundamentos.py``, leitores em ``fontes_fundamentos.py``), assim como
``valuation`` e ``pares`` (``valuation.py``, ``pares.py``, leitores em
``fontes_valuation.py``), e ``noticias``, ``relatorios`` e ``eventos``
(``informacoes.py``, leitor em ``fontes_informacoes.py``, artefato gerado
por ``scripts/publish_informacoes_recentes.py``), e ``cenario``, que lê o
Cenário de Investimentos do próprio usuário (``core/cenario``) já carregado
no ``ContextoInvestidor``. Uma seção sem provedor real usaria ``_pendente``:
devolve ``estado=PENDENTE`` e o que a seção vai trazer. Implementar uma etapa é trocar a entrada dela em ``PROVEDORES``
por um provedor real; nada mais na análise ou na tela precisa mudar. Um
provedor que falha não derruba a análise: ``coletar`` converte a exceção em
``SEM_DADOS`` com o motivo.

Fontes que já existem no projeto e devem alimentar estas seções:

- fundamentos/valuation B3: vitrine de Empresas B3 (score e múltiplos);
- fundamentos/valuation FII: ``fii_selection_snapshot`` (P/VP, DY, vacância);
- EUA: vitrine de Empresas Americanas;
- notícias: acervo de notícias do armazém local, filtrado pela manchete;
- relatórios: documentos CVM (IPE) da B3 e FNET dos FIIs, só metadados;
- eventos: proventos anunciados (``market.dividends``), prazo regulatório
  de resultado e vencimento do Tesouro;
- cenário: o Cenário de Investimentos do usuário, com sinais de revisão
  calculados contra os insumos macro publicados (Selic, IPCA, câmbio).
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Callable

from core.inteligencia_ativos.modelos import (
    DISPONIVEL,
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
                "O Cenário de Investimentos que você cadastrou, como "
                "premissa do ambiente econômico."),
    "noticias": ("Notícias",
                 "Notícias recentes em que o ativo é o assunto, com impacto "
                 "e dimensões afetadas."),
    "relatorios": ("Relatórios",
                   "Balanços, releases, relatórios gerenciais, fatos "
                   "relevantes e comunicados recentes."),
    "eventos": ("Próximos eventos",
                "Proventos anunciados, prazo de resultado e vencimentos."),
}


def _pendente(chave: str) -> Provedor:
    titulo, promessa = SECOES[chave]

    def provedor(info: InfoBasica, ctx: ContextoInvestidor) -> Secao:
        return Secao(chave=chave, titulo=titulo, estado=PENDENTE,
                     resumo=f"Em preparação. Vai trazer: {promessa}")
    return provedor


def _ler_fundamentos(info: InfoBasica):
    from core.inteligencia_ativos import fontes_fundamentos
    return fontes_fundamentos.ler(info.ticker, info.nome, info.classe,
                                  info.moeda)


def provedor_fundamentos(info: InfoBasica, ctx: ContextoInvestidor, *,
                         leitor=None) -> Secao:
    """Indicadores da classe do ativo, com ``Dado não disponível.`` onde a
    fonte não tem o número. ``leitor(info) -> Fundamentos`` é injetável."""
    from core.inteligencia_ativos import fundamentos as f
    fund = (leitor or _ler_fundamentos)(info)
    return Secao(chave="fundamentos", titulo=SECOES["fundamentos"][0],
                 estado=DISPONIVEL if fund.disponiveis else SEM_DADOS,
                 resumo=f.resumo(fund), dados=fund.como_dict(),
                 fonte=", ".join(fund.fontes) or None)


def _ler_valuation_e_pares(info: InfoBasica):
    from core.inteligencia_ativos import fontes_valuation
    return fontes_valuation.ler(info.ticker, info.nome, info.classe,
                                info.moeda)


def provedor_valuation(info: InfoBasica, ctx: ContextoInvestidor, *,
                       leitor=None) -> Secao:
    """Múltiplos da classe contra o próprio histórico e a mediana dos pares,
    com dado e interpretação separados. ``leitor(info) -> (Valuation,
    ComparacaoPares)`` é injetável."""
    from core.inteligencia_ativos import valuation as v
    val, _ = (leitor or _ler_valuation_e_pares)(info)
    return Secao(chave="valuation", titulo=SECOES["valuation"][0],
                 estado=DISPONIVEL if val.com_dado else SEM_DADOS,
                 resumo=v.resumo(val), dados=val.como_dict(),
                 fonte=", ".join(val.fontes) or None)


def provedor_pares(info: InfoBasica, ctx: ContextoInvestidor, *,
                   leitor=None) -> Secao:
    """Grupo de pares escolhido por regra (classe, mercado, modelo de negócio,
    porte, risco) e a tabela Ativo | Métrica | Valor | Mediana | Diferença.
    ``dados`` é o insumo estruturado para o Portfolio Fit."""
    from core.inteligencia_ativos import pares as p
    _, comp = (leitor or _ler_valuation_e_pares)(info)
    return Secao(chave="pares", titulo=SECOES["pares"][0],
                 estado=DISPONIVEL if comp.com_dado else SEM_DADOS,
                 resumo=p.resumo(comp), dados=comp.como_dict(),
                 fonte="Universo da mesma classe e mercado nas vitrines do "
                       "projeto" if comp.com_dado else None)


def _ler_informacoes(info: InfoBasica):
    from core.inteligencia_ativos import fontes_informacoes
    return fontes_informacoes.ler(info.ticker, info.nome, info.classe,
                                  info.moeda)


def provedor_noticias(info: InfoBasica, ctx: ContextoInvestidor, *,
                      leitor=None) -> Secao:
    """Manchetes em que o ativo é o assunto, com nível de impacto e
    dimensões. ``leitor(info) -> (Noticias, Relatorios, Eventos)``."""
    from core.inteligencia_ativos import informacoes as inf
    n, _, _ = (leitor or _ler_informacoes)(info)
    return Secao(chave="noticias", titulo=SECOES["noticias"][0],
                 estado=DISPONIVEL if n.itens else SEM_DADOS,
                 resumo=inf.resumo_noticias(n), dados=n.como_dict(),
                 fonte=n.fonte if n.itens else None)


def provedor_relatorios(info: InfoBasica, ctx: ContextoInvestidor, *,
                        leitor=None) -> Secao:
    """Documentos oficiais recentes (metadados) e os indícios, pelo título,
    para as sete perguntas de extração."""
    from core.inteligencia_ativos import informacoes as inf
    _, r, _ = (leitor or _ler_informacoes)(info)
    return Secao(chave="relatorios", titulo=SECOES["relatorios"][0],
                 estado=DISPONIVEL if r.documentos else SEM_DADOS,
                 resumo=inf.resumo_relatorios(r), dados=r.como_dict(),
                 fonte=r.fonte if r.documentos else None)


def provedor_eventos(info: InfoBasica, ctx: ContextoInvestidor, *,
                     leitor=None) -> Secao:
    """Linha do tempo à frente: só eventos com data de fonte."""
    from core.inteligencia_ativos import informacoes as inf
    _, _, e = (leitor or _ler_informacoes)(info)
    fontes = sorted({x.source for x in e.itens if x.source})
    return Secao(chave="eventos", titulo=SECOES["eventos"][0],
                 estado=DISPONIVEL if e.itens else SEM_DADOS,
                 resumo=inf.resumo_eventos(e), dados=e.como_dict(),
                 fonte=", ".join(fontes) or None)


def provedor_cenario(info: InfoBasica, ctx: ContextoInvestidor, *,
                     hoje: dt.date | None = None) -> Secao:
    """O cenário do usuário como premissa. Não lê banco: vem no ``ctx``.

    Não mede impacto no ativo: isso é leitura, e fica com a LLM, que recebe
    o cenário ao lado dos fundamentos e da estratégia.
    """
    from core.cenario import modelo as cen

    titulo = SECOES["cenario"][0]
    c = ctx.cenario
    if c is None or c.vazio:
        return Secao(chave="cenario", titulo=titulo, estado=SEM_DADOS,
                     resumo="Nenhum Cenário de Investimentos cadastrado. "
                            "Cadastre em \"Meu cenário\", no fim desta "
                            "aba.")
    hoje = hoje or dt.date.today()
    sinais = ctx.sinais_cenario
    relev = [k for k in cen.relevantes(info.classe_politica)
             if c.item(k).preenchido]
    resumo = (f"Premissa do usuário, versão {c.versao}: "
              f"{len(c.preenchidos)} de {len(cen.CHAVES)} itens. ")
    if relev:
        resumo += ("Mais relevantes para esta classe: "
                   + "; ".join(f"{cen.ROTULO[k]} {c.item(k).current_value}"
                               for k in relev) + ".")
    else:
        resumo += ("Nenhum dos itens mais relevantes para esta classe foi "
                   "preenchido.")
    velhos = c.envelhecidos(hoje)
    if velhos:
        resumo += (f" Revistos há mais de {cen.ENVELHECE_DIAS} dias: "
                   + ", ".join(cen.ROTULO[k] for k in velhos) + ".")
    if sinais:
        resumo += " " + cen.FRASE_REVISAO
    return Secao(chave="cenario", titulo=titulo, estado=DISPONIVEL,
                 resumo=resumo,
                 dados=cen.para_contexto(c, hoje=hoje,
                                         classe_politica=info.classe_politica,
                                         sinais=sinais) or {},
                 fonte=f"Cenário de Investimentos do usuário (versão "
                       f"{c.versao})")


PROVEDORES: dict[str, Provedor] = {c: _pendente(c) for c in SECOES}
PROVEDORES["cenario"] = provedor_cenario
PROVEDORES["fundamentos"] = provedor_fundamentos
PROVEDORES["valuation"] = provedor_valuation
PROVEDORES["pares"] = provedor_pares
PROVEDORES["noticias"] = provedor_noticias
PROVEDORES["relatorios"] = provedor_relatorios
PROVEDORES["eventos"] = provedor_eventos


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
