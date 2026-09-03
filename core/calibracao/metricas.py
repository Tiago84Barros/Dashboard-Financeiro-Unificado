"""As medidas que decidem se o motor sai do laboratório.

A instrução lista o que precisa ser medido e, no fim, lista as condições em que
**não** se coloca em produção. As duas listas são a mesma coisa vista de dois
lados, e este módulo produz os números das duas.

Uma escolha atravessa o módulo inteiro: nada aqui devolve uma nota única. Um
motor pode ter F1 alto e probabilidade completamente descalibrada; pode acertar
a direção e errar a magnitude por um fator de três; pode bater o "não agir" no
retorno bruto e perder depois de custo. Colapsar isso num número esconde
exatamente o que a decisão precisa ver -- é o mesmo defeito que o requisito do
Índice de Antifragilidade proíbe com todas as letras ("não esconder riscos
dentro de uma nota única").

Sobre "acertou"
---------------
Detecção é comparada contra *movimento relevante realizado*, e o que conta como
relevante vem de :mod:`core.calibracao.limiar` -- por classe e por volatilidade
do próprio ativo. Usar um limiar absoluto aqui recriaria, dentro da própria
métrica, o viés que o limiar por classe existe para remover: o motor pareceria
excelente em small cap volátil (onde qualquer coisa cruza 3%) e cego em FII.

Sobre o denominador
-------------------
Precisão e recall são medidos **na população em que o motor opera**, não na
população em que ele acerta. Este repositório já publicou uma taxa medida dentro
da amostra sobrevivente, que sempre daria o resultado bonito
(``memoria: vies-so-e-acionavel-com-tamanho``). Por isso
:func:`avaliar_deteccao` exige os quatro quadrantes e recusa entrada em que o
verdadeiro-negativo não foi contado.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

#: Fronteiras dos baldes de probabilidade. Dez baldes com poucas observações
#: cada produzem ruído com cara de descalibração; cinco é o compromisso usado
#: aqui, e o ``n`` de cada balde sai publicado para quem quiser desconfiar.
BALDES = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0001))

#: Mínimo de observações num balde para ele contar na calibração agregada. Um
#: balde com 3 casos tem frequência observada em passos de 33 pontos.
MINIMO_POR_BALDE = 20

#: Desvio tolerado entre probabilidade declarada e frequência observada. A
#: instrução define o alvo em palavras -- "se o sistema disser 70%, o impacto
#: precisa ocorrer em torno de 70% das vezes" -- e isto é o "em torno de".
TOLERANCIA_CALIBRACAO = 0.10


def _finitos(valores) -> list[float]:
    return [float(v) for v in (valores or ())
            if v is not None and isfinite(float(v))]


def _mediana(ordenados: list[float]) -> float:
    n = len(ordenados)
    meio = n // 2
    if n % 2:
        return ordenados[meio]
    return (ordenados[meio - 1] + ordenados[meio]) / 2.0


# ─────────────────────────────────────────────────────────────────────────────
# Detecção
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Confusao:
    """Matriz de confusão de "houve movimento relevante?"."""

    verdadeiro_positivo: int
    falso_positivo: int
    verdadeiro_negativo: int
    falso_negativo: int

    @property
    def total(self) -> int:
        return (self.verdadeiro_positivo + self.falso_positivo
                + self.verdadeiro_negativo + self.falso_negativo)

    @property
    def precisao(self) -> float | None:
        """Dos que o motor apontou, quantos eram. ``None`` se não apontou nada.

        ``None`` e não 1,0: um motor que nunca dispara não tem precisão
        perfeita, tem precisão indefinida. Retornar 1,0 aqui premiaria o
        silêncio, e o portão de promoção leria isso como excelência.
        """
        apontados = self.verdadeiro_positivo + self.falso_positivo
        if not apontados:
            return None
        return self.verdadeiro_positivo / apontados

    @property
    def recall(self) -> float | None:
        """Dos que houve, quantos o motor apontou."""
        houve = self.verdadeiro_positivo + self.falso_negativo
        if not houve:
            return None
        return self.verdadeiro_positivo / houve

    @property
    def f1(self) -> float | None:
        p, r = self.precisao, self.recall
        if p is None or r is None or (p + r) <= 0:
            return None
        return 2 * p * r / (p + r)

    @property
    def taxa_falso_alarme(self) -> float | None:
        """Falso positivo sobre tudo que não era. É o número do alarme excessivo."""
        nao_era = self.falso_positivo + self.verdadeiro_negativo
        if not nao_era:
            return None
        return self.falso_positivo / nao_era

    @property
    def taxa_nao_deteccao(self) -> float | None:
        """Complemento do recall. Publicado à parte porque é o custo assimétrico.

        Falso alarme cansa o usuário; crise não detectada o pega posicionado. Os
        dois erros não valem o mesmo, e uma métrica que os soma finge que sim.
        """
        r = self.recall
        return None if r is None else 1.0 - r

    def como_dict(self) -> dict:
        return {
            "verdadeiro_positivo": self.verdadeiro_positivo,
            "falso_positivo": self.falso_positivo,
            "verdadeiro_negativo": self.verdadeiro_negativo,
            "falso_negativo": self.falso_negativo,
            "total": self.total,
            "precisao": self.precisao,
            "recall": self.recall,
            "f1": self.f1,
            "taxa_falso_alarme": self.taxa_falso_alarme,
            "taxa_nao_deteccao": self.taxa_nao_deteccao,
        }


def avaliar_deteccao(casos) -> Confusao:
    """Matriz a partir de pares ``(apontou, ocorreu)``.

    Cada caso é uma dupla de booleanos. Casos com ``None`` em qualquer lado são
    descartados: não medido não entra em nenhum dos quatro quadrantes, e
    empurrá-lo para o negativo inflaria o verdadeiro-negativo -- que é o
    quadrante que carrega a precisão inteira.
    """
    vp = fp = vn = fn = 0
    for caso in casos or ():
        apontou, ocorreu = caso[0], caso[1]
        if apontou is None or ocorreu is None:
            continue
        if apontou and ocorreu:
            vp += 1
        elif apontou and not ocorreu:
            fp += 1
        elif not apontou and ocorreu:
            fn += 1
        else:
            vn += 1
    return Confusao(vp, fp, vn, fn)


# ─────────────────────────────────────────────────────────────────────────────
# Probabilidade
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Balde:
    inferior: float
    superior: float
    n: int
    prob_media: float | None
    frequencia: float | None

    @property
    def desvio(self) -> float | None:
        if self.prob_media is None or self.frequencia is None:
            return None
        return self.frequencia - self.prob_media

    @property
    def suficiente(self) -> bool:
        return self.n >= MINIMO_POR_BALDE


@dataclass(frozen=True)
class CalibracaoProb:
    """Quão perto a probabilidade declarada fica da frequência observada."""

    baldes: tuple[Balde, ...]
    brier: float | None
    erro_calibracao: float | None
    n: int

    @property
    def calibrada(self) -> bool | None:
        """``None`` quando nenhum balde tem observação suficiente.

        A lei do projeto: ``ok=None`` é "não medido", nunca ``False``. Um motor
        sem amostra não é um motor descalibrado -- é um motor não avaliado, e o
        portão de promoção precisa distinguir os dois.
        """
        if self.erro_calibracao is None:
            return None
        return self.erro_calibracao <= TOLERANCIA_CALIBRACAO

    def como_dict(self) -> dict:
        return {
            "n": self.n,
            "brier": self.brier,
            "erro_calibracao": self.erro_calibracao,
            "calibrada": self.calibrada,
            "baldes": [
                {"faixa": [b.inferior, b.superior], "n": b.n,
                 "prob_media": b.prob_media, "frequencia": b.frequencia,
                 "desvio": b.desvio, "suficiente": b.suficiente}
                for b in self.baldes
            ],
        }


def avaliar_probabilidade(pares) -> CalibracaoProb:
    """Confiabilidade a partir de ``(probabilidade_declarada, ocorreu)``.

    ``brier`` é o erro quadrático médio da probabilidade -- mede acurácia e
    calibração juntas. ``erro_calibracao`` é o ECE ponderado pelos baldes com
    observação suficiente, e é ele que responde à frase da instrução sobre os
    70%.
    """
    limpos = []
    for par in pares or ():
        p, ocorreu = par[0], par[1]
        if p is None or ocorreu is None:
            continue
        p = float(p)
        if not isfinite(p):
            continue
        limpos.append((min(1.0, max(0.0, p)), bool(ocorreu)))

    if not limpos:
        return CalibracaoProb(baldes=(), brier=None, erro_calibracao=None, n=0)

    brier = sum((p - (1.0 if o else 0.0)) ** 2 for p, o in limpos) / len(limpos)

    baldes: list[Balde] = []
    for inferior, superior in BALDES:
        dentro = [(p, o) for p, o in limpos if inferior <= p < superior]
        if not dentro:
            baldes.append(Balde(inferior, min(superior, 1.0), 0, None, None))
            continue
        prob_media = sum(p for p, _ in dentro) / len(dentro)
        frequencia = sum(1 for _, o in dentro if o) / len(dentro)
        baldes.append(Balde(inferior, min(superior, 1.0), len(dentro),
                            prob_media, frequencia))

    usaveis = [b for b in baldes if b.suficiente and b.desvio is not None]
    if usaveis:
        peso = sum(b.n for b in usaveis)
        erro = sum(abs(b.desvio) * b.n for b in usaveis) / peso
    else:
        erro = None

    return CalibracaoProb(baldes=tuple(baldes), brier=brier,
                          erro_calibracao=erro, n=len(limpos))


# ─────────────────────────────────────────────────────────────────────────────
# Magnitude, direção e faixa
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Magnitude:
    n: int
    mae: float | None
    mediana_erro: float | None
    vies: float | None
    mae_referencia: float | None = None

    @property
    def ganho_sobre_referencia(self) -> float | None:
        """Fração do erro que o modelo removeu em relação à referência ingênua.

        Sem referência, ``mae`` sozinho não é resultado: é um número solitário.
        O mesmo princípio já está escrito em
        ``core.memoria_mercado.calibracao``, e vale aqui pelo mesmo motivo.
        """
        if self.mae is None or not self.mae_referencia:
            return None
        return (self.mae_referencia - self.mae) / self.mae_referencia


def avaliar_magnitude(pares, referencia=None) -> Magnitude:
    """Erro do valor central a partir de ``(estimado, realizado)``, em fração."""
    erros = []
    for par in pares or ():
        est, real = par[0], par[1]
        if est is None or real is None:
            continue
        est, real = float(est), float(real)
        if not (isfinite(est) and isfinite(real)):
            continue
        erros.append(est - real)
    if not erros:
        return Magnitude(0, None, None, None, None)

    absolutos = sorted(abs(e) for e in erros)
    mae = sum(absolutos) / len(absolutos)
    vies = sum(erros) / len(erros)

    mae_ref = None
    if referencia:
        ref = [abs(float(a) - float(b)) for a, b in referencia
               if a is not None and b is not None]
        if ref:
            mae_ref = sum(ref) / len(ref)

    return Magnitude(len(erros), mae, _mediana(absolutos), vies, mae_ref)


def avaliar_direcao(pares) -> dict:
    """Acerto de direção a partir de ``(estimado, realizado)``.

    Casos em que qualquer um dos dois é zero ficam **fora** da conta e aparecem
    como ``sem_direcao``. Contar "estimou zero, saiu zero" como acerto premiaria
    o motor por não ter opinião, e é assim que uma taxa de acerto alta convive
    com um motor inútil.
    """
    acertos = total = sem_direcao = 0
    for par in pares or ():
        est, real = par[0], par[1]
        if est is None or real is None:
            continue
        est, real = float(est), float(real)
        if est == 0 or real == 0 or not (isfinite(est) and isfinite(real)):
            sem_direcao += 1
            continue
        total += 1
        if (est > 0) == (real > 0):
            acertos += 1
    return {
        "n": total,
        "sem_direcao": sem_direcao,
        "acerto": (acertos / total) if total else None,
        "erro": (1 - acertos / total) if total else None,
    }


def avaliar_faixa(casos, alvo: float = 0.80) -> dict:
    """Cobertura de ``(p10, p90, realizado)``: quanto a faixa de fato contém.

    Uma faixa p10-p90 honesta cobre perto de 80%. Muito acima é faixa larga
    demais para significar alguma coisa; muito abaixo é precisão falsa. Os dois
    defeitos aparecem como o mesmo desvio e são reportados com sinal.
    """
    dentro = total = 0
    for caso in casos or ():
        p10, p90, real = caso[0], caso[1], caso[2]
        if p10 is None or p90 is None or real is None:
            continue
        p10, p90, real = float(p10), float(p90), float(real)
        if not (isfinite(p10) and isfinite(p90) and isfinite(real)):
            continue
        total += 1
        if min(p10, p90) <= real <= max(p10, p90):
            dentro += 1
    cobertura = (dentro / total) if total else None
    return {
        "n": total,
        "cobertura": cobertura,
        "alvo": alvo,
        "desvio": (cobertura - alvo) if cobertura is not None else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Agir contra não agir
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Trajetoria:
    """Resultado de uma política, com o que o retorno bruto esconde."""

    retorno: float | None
    drawdown: float | None
    recuperacao_pregoes: int | None
    turnover: float
    custo: float

    @property
    def retorno_liquido(self) -> float | None:
        if self.retorno is None:
            return None
        return self.retorno - self.custo


def _drawdown(serie: list[float]) -> tuple[float | None, int | None]:
    """Pior queda acumulada e quantos passos até voltar ao topo anterior.

    ``recuperacao`` sai ``None`` quando a série termina sem recuperar. É
    diferente de zero e é diferente do comprimento da série: quem não voltou não
    voltou, e escrever o comprimento ali seria inventar a data da volta.
    """
    if not serie:
        return None, None
    pico = serie[0]
    pior = 0.0
    indice_pico = 0
    pior_indice_pico = 0
    for i, valor in enumerate(serie):
        if valor > pico:
            pico = valor
            indice_pico = i
        queda = (valor / pico) - 1.0 if pico else 0.0
        if queda < pior:
            pior = queda
            pior_indice_pico = indice_pico
    if pior >= 0:
        return 0.0, 0
    alvo = serie[pior_indice_pico]
    for i in range(pior_indice_pico + 1, len(serie)):
        if serie[i] >= alvo:
            return pior, i - pior_indice_pico
    return pior, None


def avaliar_politica(valores, *, turnover: float = 0.0,
                     custo_por_giro: float = 0.0) -> Trajetoria:
    """Trajetória de uma política a partir da série de patrimônio.

    ``custo`` multiplica giro por custo unitário -- corretagem, spread e imposto
    entram aqui como um único número por decisão de quem chama. O ponto não é a
    precisão do custo; é que a comparação com "não agir" **não pode** ser feita
    no bruto: girar sempre ganha no bruto.
    """
    serie = _finitos(valores)
    if len(serie) < 2 or serie[0] <= 0:
        return Trajetoria(None, None, None, float(turnover),
                          float(turnover) * float(custo_por_giro))
    retorno = serie[-1] / serie[0] - 1.0
    dd, rec = _drawdown(serie)
    return Trajetoria(retorno, dd, rec, float(turnover),
                      float(turnover) * float(custo_por_giro))


def comparar(agir: Trajetoria, nao_agir: Trajetoria) -> dict:
    """A diferença que a instrução pede: agir contra não agir, líquido de custo.

    ``melhor`` sai ``None`` quando qualquer um dos dois lados não foi medido --
    e não ``False``. Comparação com lado faltando não é derrota; é comparação
    que não aconteceu.
    """
    def _delta(a, b):
        return None if (a is None or b is None) else a - b

    liquido = _delta(agir.retorno_liquido, nao_agir.retorno_liquido)
    return {
        "retorno_bruto": _delta(agir.retorno, nao_agir.retorno),
        "retorno_liquido": liquido,
        "drawdown": _delta(agir.drawdown, nao_agir.drawdown),
        "turnover_extra": agir.turnover - nao_agir.turnover,
        "custo_extra": agir.custo - nao_agir.custo,
        "recuperacao_agir": agir.recuperacao_pregoes,
        "recuperacao_nao_agir": nao_agir.recuperacao_pregoes,
        "melhor": None if liquido is None else ("agir" if liquido > 0
                                                else "nao_agir"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Estabilidade entre períodos e segmentos
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Estabilidade:
    """Resultado por segmento e o veredito sobre depender de um só período."""

    por_segmento: dict[str, float | None] = field(default_factory=dict)

    @property
    def medidos(self) -> dict[str, float]:
        return {k: v for k, v in self.por_segmento.items() if v is not None}

    @property
    def amplitude(self) -> float | None:
        m = self.medidos
        if len(m) < 2:
            return None
        return max(m.values()) - min(m.values())

    @property
    def positivos(self) -> int:
        return sum(1 for v in self.medidos.values() if v > 0)

    @property
    def concentrado(self) -> bool | None:
        """Verdadeiro quando o desempenho vem de um único segmento.

        É a condição "funcionou apenas em um período específico" da instrução,
        virada em teste: com dois ou mais segmentos medidos, se apenas um for
        positivo, o resultado agregado é aquele segmento com maquiagem.
        """
        m = self.medidos
        if len(m) < 2:
            return None
        return self.positivos <= 1
