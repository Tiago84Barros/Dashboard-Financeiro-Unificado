"""
core/inteligencia_ativos/papeis.py
Papel de um ativo na carteira: para que ele serve.

Hoje o papel é inferido da classe, do setor e (no Tesouro) do indexador que
aparece no nome. Cada ``Papel`` diz de onde veio (``fonte``) e por quê
(``motivo``): é uma hipótese de partida, não uma verdade sobre o ativo. As
próximas etapas podem confirmá-la com fundamentos (o dividend yield de fato
de uma ação "de dividendos") ou o usuário pode corrigi-la
(``papeis_do_usuario``), e o que ele disser vence.

O papel descreve o ATIVO. Se ele serve a ESTE investidor é outra pergunta,
respondida em ``adequacao.py``.
"""
from __future__ import annotations

from core.inteligencia_ativos.modelos import PAPEIS, InfoBasica, Papel

# Setores cujas empresas listadas no Brasil costumam distribuir a maior parte
# do lucro. Heurística de partida: a confirmação é o dividend yield, que chega
# com a etapa de fundamentos.
_SETORES_DIVIDENDOS = {"Financeiro", "Utilidades", "Energia", "Telecom"}
_SETORES_DEFENSIVOS = {"Utilidades", "Energia", "Consumo Básico", "Saúde",
                       "Telecom"}


def _tesouro(info: InfoBasica) -> list[tuple[str, str, str]]:
    nome = info.nome.upper()
    if "IPCA" in nome or "NTN-B" in nome or "RENDA+" in nome or "EDUCA" in nome:
        return [("inflation_protection", "título atrelado ao IPCA", "nome"),
                ("fixed_income_core", "título público", "classe")]
    if "SELIC" in nome or "LFT" in nome:
        return [("liquidity", "título pós-fixado na Selic, de baixa "
                              "oscilação", "nome"),
                ("capital_preservation", "título público pós-fixado", "nome")]
    return [("fixed_income_core", "título público", "classe"),
            ("capital_preservation", "renda fixa soberana", "classe")]


def inferir(info: InfoBasica) -> tuple[Papel, ...]:
    """Papéis inferidos, o primeiro é o principal. Puro."""
    classe = info.classe
    setor = info.setor or ""
    brutos: list[tuple[str, str, str]]

    if classe == "Tesouro Direto":
        brutos = _tesouro(info)
    elif classe in ("Renda Fixa", "Fundo RF"):
        brutos = [("fixed_income_core", "renda fixa", "classe"),
                  ("capital_preservation", "renda fixa", "classe")]
    elif classe == "FII":
        brutos = [("real_estate_income", "fundo imobiliário, que distribui "
                                         "rendimentos mensais", "classe"),
                  ("income", "distribuição mensal", "classe")]
    elif info.classe_politica == "exterior":
        brutos = [("international_diversification",
                   f"cotado em {info.moeda}" if info.moeda != "BRL"
                   else "exposição a ativo estrangeiro", "classe"),
                  ("growth", "renda variável", "classe")]
    elif classe == "Cripto":
        brutos = [("opportunity", "criptoativo, de alta volatilidade",
                   "classe")]
    elif classe in ("Ações BR", "ETF Brasil", "ETF"):
        if setor in _SETORES_DIVIDENDOS:
            brutos = [("dividend", f"setor {setor}, tradicionalmente pagador",
                       "setor")]
        else:
            brutos = [("growth", "renda variável", "classe")]
        if setor in _SETORES_DEFENSIVOS:
            brutos.append(("defensive", f"setor {setor}, de demanda estável",
                           "setor"))
        if setor and setor != "Outros":
            brutos.append(("sector_exposure", f"exposição a {setor}", "setor"))
    else:
        brutos = []

    vistos: set[str] = set()
    papeis = []
    for codigo, motivo, fonte in brutos:
        if codigo in vistos:
            continue
        vistos.add(codigo)
        papeis.append(Papel(codigo, principal=not papeis, motivo=motivo,
                            fonte=fonte))
    return tuple(papeis)


def papeis_do_usuario(codigos: list[str] | tuple[str, ...]) -> tuple[Papel, ...]:
    """Papéis declarados pelo usuário, na ordem dada. Substituem os inferidos.

    Ainda sem tela nem tabela: é o ponto de entrada para quando o usuário
    puder dizer "comprei isto para renda".
    """
    papeis = []
    for codigo in codigos:
        if codigo in PAPEIS and all(p.codigo != codigo for p in papeis):
            papeis.append(Papel(codigo, principal=not papeis,
                                motivo="definido por você", fonte="usuario"))
    return tuple(papeis)


def em_linguagem_natural(papeis: tuple[Papel, ...]) -> list[str]:
    """"Papel principal: geração de renda." / "Papel secundário: ..." """
    if not papeis:
        return ["Papel ainda não identificado para esta classe de ativo."]
    principal = [p for p in papeis if p.principal]
    secundarios = [p for p in papeis if not p.principal]
    frases = [f"Papel principal: {p.rotulo}." for p in principal]
    if secundarios:
        rotulo = "Papel secundário" if len(secundarios) == 1 else "Papéis secundários"
        frases.append(f"{rotulo}: "
                      f"{', '.join(p.rotulo for p in secundarios)}.")
    return frases
