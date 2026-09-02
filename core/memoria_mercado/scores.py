"""Dois scores, duas escalas, e um teto de ação que não inclui vender.

O requisito separa o que costuma ficar somado:

**Score Estrutural** (0 a 100) -- fundamentos, valuation, qualidade, vantagem
competitiva, risco de longo prazo. É ele que forma a carteira. Ele muda quando o
negócio muda, não quando a manchete muda.

**Score Conjuntural** (−100 a +100) -- notícias, ambiente macro, eventos
históricos comparáveis, riscos e oportunidades temporárias. Ele **não** forma
carteira. Ele mexe em prioridade de aporte, coloca em observação, suspende
aporte novo, pede reavaliação fundamentalista e libera compra gradual em queda
sem deterioração.

As escalas são diferentes de propósito
--------------------------------------
Um vai de 0 a 100 e o outro de −100 a +100. Não é estética: é para que somar os
dois seja obviamente errado ao olhar. Score conjuntural é **desvio**, não nota.
Se as duas escalas fossem 0-100, mais cedo ou mais tarde alguém escreveria
``0.7 * estrutural + 0.3 * conjuntural`` e a carteira passaria a ser formada por
notícia -- que é exatamente o que o requisito proíbe.

O teto de ação
--------------
:data:`ACOES` tem sete itens e nenhum deles vende, zera, reduz posição ou emite
ordem. O item mais severo é :data:`SUSPENDER_APORTE`, que impede **dinheiro
novo** e deixa o que já está comprado onde está. Existe um teste dedicado a essa
invariante, porque ela é o tipo de garantia que se perde numa refatoração
distraída seis meses depois.

O que sobra para o humano
-------------------------
:data:`REAVALIAR_FUNDAMENTOS` é a saída deliberada para o caso em que a
conjuntura sugere que a tese estrutural pode ter mudado. O módulo não altera o
score estrutural por conta própria -- ele pede que alguém olhe. Mudar fundamento
por notícia seria deixar a manchete formar carteira pela porta dos fundos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from core.memoria_mercado.estimativa import Estimativa
from core.noticias.impacto import (
    CONFIANCA_ALTA,
    CONFIANCA_BAIXA,
    CONFIANCA_MEDIA,
)
from core.noticias.taxonomia import DIRECAO_ALTA, DIRECAO_BAIXA

# ── ações permitidas ─────────────────────────────────────────────────────────
MANTER = "manter"
PRIORIZAR_APORTE = "priorizar_aporte"
REDUZIR_PRIORIDADE_APORTE = "reduzir_prioridade_aporte"
OBSERVAR = "observar"
SUSPENDER_APORTE = "suspender_aporte"
REAVALIAR_FUNDAMENTOS = "reavaliar_fundamentos"
OPORTUNIDADE_GRADUAL = "oportunidade_gradual"

ACOES = (MANTER, PRIORIZAR_APORTE, REDUZIR_PRIORIDADE_APORTE, OBSERVAR,
         SUSPENDER_APORTE, REAVALIAR_FUNDAMENTOS, OPORTUNIDADE_GRADUAL)

#: Vazio, e é a documentação executável da regra "não liquidar automaticamente".
#: Qualquer ação futura que reduza posição teria de entrar aqui, e o teste que lê
#: este conjunto falharia -- que é o ponto.
ACOES_QUE_REDUZEM_POSICAO: frozenset = frozenset()

#: Ações que bloqueiam dinheiro NOVO. Bloquear aporte não é vender.
ACOES_QUE_BLOQUEIAM_APORTE = frozenset({SUSPENDER_APORTE})

# ── limiares do score conjuntural ────────────────────────────────────────────
#: Abaixo de -60: suspende aporte novo. Acima de +40 com estrutura sadia:
#: oportunidade gradual. Entre -25 e +25: ruído, mantém.
LIMITE_SUSPENDER = -60.0
LIMITE_REDUZIR = -25.0
LIMITE_PRIORIZAR = 25.0
LIMITE_OPORTUNIDADE = 40.0

#: Piso de score estrutural para que uma queda possa ser lida como oportunidade.
#: Sem isso, "caiu muito" viraria motivo de compra em empresa ruim -- que é como
#: se compra armadilha de valor.
PISO_ESTRUTURAL_OPORTUNIDADE = 60.0

#: Queda mínima (fração, negativa) para caracterizar "houve queda".
QUEDA_MINIMA_OPORTUNIDADE = -0.10

#: Multiplicador de prioridade de aporte. Nunca zero e nunca ilimitado: o
#: bloqueio é expresso por ``bloqueia_aporte``, não por prioridade zero, para que
#: as duas coisas continuem distinguíveis na tela e no log.
PRIORIDADE_MINIMA, PRIORIDADE_MAXIMA = 0.50, 1.50

#: Pesos-prior dos componentes do score conjuntural. Somam 1,00 e são priores
#: declarados: ``core.memoria_mercado.calibracao`` existe para substituí-los por
#: evidência, e ``ScoreConjuntural.calibrado`` diz qual dos dois está em uso.
PESOS_CONJUNTURAIS_PRIOR: dict[str, float] = {
    "noticias": 0.35,
    "memoria_mercado": 0.30,
    "macro": 0.20,
    "tecnico": 0.15,
}

#: Pesos-prior dos componentes do score estrutural. Idem.
PESOS_ESTRUTURAIS_PRIOR: dict[str, float] = {
    "fundamentos": 0.30,
    "valuation": 0.25,
    "qualidade": 0.20,
    "vantagem_competitiva": 0.15,
    "risco_longo_prazo": 0.10,
}

#: Impacto central (em fração) que satura a escala do componente. 10% é grande o
#: bastante para ser raro e pequeno o bastante para não precisar de cauda.
ESCALA_IMPACTO = 0.10


#: Abaixo desta cobertura o score é publicado mas não sustenta decisão.
COBERTURA_MINIMA = 0.50


def _num(valor) -> float | None:
    if valor is None:
        return None
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return None
    return f if isfinite(f) else None


def _renormalizar(componentes: dict, pesos: dict) -> tuple[float | None, float, tuple[str, ...]]:
    """Média ponderada sobre o peso MEDIDO. Ausente sai do denominador.

    A regra do repositório, com o motivo de sempre: ``None`` é não medido e
    ``0.0`` é medido-neutro. Tratar o primeiro como o segundo pune quem tem pior
    cobertura de dados -- ``memoria: medicao-que-pune-a-evidencia``.
    """
    peso_total = sum(float(p) for p in pesos.values())
    soma = 0.0
    peso_medido = 0.0
    ausentes: list[str] = []
    for chave, peso in pesos.items():
        valor = _num(componentes.get(chave))
        if valor is None:
            ausentes.append(chave)
            continue
        soma += valor * float(peso)
        peso_medido += float(peso)
    cobertura = (peso_medido / peso_total) if peso_total > 0 else 0.0
    valor = (soma / peso_medido) if peso_medido > 0 else None
    return valor, cobertura, tuple(ausentes)


@dataclass(frozen=True)
class ScoreEstrutural:
    """Nota de 0 a 100 sobre o negócio. Forma a carteira."""

    valor: float | None
    cobertura: float
    componentes: dict = field(default_factory=dict)
    ausentes: tuple[str, ...] = ()
    calibrado: bool = False
    fonte: str | None = None
    limitacoes: tuple[str, ...] = ()

    @property
    def utilizavel(self) -> bool:
        return self.valor is not None and self.cobertura >= COBERTURA_MINIMA


@dataclass(frozen=True)
class ScoreConjuntural:
    """Desvio de −100 a +100 sobre o momento. NÃO forma carteira."""

    valor: float | None
    cobertura: float
    componentes: dict = field(default_factory=dict)
    ausentes: tuple[str, ...] = ()
    confianca: str = CONFIANCA_BAIXA
    experimental: bool = True
    calibrado: bool = False
    limitacoes: tuple[str, ...] = ()

    @property
    def utilizavel(self) -> bool:
        return self.valor is not None and self.cobertura >= COBERTURA_MINIMA


@dataclass(frozen=True)
class Decisao:
    """O que a conjuntura autoriza fazer -- e o que ela não autoriza.

    ``acoes`` é sempre um subconjunto de :data:`ACOES`. ``fator_prioridade``
    multiplica o déficit do ativo em :func:`core.aporte.plano_de_aporte`;
    ``bloqueia_aporte`` o retira da distribuição de dinheiro novo.
    """

    simbolo: str | None
    acoes: tuple[str, ...]
    fator_prioridade: float
    bloqueia_aporte: bool
    motivo: str
    score_estrutural: float | None
    score_conjuntural: float | None
    confianca: str
    limitacoes: tuple[str, ...] = ()

    @property
    def altera_posicao_existente(self) -> bool:
        """Sempre ``False``. Ver :data:`ACOES_QUE_REDUZEM_POSICAO`."""
        return bool(set(self.acoes) & ACOES_QUE_REDUZEM_POSICAO)

    def texto(self) -> str:
        return f"{self.motivo} -> {', '.join(self.acoes)}"


def estrutural(componentes: dict, *, pesos: dict | None = None,
               calibrado: bool = False, fonte: str | None = None
               ) -> ScoreEstrutural:
    """Consolida os componentes fundamentais numa nota de 0 a 100.

    Este módulo **não** calcula fundamentos: os motores de score de B3, FII e
    EUA já existem no repositório e continuam sendo a fonte. O que entra aqui
    são os componentes já normalizados em 0-100, e o que sai é a nota única com
    a cobertura ao lado.
    """
    pesos = dict(pesos or PESOS_ESTRUTURAIS_PRIOR)
    valor, cobertura, ausentes = _renormalizar(dict(componentes or {}), pesos)

    limitacoes: list[str] = []
    if ausentes:
        limitacoes.append(
            f"componentes estruturais nao medidos: {', '.join(sorted(ausentes))}")
    if valor is None:
        limitacoes.append(
            "nenhum componente estrutural medido: score nao calculado")
    elif cobertura < COBERTURA_MINIMA:
        limitacoes.append(
            f"cobertura de {cobertura * 100:.0f}% dos componentes, abaixo do "
            f"minimo de {COBERTURA_MINIMA * 100:.0f}%")
    if not calibrado:
        limitacoes.append(
            "pesos estruturais ainda sao os priores declarados, nao calibrados")

    return ScoreEstrutural(
        valor=(round(max(0.0, min(100.0, valor)), 2) if valor is not None else None),
        cobertura=round(cobertura, 4),
        componentes=dict(componentes or {}),
        ausentes=ausentes,
        calibrado=calibrado,
        fonte=fonte,
        limitacoes=tuple(limitacoes),
    )


def componente_de_estimativa(est: Estimativa | None) -> float | None:
    """Converte a faixa da Memória de Mercado num componente de −100 a +100.

    Regras, e o motivo de cada uma:

    * estimativa não publicável devolve ``None`` -- amostra rala não vira
      componente neutro, ela sai do denominador;
    * estimativa com condição invalidante devolve ``None`` pelo mesmo motivo;
    * o valor central em fração é convertido a pontos usando
      :data:`ESCALA_IMPACTO`: um impacto central de 10% satura a escala. Saturar
      evita que um único evento extremo domine um score que também representa
      macro e notícia;
    * o sinal é invertido: impacto esperado **negativo** vira score conjuntural
      negativo (piorou o momento). Isso é o oposto da leitura contrária "caiu,
      logo está barato", que é tratada separadamente em :func:`avaliar` e exige
      fundamentos verificadamente estáveis.
    """
    if est is None or not est.publicavel or est.condicoes_invalidam:
        return None
    if est.valor_central is None:
        return None
    bruto = 100.0 * est.valor_central / ESCALA_IMPACTO
    peso_confianca = {CONFIANCA_ALTA: 1.0, CONFIANCA_MEDIA: 0.70,
                      CONFIANCA_BAIXA: 0.40}.get(est.confianca, 0.40)
    return max(-100.0, min(100.0, bruto * peso_confianca))


def conjuntural(componentes: dict, *, pesos: dict | None = None,
                calibrado: bool = False, experimental: bool = True,
                confianca: str = CONFIANCA_BAIXA) -> ScoreConjuntural:
    """Consolida notícias, memória de mercado, macro e técnico em −100 a +100."""
    pesos = dict(pesos or PESOS_CONJUNTURAIS_PRIOR)
    valor, cobertura, ausentes = _renormalizar(dict(componentes or {}), pesos)

    limitacoes: list[str] = []
    if ausentes:
        limitacoes.append(
            f"componentes conjunturais nao medidos: {', '.join(sorted(ausentes))}")
    if valor is None:
        limitacoes.append(
            "nenhum componente conjuntural medido: score nao calculado, "
            "carteira segue apenas pelo score estrutural")
    elif cobertura < COBERTURA_MINIMA:
        limitacoes.append(
            f"cobertura de {cobertura * 100:.0f}% dos componentes conjunturais, "
            f"abaixo do minimo de {COBERTURA_MINIMA * 100:.0f}%: score "
            "publicado como indicativo, sem alterar prioridade de aporte")
    if not calibrado:
        limitacoes.append(
            "pesos conjunturais ainda sao os priores declarados, nao calibrados")
    if experimental:
        limitacoes.append(
            "score conjuntural marcado como experimental: base historica ainda "
            "abaixo do piso de robustez")

    return ScoreConjuntural(
        valor=(round(max(-100.0, min(100.0, valor)), 2)
               if valor is not None else None),
        cobertura=round(cobertura, 4),
        componentes=dict(componentes or {}),
        ausentes=ausentes,
        confianca=confianca,
        experimental=experimental,
        calibrado=calibrado,
        limitacoes=tuple(limitacoes),
    )


def avaliar(estrut: ScoreEstrutural, conj: ScoreConjuntural, *,
            simbolo: str | None = None,
            estimativa: Estimativa | None = None,
            queda_recente: float | None = None,
            fundamentos_deteriorados: bool | None = None) -> Decisao:
    """Traduz os dois scores numa das ações permitidas. Nenhuma delas vende.

    ``fundamentos_deteriorados`` é ternário de propósito. ``None`` -- ninguém
    verificou -- **não** libera a leitura de oportunidade: sem verificação, uma
    queda é apenas uma queda, e a ação correta é observar. Aprovar no ``None``
    seria o defeito de ``memoria: fallback-nunca-contradiz``, aqui na sua forma
    mais cara, porque a compra na queda de uma tese que se deteriorou é como se
    perde dinheiro devagar.

    ``queda_recente`` é a variação já ocorrida no preço, em fração e negativa.
    """
    limitacoes = list(conj.limitacoes)
    valor = conj.valor

    if valor is None or not conj.utilizavel:
        motivo = ("conjuntura sem cobertura suficiente para alterar prioridade "
                  "de aporte")
        return Decisao(simbolo=simbolo, acoes=(MANTER,), fator_prioridade=1.0,
                       bloqueia_aporte=False, motivo=motivo,
                       score_estrutural=estrut.valor, score_conjuntural=valor,
                       confianca=CONFIANCA_BAIXA, limitacoes=tuple(limitacoes))

    if estimativa is not None and estimativa.condicoes_invalidam:
        limitacoes.extend(estimativa.condicoes_invalidam)

    acoes: list[str] = []
    bloqueia = False

    if valor <= LIMITE_SUSPENDER:
        acoes.extend([SUSPENDER_APORTE, OBSERVAR, REAVALIAR_FUNDAMENTOS])
        bloqueia = True
        motivo = (f"score conjuntural {valor:+.0f} abaixo de "
                  f"{LIMITE_SUSPENDER:+.0f}: aporte NOVO suspenso "
                  "temporariamente; posicao existente inalterada")
    elif valor <= LIMITE_REDUZIR:
        acoes.extend([REDUZIR_PRIORIDADE_APORTE, OBSERVAR])
        motivo = (f"score conjuntural {valor:+.0f}: prioridade de aporte "
                  "reduzida e ativo em observacao")
    elif valor >= LIMITE_OPORTUNIDADE:
        queda = _num(queda_recente)
        estrutura_ok = (estrut.utilizavel
                        and estrut.valor is not None
                        and estrut.valor >= PISO_ESTRUTURAL_OPORTUNIDADE)
        caiu = queda is not None and queda <= QUEDA_MINIMA_OPORTUNIDADE
        if caiu and estrutura_ok and fundamentos_deteriorados is False:
            acoes.append(OPORTUNIDADE_GRADUAL)
            motivo = (f"queda de {queda * 100:.1f}% com fundamentos "
                      f"verificados e estaveis (estrutural {estrut.valor:.0f}): "
                      "aporte gradual liberado, sem compra de uma vez")
        elif caiu and fundamentos_deteriorados is None:
            acoes.append(OBSERVAR)
            motivo = (f"queda de {queda * 100:.1f}% sem verificacao dos "
                      "fundamentos: ativo em observacao, sem aumento de "
                      "prioridade")
            limitacoes.append(
                "oportunidade gradual exige verificacao explicita de que os "
                "fundamentos nao se deterioraram; ela nao foi feita")
        else:
            acoes.append(PRIORIZAR_APORTE)
            motivo = (f"score conjuntural {valor:+.0f}: prioridade de aporte "
                      "aumentada")
    elif valor >= LIMITE_PRIORIZAR:
        acoes.append(PRIORIZAR_APORTE)
        motivo = (f"score conjuntural {valor:+.0f}: prioridade de aporte "
                  "levemente aumentada")
    else:
        acoes.append(MANTER)
        motivo = (f"score conjuntural {valor:+.0f} dentro da faixa de ruido "
                  f"({LIMITE_REDUZIR:+.0f} a {LIMITE_PRIORIZAR:+.0f}): "
                  "nenhuma alteracao")

    if (estimativa is not None and estimativa.publicavel
            and estimativa.direcao in (DIRECAO_ALTA, DIRECAO_BAIXA)
            and estimativa.condicoes_invalidam
            and REAVALIAR_FUNDAMENTOS not in acoes):
        # Direção definida mas comparação invalidada: não é motivo para agir,
        # é motivo para alguém olhar.
        acoes.append(OBSERVAR)

    # Prioridade proporcional ao score, saturada. O bloqueio NÃO vira prioridade
    # zero: são dois estados diferentes e devem continuar legíveis como tais.
    fator = 1.0 + (valor / 100.0) * 0.5
    fator = max(PRIORIDADE_MINIMA, min(PRIORIDADE_MAXIMA, fator))
    if bloqueia:
        fator = 1.0

    if conj.experimental:
        limitacoes.append(
            "decisao apoiada em score conjuntural experimental: trate como "
            "sinal para revisao humana, nao como conclusao")

    ilegais = set(acoes) - set(ACOES)
    if ilegais:  # pragma: no cover - defesa contra regressão
        raise ValueError(f"acao fora do conjunto permitido: {sorted(ilegais)}")

    return Decisao(
        simbolo=simbolo,
        acoes=tuple(dict.fromkeys(acoes)),
        fator_prioridade=round(fator, 4),
        bloqueia_aporte=bloqueia,
        motivo=motivo,
        score_estrutural=estrut.valor,
        score_conjuntural=valor,
        confianca=conj.confianca,
        limitacoes=tuple(dict.fromkeys(limitacoes)),
    )


def para_aporte(decisoes) -> tuple[dict, dict]:
    """Converte decisões em ``(bloqueios, prioridades)`` para ``core.aporte``.

    Duas estruturas separadas porque elas fazem coisas diferentes no plano de
    aporte: bloqueio retira o ativo da distribuição de dinheiro novo, prioridade
    apenas reordena quem recebe mais dentro de quem continua elegível. Nenhuma
    das duas produz venda.
    """
    bloqueios: dict[str, str] = {}
    prioridades: dict[str, float] = {}
    for d in decisoes or ():
        if not d.simbolo:
            continue
        if d.bloqueia_aporte:
            bloqueios[d.simbolo] = d.motivo
        elif d.fator_prioridade != 1.0:
            prioridades[d.simbolo] = d.fator_prioridade
    return bloqueios, prioridades
