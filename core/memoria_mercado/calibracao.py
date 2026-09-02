"""Backtest ponto-no-tempo e calibração dos pesos. Prior declarado, não fixo.

O requisito tem duas frases que este módulo existe para cumprir: *"Implemente
backtests e mecanismos de calibração"* e *"Não fixe pesos arbitrários como
definitivos."*

A segunda é a difícil. Os pesos de :mod:`core.memoria_mercado.similaridade` e de
:mod:`core.memoria_mercado.scores` são priores **declarados** -- escritos com o
motivo ao lado, não escolhidos no olho e nem por isso corretos. Este módulo
mede se eles ajudam e os substitui quando houver evidência; enquanto não houver,
devolve o prior com ``calibrado=False``, e esse ``False`` viaja até a tela.

O backtest é ponto-no-tempo, e isso não é detalhe
-------------------------------------------------
:func:`walk_forward` estima o evento de índice ``i`` usando **apenas** os
eventos anteriores a ele. Montar a amostra com todos os eventos e depois
"testar" nela mede o quão bem a mediana descreve os dados que a produziram --
que é sempre muito bem, e não significa nada. O repositório já tem esse
princípio em ``core.us_backtest.walk_forward``, e ``memoria:
ordenacao-nao-e-vantagem`` registra o custo de confundir as duas medidas.

O que é medido
--------------
``cobertura_faixa``    fração dos casos em que o retorno realizado caiu dentro
                       da faixa publicada. Uma faixa honesta acerta perto de
                       :data:`COBERTURA_ALVO`; muito acima significa faixa larga
                       demais para ser útil, muito abaixo significa precisão
                       falsa.
``acerto_direcional``  fração dos casos em que o sinal do central bateu com o
                       sinal do realizado, contada só onde os dois têm direção.
``mae``                erro absoluto médio do valor central.
``mae_referencia``     o mesmo erro para a mediana histórica **sem** ajuste de
                       similaridade. É o que diz se o Fator de Similaridade
                       melhora alguma coisa ou só decora a saída.

Sem o segundo, o primeiro não é resultado: é um número sozinho.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from math import isfinite, sqrt

from core.memoria_mercado import amostra as am
from core.memoria_mercado import similaridade as sim
from core.memoria_mercado.estimativa import Estimativa, estimar

logger = logging.getLogger(__name__)

#: Fração de acertos esperada de uma faixa p25-p75 alargada. Não é 0,50: o
#: alargamento por amostra pequena abre a faixa de propósito.
COBERTURA_ALVO = 0.60

#: Casos mínimos para o backtest dizer alguma coisa. Abaixo disto ele devolve o
#: resultado marcado como insuficiente, e nenhum peso é substituído.
N_MINIMO_BACKTEST = 20

#: Eventos mínimos no conjunto de treino de cada passo do walk-forward. É o piso
#: de publicação da amostra: abaixo dele a estimativa nem sairia em produção, e
#: testá-la seria testar um caminho que não existe.
N_MINIMO_TREINO = am.N_MINIMO_EXPERIMENTAL

#: Encolhimento bayesiano ingênuo na calibração dos pesos: com ``n`` observações
#: o peso calibrado entra com ``n / (n + N_ENCOLHIMENTO)``. Com 20 casos o
#: calibrado vale 40%; com 200, 87%. Impede que trinta observações reescrevam
#: pesos inteiros.
N_ENCOLHIMENTO = 30


@dataclass(frozen=True)
class CasoBacktest:
    """Um evento estimado sem olhar para ele, e o que de fato aconteceu."""

    chave: str
    simbolo: str
    horizonte: int
    estimativa: Estimativa
    realizado: float
    dimensoes: dict = field(default_factory=dict)

    @property
    def dentro_da_faixa(self) -> bool | None:
        f = self.estimativa.faixa
        if f is None:
            return None
        return f[0] <= self.realizado <= f[1]

    @property
    def erro(self) -> float | None:
        c = self.estimativa.valor_central
        return None if c is None else (c - self.realizado)


@dataclass(frozen=True)
class ResultadoBacktest:
    """As quatro medidas, o tamanho e o motivo de não confiar nelas."""

    n: int
    cobertura_faixa: float | None
    acerto_direcional: float | None
    mae: float | None
    mae_referencia: float | None
    vies: float | None
    n_sem_estimativa: int = 0
    limitacoes: tuple[str, ...] = ()

    @property
    def suficiente(self) -> bool:
        return self.n >= N_MINIMO_BACKTEST and self.mae is not None

    @property
    def ganho_sobre_referencia(self) -> float | None:
        """Redução relativa do erro contra a mediana histórica crua.

        Negativo significa que o ajuste por similaridade **piorou** a
        estimativa. Publicar isso é o ponto: um mecanismo de ajuste que não é
        medido contra a alternativa de não ajustar nada é decoração --
        ``memoria: diagnostico-precisa-porta-de-entrada``.
        """
        if self.mae is None or not self.mae_referencia:
            return None
        return (self.mae_referencia - self.mae) / self.mae_referencia

    def texto(self) -> str:
        if not self.suficiente:
            return (f"backtest com {self.n} casos: abaixo do minimo de "
                    f"{N_MINIMO_BACKTEST}, sem conclusao")
        cob = ("indisponivel" if self.cobertura_faixa is None
               else f"{self.cobertura_faixa * 100:.0f}%")
        dirc = ("indisponivel" if self.acerto_direcional is None
                else f"{self.acerto_direcional * 100:.0f}%")
        ganho = self.ganho_sobre_referencia
        g = "indisponivel" if ganho is None else f"{ganho * 100:+.0f}%"
        return (f"{self.n} casos; cobertura da faixa: {cob} (alvo "
                f"{COBERTURA_ALVO * 100:.0f}%); acerto direcional: {dirc}; "
                f"erro medio: {self.mae * 100:.2f} pp; ganho sobre a mediana "
                f"historica crua: {g}")


def _media(valores) -> float | None:
    limpos = [v for v in valores if v is not None and isfinite(v)]
    return (sum(limpos) / len(limpos)) if limpos else None


def walk_forward(eventos, *, tipo_evento: str, horizonte: int,
                 cenarios: dict | None = None,
                 minimo_treino: int = N_MINIMO_TREINO) -> list[CasoBacktest]:
    """Estima cada evento usando só os anteriores a ele, em ordem de data.

    ``cenarios`` mapeia ``chave do evento -> dicionário de dimensões`` (as
    chaves ``DIM_*`` de :mod:`core.memoria_mercado.similaridade`). Quando ele é
    fornecido, o cenário do evento sob teste é comparado ao cenário mediano dos
    eventos de treino, exatamente como em produção. Sem ele, o backtest mede o
    caminho sem similaridade -- e a limitação é registrada por quem chama.

    Eventos com a mesma data são ordenados por chave, para o resultado não
    depender da ordem de chegada (``memoria: determinismo-carteira-b3``).
    """
    ordenados = sorted(
        (e for e in (eventos or ())
         if e.janelas.get(horizonte) is not None and e.janelas[horizonte].medida),
        key=lambda e: (e.data_evento, e.chave, e.simbolo),
    )
    cenarios = dict(cenarios or {})
    casos: list[CasoBacktest] = []

    for i, evento in enumerate(ordenados):
        treino = ordenados[:i]
        if len(treino) < minimo_treino:
            continue
        amostra = am.resumir(treino, tipo_evento=tipo_evento,
                             horizonte=horizonte)
        janela = evento.janelas[horizonte]
        realizado = (janela.retorno_ativo if amostra.usa_retorno_bruto
                     else janela.retorno_anormal)
        if realizado is None or not isfinite(realizado):
            continue

        similar = None
        cenario_atual = cenarios.get(evento.chave)
        if cenario_atual:
            base = sim.cenario_medio(
                [cenarios[t.chave] for t in treino if t.chave in cenarios])
            if base:
                similar = sim.calcular(cenario_atual, base)

        est = estimar(amostra, similar, simbolo=evento.simbolo)
        casos.append(CasoBacktest(
            chave=evento.chave, simbolo=evento.simbolo, horizonte=horizonte,
            estimativa=est, realizado=realizado,
            dimensoes=({d.chave: d.valor for d in similar.dimensoes}
                       if similar is not None else {}),
        ))
    return casos


def avaliar(casos) -> ResultadoBacktest:
    """Consolida os casos do walk-forward nas quatro medidas."""
    todos = list(casos or ())
    publicaveis = [c for c in todos if c.estimativa.publicavel]
    sem_estimativa = len(todos) - len(publicaveis)

    limitacoes: list[str] = []
    if sem_estimativa:
        limitacoes.append(
            f"{sem_estimativa} de {len(todos)} casos nao geraram faixa (amostra "
            "de treino abaixo do piso): fora das medidas")

    if not publicaveis:
        limitacoes.append("nenhum caso com faixa publicada: backtest vazio")
        return ResultadoBacktest(n=0, cobertura_faixa=None,
                                 acerto_direcional=None, mae=None,
                                 mae_referencia=None, vies=None,
                                 n_sem_estimativa=sem_estimativa,
                                 limitacoes=tuple(limitacoes))

    dentro = [c.dentro_da_faixa for c in publicaveis]
    dentro = [d for d in dentro if d is not None]
    cobertura = (sum(1 for d in dentro if d) / len(dentro)) if dentro else None

    direcionais = [
        (1.0 if (c.estimativa.valor_central > 0) == (c.realizado > 0) else 0.0)
        for c in publicaveis
        if c.estimativa.valor_central not in (None, 0.0) and c.realizado != 0.0
    ]
    acerto = _media(direcionais)

    erros = [abs(c.erro) for c in publicaveis if c.erro is not None]
    mae = _media(erros)
    vies = _media([c.erro for c in publicaveis if c.erro is not None])

    referencia = _media([
        abs(c.estimativa.mediana_historica - c.realizado)
        for c in publicaveis if c.estimativa.mediana_historica is not None
    ])

    if len(publicaveis) < N_MINIMO_BACKTEST:
        limitacoes.append(
            f"backtest com {len(publicaveis)} casos, abaixo do minimo de "
            f"{N_MINIMO_BACKTEST}: medidas publicadas como indicativas")
    if cobertura is not None and cobertura > 0.90:
        limitacoes.append(
            f"faixa cobriu {cobertura * 100:.0f}% dos casos, bem acima do alvo "
            f"de {COBERTURA_ALVO * 100:.0f}%: a faixa esta larga demais para "
            "ser acionavel")
    if cobertura is not None and cobertura < 0.35:
        limitacoes.append(
            f"faixa cobriu apenas {cobertura * 100:.0f}% dos casos: precisao "
            "falsa, a faixa esta estreita demais para a incerteza real")

    return ResultadoBacktest(
        n=len(publicaveis),
        cobertura_faixa=(round(cobertura, 4) if cobertura is not None else None),
        acerto_direcional=(round(acerto, 4) if acerto is not None else None),
        mae=(round(mae, 6) if mae is not None else None),
        mae_referencia=(round(referencia, 6) if referencia is not None else None),
        vies=(round(vies, 6) if vies is not None else None),
        n_sem_estimativa=sem_estimativa,
        limitacoes=tuple(dict.fromkeys(limitacoes)),
    )


@dataclass(frozen=True)
class PesosCalibrados:
    """Pesos em uso, e a declaração honesta de de onde eles vieram."""

    pesos: dict
    calibrado: bool
    n: int
    encolhimento: float | None = None
    correlacoes: dict = field(default_factory=dict)
    limitacoes: tuple[str, ...] = ()


def _correlacao(pares) -> float | None:
    limpos = [(x, y) for x, y in pares
              if x is not None and y is not None and isfinite(x) and isfinite(y)]
    n = len(limpos)
    if n < 3:
        return None
    mx = sum(x for x, _ in limpos) / n
    my = sum(y for _, y in limpos) / n
    sxx = sum((x - mx) ** 2 for x, _ in limpos)
    syy = sum((y - my) ** 2 for _, y in limpos)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in limpos)
    return sxy / sqrt(sxx * syy)


def calibrar_pesos_similaridade(casos, *, prior: dict | None = None,
                                minimo: int = N_MINIMO_BACKTEST,
                                encolhimento: int = N_ENCOLHIMENTO
                                ) -> PesosCalibrados:
    """Repesa as dimensões pelo quanto cada uma antecipou o erro da estimativa.

    A hipótese testada por dimensão é simples e falsificável: *se esta dimensão
    mede algo, então quanto mais parecida ela estiver, menor deveria ser o erro
    da estimativa*. Isso é a correlação entre a similaridade da dimensão e o
    **negativo** do erro absoluto. Dimensão com correlação nula ou negativa não
    ganha peso -- ela não estava ajudando.

    Três recusas deliberadas:

    * menos de ``minimo`` casos devolve o prior com ``calibrado=False``. É o
      caminho normal hoje, e precisa continuar sendo dito em voz alta;
    * dimensão com menos de três pares mensuráveis não entra;
    * se nenhuma correlação for positiva, devolve o prior -- calibrar para pesos
      todos zerados seria trocar um prior declarado por um artefato de ruído.

    O resultado é encolhido em direção ao prior por ``n / (n + encolhimento)``,
    de modo que uma base pequena mexa pouco.
    """
    prior = dict(prior or sim.PESOS_PRIOR)
    lista = [c for c in (casos or ()) if c.estimativa.publicavel]
    n = len(lista)

    if n < minimo:
        return PesosCalibrados(
            pesos=prior, calibrado=False, n=n,
            limitacoes=(
                f"apenas {n} casos de backtest, abaixo do minimo de {minimo}: "
                "pesos de similaridade seguem sendo os priores declarados",))

    correlacoes: dict[str, float] = {}
    for chave in sim.DIMENSOES:
        pares = [(c.dimensoes.get(chave), -abs(c.erro)) for c in lista
                 if c.erro is not None]
        r = _correlacao(pares)
        if r is not None:
            correlacoes[chave] = round(r, 4)

    positivas = {k: v for k, v in correlacoes.items() if v > 0}
    if not positivas:
        return PesosCalibrados(
            pesos=prior, calibrado=False, n=n, correlacoes=correlacoes,
            limitacoes=(
                "nenhuma dimensao do cenario correlacionou com a reducao do "
                "erro: pesos mantidos no prior, e isso e evidencia contra o "
                "proprio fator de similaridade, nao a favor dele",))

    soma = sum(positivas.values())
    calibrados = {k: (positivas.get(k, 0.0) / soma) for k in prior}
    lam = n / (n + max(1, encolhimento))
    mistura = {k: round((1 - lam) * prior.get(k, 0.0) + lam * calibrados.get(k, 0.0), 6)
               for k in prior}
    total = sum(mistura.values())
    if total <= 0:  # pragma: no cover - defesa
        return PesosCalibrados(pesos=prior, calibrado=False, n=n,
                               correlacoes=correlacoes,
                               limitacoes=("pesos calibrados somaram zero",))
    mistura = {k: round(v / total, 6) for k, v in mistura.items()}

    logger.info("pesos de similaridade calibrados com %d casos (lambda=%.2f)",
                n, lam)
    return PesosCalibrados(
        pesos=mistura, calibrado=True, n=n, encolhimento=round(lam, 4),
        correlacoes=correlacoes,
        limitacoes=(
            f"pesos calibrados sobre {n} casos e encolhidos em direcao ao "
            f"prior com peso {lam:.2f}; recalibrar quando a base crescer",))
