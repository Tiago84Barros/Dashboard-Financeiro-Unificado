"""Impacto provável de uma notícia, com cada dimensão separada da outra.

O requisito é explícito: nada de "impacto de 72%". Setenta e dois por cento
do quê? Probabilidade de acontecer alguma coisa? Tamanho da queda? Confiança
de quem escreveu? As três leituras cabem na mesma frase e levam a decisões
diferentes, então elas viajam aqui em campos diferentes e são impressas com
nome e unidade:

* ``direcao``      -- alta, baixa, neutra ou indefinida.
* ``probabilidade``-- chance estimada de haver movimento **relevante**, onde
                      "relevante" é o limiar declarado da base histórica.
* ``faixa``        -- variação provável, em pontos percentuais, com piso e teto.
* ``horizonte``    -- em quanto tempo.
* ``confianca``    -- quanta fé se pode ter na análise acima.

E a regra que sustenta tudo: **número só aparece se houver base observada.**
Sem base histórica suficiente, ``probabilidade`` e ``faixa`` ficam ``None`` e a
limitação vai escrita junto. A alternativa -- estimar de qualquer jeito -- é
inventar número, que é a única coisa que o AGENTS.md deste repositório proíbe
sem exceção.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.noticias import taxonomia
from core.noticias.modelos import Sentimento

#: Abaixo disto a amostra não sustenta probabilidade nem faixa.
N_MINIMO_BASE = 30

#: Acima deste módulo o sentimento vira direção; abaixo, é ruído.
LIMIAR_DIRECAO = 0.15

CONFIANCA_BAIXA = "baixa"
CONFIANCA_MEDIA = "media"
CONFIANCA_ALTA = "alta"

# Tipos cuja direção não se lê do sentimento do texto: o fato em si já dita o
# sinal, e o texto costuma ser neutro ou até elogioso ("companhia conclui
# reestruturacao").
DIRECAO_FIXA: dict[str, str] = {
    "recuperacao_judicial": taxonomia.DIRECAO_BAIXA,
    "fraude_governanca": taxonomia.DIRECAO_BAIXA,
    "deslistagem": taxonomia.DIRECAO_BAIXA,
}


@dataclass(frozen=True)
class FaixaVariacao:
    """Faixa provável de variação, sempre com unidade explícita."""

    minimo: float
    maximo: float
    unidade: str = "%"

    def texto(self) -> str:
        return (f"de {self.minimo:+.1f}{self.unidade} a "
                f"{self.maximo:+.1f}{self.unidade}")


@dataclass(frozen=True)
class BaseHistorica:
    """Estatística observada para um tipo de evento.

    Não é calculada aqui. Este módulo consome o que o backtest tiver produzido
    e recusa o que for pequeno demais. Enquanto nenhuma base existir, o motor
    opera sem números de magnitude -- e diz isso na cara.
    """

    tipo_evento: str
    n_observacoes: int
    limiar_relevante: float
    horizonte: str
    prob_movimento_relevante: float | None = None
    p10: float | None = None
    p90: float | None = None
    fonte: str | None = None
    janela: str | None = None

    @property
    def suficiente(self) -> bool:
        return (self.n_observacoes >= N_MINIMO_BASE
                and self.prob_movimento_relevante is not None)


@dataclass(frozen=True)
class Impacto:
    """As cinco dimensões, separadas. Nenhuma delas sozinha é "o impacto"."""

    direcao: str = taxonomia.DIRECAO_INDEFINIDA
    probabilidade: float | None = None
    faixa: FaixaVariacao | None = None
    horizonte: str = taxonomia.HORIZONTE_INDETERMINADO
    confianca: float | None = None
    limiar_relevante: float | None = None
    n_observacoes: int | None = None
    fonte_base: str | None = None
    limitacoes: tuple[str, ...] = ()

    @property
    def tem_base_estatistica(self) -> bool:
        return self.probabilidade is not None

    @property
    def grau_confianca(self) -> str:
        if self.confianca is None:
            return CONFIANCA_BAIXA
        if self.confianca >= 0.70:
            return CONFIANCA_ALTA
        if self.confianca >= 0.40:
            return CONFIANCA_MEDIA
        return CONFIANCA_BAIXA

    @property
    def rotulo_direcao(self) -> str:
        return {
            taxonomia.DIRECAO_ALTA: "alta",
            taxonomia.DIRECAO_BAIXA: "baixa",
            taxonomia.DIRECAO_NEUTRA: "neutra",
        }.get(self.direcao, "indefinida")

    def texto(self) -> str:
        """Frase sem ambiguidade. Cada número sai com o nome do que ele mede."""
        partes = [f"Direcao provavel: {self.rotulo_direcao}"]
        if self.probabilidade is None:
            partes.append(
                "sem base estatistica suficiente para estimar probabilidade "
                "ou magnitude")
        else:
            limiar = self.limiar_relevante or 0.0
            partes.append(
                f"probabilidade estimada de variacao acima de {limiar:.1f}% "
                f"no horizonte {self.horizonte}: {self.probabilidade * 100:.0f}%"
            )
            if self.faixa is not None:
                partes.append(f"faixa provavel {self.faixa.texto()}")
            if self.n_observacoes:
                partes.append(f"base de {self.n_observacoes} eventos comparaveis")
        partes.append(f"grau de confianca da analise: {self.grau_confianca}")
        return "; ".join(partes) + "."


def _direcao(tipo_evento: str, sentimento: Sentimento | None) -> str:
    fixa = DIRECAO_FIXA.get(tipo_evento)
    if fixa:
        return fixa
    valor = sentimento.valor if sentimento is not None else None
    if valor is None:
        return taxonomia.DIRECAO_INDEFINIDA
    if valor >= LIMIAR_DIRECAO:
        return taxonomia.DIRECAO_ALTA
    if valor <= -LIMIAR_DIRECAO:
        return taxonomia.DIRECAO_BAIXA
    return taxonomia.DIRECAO_NEUTRA


def _confianca(
    *,
    sentimento: Sentimento | None,
    confiabilidade_fonte: float | None,
    estado_verificacao: str,
    cobertura_relevancia: float | None,
    base: BaseHistorica | None,
) -> float | None:
    """Confiança da análise, como média dos sinais efetivamente medidos.

    Mesma disciplina da relevância: sinal ausente fica de fora da média em vez
    de entrar como zero. Divergência entre o sentimento da API e o do APP4
    derruba a confiança -- é o caso em que duas leituras independentes do mesmo
    texto discordam, e esconder isso seria o pior dos mundos.
    """
    sinais: list[float] = []

    if sentimento is not None:
        concordam = sentimento.concordam
        if concordam is True:
            sinais.append(0.9)
        elif concordam is False:
            sinais.append(0.25)

    if confiabilidade_fonte is not None:
        sinais.append(confiabilidade_fonte)

    if estado_verificacao == taxonomia.VERIF_FONTE_PRIMARIA:
        sinais.append(1.0)
    elif estado_verificacao == taxonomia.VERIF_INDEPENDENTE:
        sinais.append(0.75)
    elif estado_verificacao == taxonomia.VERIF_CONTESTADA:
        sinais.append(0.10)
    elif estado_verificacao == taxonomia.VERIF_NAO_VERIFICADA:
        sinais.append(0.35)

    if cobertura_relevancia is not None:
        sinais.append(max(0.0, min(1.0, cobertura_relevancia)))

    if base is not None and base.suficiente:
        # Amostra maior sustenta mais, com teto: 500 eventos não fazem de uma
        # leitura qualitativa uma previsão.
        sinais.append(min(1.0, base.n_observacoes / 200.0))

    if not sinais:
        return None
    return round(sum(sinais) / len(sinais), 4)


def estimar(
    *,
    tipo_evento: str,
    sentimento: Sentimento | None = None,
    confiabilidade_fonte: float | None = None,
    estado_verificacao: str = taxonomia.VERIF_NAO_VERIFICADA,
    cobertura_relevancia: float | None = None,
    base: BaseHistorica | None = None,
) -> Impacto:
    """Monta o impacto provável a partir do que existe medido.

    ``base`` é a única porta por onde probabilidade e faixa entram. Sem ela, o
    resultado ainda é útil -- direção e confiança são qualitativas e honestas --
    mas nenhum percentual de movimento é publicado.
    """
    tipo = taxonomia.tipo(tipo_evento)
    limitacoes: list[str] = []

    direcao = _direcao(tipo_evento, sentimento)
    if direcao == taxonomia.DIRECAO_INDEFINIDA:
        limitacoes.append("sentimento nao pode ser medido: direcao indefinida")

    probabilidade = None
    faixa = None
    limiar = None
    n_obs = None
    fonte_base = None
    horizonte = tipo.horizonte

    if base is None:
        limitacoes.append(
            "sem base historica para este tipo de evento: probabilidade e "
            "faixa de variacao nao estimadas")
    elif not base.suficiente:
        n_obs = base.n_observacoes
        limitacoes.append(
            f"base historica insuficiente ({base.n_observacoes} observacoes, "
            f"minimo de {N_MINIMO_BASE}): probabilidade e faixa nao estimadas")
    else:
        probabilidade = max(0.0, min(1.0, float(base.prob_movimento_relevante)))
        limiar = base.limiar_relevante
        n_obs = base.n_observacoes
        fonte_base = base.fonte
        horizonte = base.horizonte or horizonte
        if base.p10 is not None and base.p90 is not None:
            faixa = FaixaVariacao(minimo=float(base.p10), maximo=float(base.p90))
        else:
            limitacoes.append(
                "base sem percentis: faixa de variacao nao publicada")

    confianca = _confianca(
        sentimento=sentimento,
        confiabilidade_fonte=confiabilidade_fonte,
        estado_verificacao=estado_verificacao,
        cobertura_relevancia=cobertura_relevancia,
        base=base,
    )
    if confianca is None:
        limitacoes.append("nenhum sinal de confianca pode ser medido")

    return Impacto(
        direcao=direcao,
        probabilidade=probabilidade,
        faixa=faixa,
        horizonte=horizonte,
        confianca=confianca,
        limiar_relevante=limiar,
        n_observacoes=n_obs,
        fonte_base=fonte_base,
        limitacoes=tuple(limitacoes),
    )
