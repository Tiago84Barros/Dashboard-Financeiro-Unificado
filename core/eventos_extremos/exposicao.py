"""Quanto deste evento chega nesta carteira.

Duas exposições, e elas respondem perguntas diferentes. A **direta** é o peso dos
ativos que o evento nomeia. A **indireta** é o peso dos ativos que compartilham
setor, país, moeda ou classe com ele -- contágio, não identidade, e por isso
entra descontado por um fator declarado em :data:`FATOR_CONTAGIO`.

O que este módulo se recusa a fazer
-----------------------------------
**Não devolve 0,0 quando não sabe.** Se o quadro de posições não traz a coluna
``country``, a exposição geográfica é ``None`` e a limitação viaja escrita. A
tentação de tratar coluna ausente como "exposição zero" é o defeito que este
projeto já registrou mais de uma vez: a regra fica certa, a entrada fica errada,
e o motor aprova com confiança. Aqui, ausência de dado nunca vira ausência de
risco.

**Não soma dimensões cegamente.** Um ativo que é do setor *e* do país do evento
não conta duas vezes: a indireta é o peso de cada ativo multiplicado pelo maior
fator de contágio que se aplica a ele. Somar dimensões produziria exposição
acima de 100% e a conclusão de que a carteira inteira está no epicentro.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from core.eventos_extremos import evidencias as ev

logger = logging.getLogger(__name__)

#: Quanto do peso de um ativo conta como exposição quando ele apenas
#: *compartilha* a dimensão com o evento. Setor contagia mais que moeda porque o
#: choque setorial atinge o negócio; o cambial atinge a conversão.
FATOR_CONTAGIO: dict[str, float] = {
    "sector": 0.60,
    "country": 0.45,
    "currency": 0.35,
    "asset_class": 0.25,
}

#: Coluna do quadro de posições que carrega o peso na carteira consolidada.
COLUNA_PESO = "weight_global"
COLUNA_SIMBOLO = "symbol"

#: Abaixo disto o quadro não sustenta conclusão nenhuma sobre exposição.
PESO_MINIMO_UTIL = 1e-9


@dataclass(frozen=True)
class Alvo:
    """O que o evento nomeia. Espelha ``core.noticias.modelos.Entidades``.

    Campos vazios significam "o evento não nomeia esta dimensão", que é
    diferente de "a carteira não tem esta dimensão". O primeiro não gera
    exposição; o segundo gera limitação.
    """

    tickers: frozenset[str] = field(default_factory=frozenset)
    setores: frozenset[str] = field(default_factory=frozenset)
    paises: frozenset[str] = field(default_factory=frozenset)
    moedas: frozenset[str] = field(default_factory=frozenset)
    classes: frozenset[str] = field(default_factory=frozenset)

    @staticmethod
    def de(*, tickers=(), setores=(), paises=(), moedas=(), classes=()) -> "Alvo":
        def norm(valores) -> frozenset[str]:
            return frozenset(
                str(v).strip().upper() for v in (valores or ()) if str(v).strip())
        return Alvo(norm(tickers), norm(setores), norm(paises), norm(moedas),
                    norm(classes))

    @property
    def vazio(self) -> bool:
        return not (self.tickers or self.setores or self.paises or self.moedas
                    or self.classes)

    def por_dimensao(self) -> dict[str, frozenset[str]]:
        return {"sector": self.setores, "country": self.paises,
                "currency": self.moedas, "asset_class": self.classes}


@dataclass(frozen=True)
class Exposicao:
    """Exposição medida, com o que não deu para medir dito em voz alta."""

    direta: float | None
    indireta: float | None
    por_dimensao: dict[str, float | None]
    ativos_diretos: tuple[str, ...]
    peso_total: float
    limitacoes: tuple[str, ...] = ()

    @property
    def total(self) -> float | None:
        """Direta + indireta, quando ao menos uma foi medida.

        Não é probabilidade nem perda: é fração do patrimônio alcançada pelo
        evento, direta e por contágio descontado.
        """
        partes = [p for p in (self.direta, self.indireta) if p is not None]
        return min(1.0, sum(partes)) if partes else None

    def descrever(self) -> tuple[str, ...]:
        linhas = []
        if self.direta is not None:
            nomes = ", ".join(self.ativos_diretos[:6]) or "nenhum ativo nomeado"
            linhas.append(f"exposição direta {self.direta:.1%} ({nomes})")
        else:
            linhas.append("exposição direta não medida")
        if self.indireta is not None:
            linhas.append(f"exposição indireta {self.indireta:.1%} (contágio)")
        else:
            linhas.append("exposição indireta não medida")
        for dim, valor in sorted(self.por_dimensao.items()):
            if valor is not None:
                linhas.append(f"  via {dim}: {valor:.1%}")
        linhas.extend(f"limitação: {lim}" for lim in self.limitacoes)
        return tuple(linhas)


def _coluna_utilizavel(df: pd.DataFrame, coluna: str) -> bool:
    """A coluna existe e tem ao menos um valor não nulo.

    Coluna presente e inteiramente nula é tão inútil quanto coluna ausente, e
    tratá-la como medida é o caminho para "0% de exposição geográfica" numa
    carteira cujo país nunca foi preenchido.
    """
    return coluna in df.columns and bool(df[coluna].notna().any())


def medir(posicoes: pd.DataFrame, alvo: Alvo) -> Exposicao:
    """Mede quanto do patrimônio o evento alcança.

    Args:
        posicoes: quadro no formato de ``core.global_portfolio.aggregate
            .montar_posicoes`` -- precisa ao menos de ``symbol`` e
            ``weight_global``.
        alvo: as entidades que o evento nomeia.

    Returns:
        Um :class:`Exposicao`. Nada aqui devolve zero por não saber: o que não
        pôde ser medido volta ``None`` e a limitação vai escrita.
    """
    limitacoes: list[str] = []

    if alvo.vazio:
        # Evento que não nomeia ativo, setor, país, moeda nem classe não tem
        # exposição zero: tem exposição desconhecida. Devolver 0,0 aqui faria a
        # regra `evento_nao_alcanca_a_carteira` do motor de transição concluir
        # que a carteira está a salvo de um evento que ninguém localizou.
        return Exposicao(None, None, {}, (), 0.0,
                         ("evento não nomeia ativo, setor, país, moeda nem "
                          "classe: exposição não é atribuível",))

    if posicoes is None or posicoes.empty:
        return Exposicao(None, None, {}, (), 0.0,
                         ("carteira sem posições: exposição não medida",))

    faltando = [c for c in (COLUNA_SIMBOLO, COLUNA_PESO)
                if c not in posicoes.columns]
    if faltando:
        # Quadro sem a coluna essencial é falha de leitura, e falha de leitura
        # precisa parecer falha -- não "exposição zero".
        logger.warning("quadro de posições sem colunas %s", faltando)
        return Exposicao(None, None, {}, (), 0.0,
                         (f"quadro de posições sem {', '.join(faltando)}: "
                          "exposição não medida",))

    pesos = pd.to_numeric(posicoes[COLUNA_PESO], errors="coerce").fillna(0.0)
    peso_total = float(pesos.sum())
    if peso_total <= PESO_MINIMO_UTIL:
        return Exposicao(None, None, {}, (), 0.0,
                         ("pesos da carteira somam zero: exposição não medida",))

    simbolos = posicoes[COLUNA_SIMBOLO].astype(str).str.strip().str.upper()

    # ── Direta ────────────────────────────────────────────────────────────────
    if alvo.tickers:
        direto = simbolos.isin(alvo.tickers)
        direta = float(pesos[direto].sum()) / peso_total
        ativos_diretos = tuple(sorted(simbolos[direto].unique()))
        if not ativos_diretos:
            limitacoes.append(
                "evento nomeia ativos que não estão nesta carteira")
    else:
        direto = pd.Series(False, index=posicoes.index)
        direta = 0.0
        ativos_diretos = ()

    # ── Indireta ──────────────────────────────────────────────────────────────
    # Cada ativo entra pelo MAIOR fator que se aplica a ele, uma vez só. Somar
    # dimensões daria exposição acima de 100% e um epicentro imaginário.
    fator_por_ativo = pd.Series(0.0, index=posicoes.index)
    por_dimensao: dict[str, float | None] = {}
    dimensoes_medidas = 0

    for coluna, alvos in alvo.por_dimensao().items():
        if not alvos:
            continue
        if not _coluna_utilizavel(posicoes, coluna):
            por_dimensao[coluna] = None
            limitacoes.append(
                f"coluna '{coluna}' ausente ou vazia: contágio por {coluna} "
                "não medido")
            continue

        dimensoes_medidas += 1
        valores = posicoes[coluna].astype(str).str.strip().str.upper()
        atinge = valores.isin(alvos) & ~direto
        fator = FATOR_CONTAGIO[coluna]
        fator_por_ativo = fator_por_ativo.where(
            ~atinge, fator_por_ativo.combine(
                pd.Series(fator, index=posicoes.index), max))
        por_dimensao[coluna] = float(pesos[atinge].sum()) / peso_total

    pediu_indireta = any(alvo.por_dimensao().values())
    if not pediu_indireta:
        indireta: float | None = 0.0
    elif dimensoes_medidas == 0:
        indireta = None
    else:
        indireta = float((pesos * fator_por_ativo).sum()) / peso_total

    return Exposicao(direta=direta, indireta=indireta,
                     por_dimensao=por_dimensao, ativos_diretos=ativos_diretos,
                     peso_total=peso_total, limitacoes=tuple(limitacoes))


def para_evidencia(
    exposicao: Exposicao,
    *,
    concentracao_hhi: float | None = None,
    liquidez_disponivel: float | None = None,
    risco_credito: float | None = None,
    risco_cambial: float | None = None,
    dependencia_geografica: float | None = None,
    perda_simulada: float | None = None,
    limitacoes: tuple[str, ...] = (),
) -> ev.Evidencia:
    """Converte a exposição medida na evidência de carteira.

    Os demais componentes chegam de fora (concentração de
    ``global_portfolio.concentration``, perda simulada de ``core.stress_tests``,
    liquidez de ``core.liquidez``) porque este módulo mede alcance, não risco --
    e misturar as duas coisas num só cálculo é o que faz uma nota esconder
    dentro de si um risco que ninguém pediu para ela carregar.
    """
    return ev.carteira(
        exposicao_direta=exposicao.direta,
        exposicao_indireta=exposicao.indireta,
        concentracao_hhi=concentracao_hhi,
        liquidez_disponivel=liquidez_disponivel,
        risco_credito=risco_credito,
        risco_cambial=risco_cambial,
        dependencia_geografica=dependencia_geografica,
        perda_simulada=perda_simulada,
        limitacoes=tuple(dict.fromkeys(exposicao.limitacoes + tuple(limitacoes))),
    )
