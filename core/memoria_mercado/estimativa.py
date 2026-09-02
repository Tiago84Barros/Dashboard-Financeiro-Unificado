"""Amostra histórica + similaridade do cenário = faixa. Nunca um ponto.

O requisito é literal: *"O resultado não deverá ser uma promessa pontual."* Este
módulo é o único ponto do pacote que combina as duas metades, e a forma da saída
é a defesa contra transformá-la em promessa: onze campos, dos quais três são
tamanho de amostra, similaridade e confiança, e três são fatores que ampliam,
fatores que reduzem e condições que invalidam a comparação.

As fórmulas
-----------
Seja ``m`` a mediana histórica do retorno anormal no horizonte, ``s`` o Fator de
Similaridade em fração (0 a 1), ``p`` a parcela já precificada em fração e ``n``
o tamanho da amostra.

``atenuacao = ATENUACAO_PISO + (1 - ATENUACAO_PISO) * s``
    Similaridade 100 preserva a referência histórica inteira; similaridade 0 a
    reduz a metade -- e **não** a zero. Zerar seria afirmar que cenários pouco
    parecidos garantem reação nula, o que é uma afirmação mais forte do que a
    evidência permite. Similaridade baixa demais não vira número pequeno: vira
    recusa, em :data:`similaridade.SIMILARIDADE_INVALIDANTE`.

``central = m * atenuacao * (1 - p)``
    A parcela já precificada desconta linearmente. ``p = 1`` produz central zero
    -- que é a leitura correta de "o mercado já sabia disso".

``faixa = centro +- semiamplitude``, com ``centro`` e a semiamplitude vindos de
``p25`` e ``p75`` da distribuição observada, escalados pelo mesmo fator, e a
semiamplitude multiplicada por ``1 + K_ALARGAMENTO / sqrt(n)``
    O alargamento é o preço da amostra pequena. Com ``n = 8`` a faixa abre 35%;
    com ``n = 100``, 10%. É deliberado que a faixa de uma amostra rala fique
    larga a ponto de ser pouco acionável -- é isso que ela é.

Conferência contra o exemplo do enunciado: mediana −6,4%, similaridade 74%,
``atenuacao`` = 0,87, central ≈ −5,6%, dentro da faixa −3% a −7% do exemplo.

O que este módulo não faz
-------------------------
Não recomenda, não ordena e não pontua ativo. Ele devolve uma faixa com
procedência. Quem converte isso em ação é
:mod:`core.memoria_mercado.scores`, e o teto do que aquele módulo pode fazer é
suspender aporte -- nunca vender.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite, sqrt

from core.memoria_mercado import amostra as am
from core.memoria_mercado import similaridade as sim
from core.noticias.impacto import (
    CONFIANCA_ALTA,
    CONFIANCA_BAIXA,
    CONFIANCA_MEDIA,
)
from core.noticias.taxonomia import (
    DIRECAO_ALTA,
    DIRECAO_BAIXA,
    DIRECAO_INDEFINIDA,
    DIRECAO_NEUTRA,
)

#: Piso da atenuação por similaridade. Ver a fórmula no docstring do módulo.
ATENUACAO_PISO = 0.50

#: Constante de alargamento da faixa por amostra pequena.
K_ALARGAMENTO = 1.0

#: Abaixo deste valor absoluto o impacto central é chamado de neutro. Meio ponto
#: percentual está dentro do custo de ida e volta somado ao ruído diário da
#: maioria dos ativos: chamar isso de direção seria precisão falsa.
LIMIAR_DIRECAO = 0.005

#: Similaridade assumida quando nenhuma foi calculada. Não é 100 (assumir
#: cenário idêntico) nem 0 (assumir incomparável): é o meio, marcado como
#: limitação, porque a ausência do cálculo não é evidência em nenhuma direção.
SIMILARIDADE_OMISSA = 50.0

#: Frações de persistência/reversão a partir das quais o padrão vira fator.
LIMIAR_PADRAO_DOMINANTE = 0.65

#: Razão de volume acima da qual o evento é tratado como tendo mobilizado fluxo.
LIMIAR_VOLUME_ALTO = 1.5


@dataclass(frozen=True)
class Estimativa:
    """Os onze campos que o requisito pede, mais o que os sustenta.

    ``faixa`` e ``valor_central`` vêm em fração (−0,064 = −6,4%). ``horizonte``
    é um par em pregões. ``similaridade`` é 0 a 100. Campo ``None`` significa
    não medido, e nunca zero.
    """

    tipo_evento: str
    simbolo: str | None
    faixa: tuple[float, float] | None
    valor_central: float | None
    horizonte: tuple[int, int] | None
    horizonte_base: int
    direcao: str
    n_amostra: int
    similaridade: float | None
    confianca: str
    experimental: bool
    publicavel: bool

    intervalo_historico: tuple[float, float] | None = None
    mediana_historica: float | None = None
    parcela_ja_precificada: float | None = None
    base_retorno: str = "anormal"

    fatores_ampliam: tuple[str, ...] = ()
    fatores_reduzem: tuple[str, ...] = ()
    condicoes_invalidam: tuple[str, ...] = ()
    limitacoes: tuple[str, ...] = ()
    detalhes: dict = field(default_factory=dict)

    @property
    def acionavel(self) -> bool:
        """Sustenta ajuste de prioridade de aporte.

        Faixa publicada, sem condição invalidante e com direção definida. Note
        que ``acionavel`` não quer dizer "confiável": uma estimativa
        experimental pode ser acionável, e é por isso que ``experimental`` e
        ``confianca`` viajam ao lado e precisam ser exibidos junto.
        """
        return (self.publicavel and not self.condicoes_invalidam
                and self.direcao in (DIRECAO_ALTA, DIRECAO_BAIXA))

    def texto(self) -> str:
        """Linha única no formato do exemplo conceitual do requisito."""
        if not self.publicavel:
            return (f"eventos comparaveis: {self.n_amostra}; amostra "
                    "insuficiente: nenhuma faixa estimada")
        faixa = self.faixa or (0.0, 0.0)
        hist = self.intervalo_historico or (0.0, 0.0)
        h = self.horizonte or (self.horizonte_base, self.horizonte_base)
        marca = " (experimental)" if self.experimental else ""
        sml = ("similaridade indisponivel" if self.similaridade is None
               else f"similaridade atual: {self.similaridade:.0f}%")
        return (
            f"eventos comparaveis: {self.n_amostra}; "
            f"reacao historica mediana: {self.mediana_historica * 100:+.1f}%; "
            f"intervalo historico: {hist[0] * 100:+.1f}% a {hist[1] * 100:+.1f}%; "
            f"{sml}; "
            f"impacto atual estimado: {faixa[0] * 100:+.1f}% a "
            f"{faixa[1] * 100:+.1f}%; "
            f"horizonte: {h[0]} a {h[1]} pregoes; "
            f"confianca: {self.confianca}{marca}"
        )


def _fracao(valor) -> float | None:
    if valor is None:
        return None
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return None
    if not isfinite(f):
        return None
    return max(0.0, min(1.0, f))


def _direcao(central: float | None, *, limiar: float = LIMIAR_DIRECAO) -> str:
    if central is None:
        return DIRECAO_INDEFINIDA
    if central >= limiar:
        return DIRECAO_ALTA
    if central <= -limiar:
        return DIRECAO_BAIXA
    return DIRECAO_NEUTRA


def _confianca(amostra: am.AmostraHistorica, fator: float | None,
               cobertura: float, *, precificado: float | None) -> str:
    """Confiança por contagem de condições satisfeitas, todas verificáveis.

    Nenhuma delas é uma opinião sobre o ativo: são tamanho de amostra,
    procedência do retorno, similaridade, cobertura das dimensões e consistência
    de sinal. Confiança alta exige as cinco.
    """
    condicoes = [
        amostra.robusta,
        not amostra.usa_retorno_bruto,
        fator is not None and fator >= 60.0,
        cobertura >= 0.60,
        (amostra.fracao_negativa is not None
         and (amostra.fracao_negativa >= 0.70
              or amostra.fracao_negativa <= 0.30)),
    ]
    atendidas = sum(1 for c in condicoes if c)
    if precificado is not None and precificado >= 0.70:
        # Informação em grande parte precificada: qualquer que seja a amostra, a
        # reação futura observável é pequena e a estimativa fica frágil.
        atendidas = min(atendidas, 2)
    if atendidas >= 5:
        return CONFIANCA_ALTA
    if atendidas >= 3:
        return CONFIANCA_MEDIA
    return CONFIANCA_BAIXA


def _horizonte(amostra: am.AmostraHistorica) -> tuple[int, int]:
    """Faixa de pregões em que a reação se materializou nos eventos observados.

    Usa p25-p75 de ``pregoes_ate_o_pior`` quando há: é o tempo observado até o
    ponto extremo, e portanto uma medida, não uma convenção. Sem isso, cai para
    metade a totalidade do horizonte da amostra e a limitação é registrada por
    quem chama.
    """
    st = amostra.pregoes_ate_o_pior
    h = max(1, int(amostra.horizonte))
    if st is not None and st.n >= am.N_MINIMO_EXPERIMENTAL:
        lo = max(1, int(round(st.p25)))
        hi = max(lo, min(h, int(round(st.p75))))
        if hi > lo:
            return (lo, hi)
    return (max(1, h // 2), h)


def _fatores(amostra: am.AmostraHistorica,
             similar: sim.Similaridade | None,
             precificado: float | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Fatores que ampliam e que reduzem, cada um preso a um número observado.

    Fator sem número atrás é opinião com aparência de análise. Todo item desta
    lista cita a contagem, a fração ou a razão que o gerou.
    """
    amplia: list[str] = []
    reduz: list[str] = []

    fp = amostra.fracao_persistente
    if fp is not None and amostra.n_eventos >= am.N_MINIMO_EXPERIMENTAL:
        if fp >= LIMIAR_PADRAO_DOMINANTE:
            amplia.append(
                f"movimento persistiu em {amostra.n_persistentes} de "
                f"{amostra.n_eventos} eventos ({fp * 100:.0f}%): o efeito "
                "historico nao se desfez dentro da janela")
        elif fp <= 1.0 - LIMIAR_PADRAO_DOMINANTE:
            reduz.append(
                f"houve reversao total ou parcial em "
                f"{amostra.n_reversoes + amostra.n_reversoes_parciais} de "
                f"{amostra.n_eventos} eventos: o efeito historico tendeu a se "
                "desfazer")

    if amostra.n_recuperaram or amostra.n_nao_recuperaram:
        total = amostra.n_recuperaram + amostra.n_nao_recuperaram
        if amostra.n_nao_recuperaram / total >= LIMIAR_PADRAO_DOMINANTE:
            amplia.append(
                f"{amostra.n_nao_recuperaram} de {total} eventos nao "
                "recuperaram o preco de t=0 dentro da janela acompanhada")
        elif amostra.n_recuperaram / total >= LIMIAR_PADRAO_DOMINANTE:
            reduz.append(
                f"{amostra.n_recuperaram} de {total} eventos recuperaram o "
                "preco de t=0 dentro da janela acompanhada")

    rv = amostra.razao_volume
    if rv is not None and rv.mediana >= LIMIAR_VOLUME_ALTO:
        amplia.append(
            f"volume mediano {rv.mediana:.1f}x o normal apos o evento: houve "
            "mobilizacao de fluxo, nao so reprecificacao")

    vol = amostra.volatilidade
    if vol is not None and vol.mediana is not None and vol.mediana > 0.60:
        amplia.append(
            f"volatilidade anualizada mediana de {vol.mediana * 100:.0f}% apos "
            "o evento: a dispersao de desfechos e alta")

    dd = amostra.drawdown
    principal = amostra.principal
    if dd is not None and principal is not None and dd.mediana < 0:
        if abs(dd.mediana) >= 2 * abs(principal.mediana):
            amplia.append(
                f"queda maxima mediana de {dd.mediana * 100:.1f}% contra "
                f"retorno mediano de {principal.mediana * 100:+.1f}%: o "
                "caminho foi bem pior que o destino")

    if precificado is not None and precificado > 0:
        reduz.append(
            f"parcela estimada de {precificado * 100:.0f}% da informacao ja "
            "refletida no preco antes do evento")

    if similar is not None:
        for chave, rotulo in (
            (sim.DIM_ENDIVIDAMENTO, "endividamento"),
            (sim.DIM_VALUATION, "valuation"),
            (sim.DIM_LIQUIDEZ, "liquidez"),
        ):
            d = next((x for x in similar.dimensoes if x.chave == chave), None)
            if d is None or not d.medida or d.valor >= 0.75:
                continue
            destino = amplia if chave == sim.DIM_ENDIVIDAMENTO else reduz
            destino.append(
                f"{rotulo} de hoje distante do observado nos eventos "
                f"historicos (similaridade da dimensao: {d.valor * 100:.0f}%)")

    if amostra.usa_retorno_bruto:
        reduz.append(
            "amostra apoiada em retorno bruto: parte do movimento medido pode "
            "ser do mercado, nao do evento")

    return tuple(dict.fromkeys(amplia)), tuple(dict.fromkeys(reduz))


def _invalidantes(amostra: am.AmostraHistorica,
                  similar: sim.Similaridade | None) -> tuple[str, ...]:
    """Condições sob as quais a comparação histórica não vale.

    Separadas das limitações de propósito: limitação é ressalva sobre um número
    publicado; condição invalidante é o motivo para não agir sobre ele.
    """
    itens: list[str] = []
    if similar is not None:
        itens.extend(similar.invalidantes)

    if amostra.n_eventos and len(amostra.simbolos) == 1:
        itens.append(
            f"todos os {amostra.n_eventos} eventos comparaveis sao do mesmo "
            f"ativo ({amostra.simbolos[0]}): a amostra descreve um historico, "
            "nao um padrao de mercado")

    periodo = amostra.periodo
    if periodo and amostra.n_eventos >= am.N_MINIMO_EXPERIMENTAL:
        try:
            anos = (periodo[1] - periodo[0]).days / 365.25
        except (TypeError, AttributeError):
            anos = None
        if anos is not None and anos < 1.0:
            itens.append(
                f"os {amostra.n_eventos} eventos ocorreram em menos de 12 "
                "meses: a amostra cobre um unico regime macroeconomico")

    return tuple(dict.fromkeys(itens))


def estimar(amostra: am.AmostraHistorica,
            similar: sim.Similaridade | None = None,
            *,
            simbolo: str | None = None,
            parcela_ja_precificada: float | None = None) -> Estimativa:
    """Combina amostra e similaridade numa faixa com procedência.

    ``parcela_ja_precificada`` pode vir explícita ou ser lida da dimensão
    :data:`similaridade.DIM_JA_PRECIFICADO` do cenário de hoje. Explícito ganha.

    Amostra abaixo do piso de publicação **não** produz faixa: devolve uma
    estimativa com ``publicavel=False``, ``faixa=None`` e a limitação dizendo o
    tamanho que faltou. É o caminho que o requisito pede para "caso ainda não
    exista base histórica suficiente" -- sem falsa precisão, informando a
    ausência de amostra adequada.
    """
    limitacoes: list[str] = list(amostra.limitacoes)

    fator = similar.fator if similar is not None else None
    cobertura = similar.cobertura if similar is not None else 0.0
    if similar is None:
        limitacoes.append(
            f"fator de similaridade nao calculado: assumida similaridade "
            f"neutra de {SIMILARIDADE_OMISSA:.0f}/100 apenas para nao "
            "amplificar nem anular a referencia historica")
        fator_uso = SIMILARIDADE_OMISSA
    elif fator is None:
        limitacoes.extend(similar.limitacoes)
        fator_uso = SIMILARIDADE_OMISSA
    else:
        limitacoes.extend(similar.limitacoes)
        fator_uso = fator
        if not similar.utilizavel:
            limitacoes.append(
                "similaridade publicada mas nao utilizavel para ajustar a "
                "referencia historica")

    precificado = _fracao(parcela_ja_precificada)
    if precificado is None and similar is not None:
        precificado = _fracao(
            next((d.hoje for d in similar.dimensoes
                  if d.chave == sim.DIM_JA_PRECIFICADO), None))

    ampliam, reduzem = _fatores(amostra, similar, precificado)
    invalidam = _invalidantes(amostra, similar)
    horizonte = _horizonte(amostra)
    base = "bruto" if amostra.usa_retorno_bruto else "anormal"

    principal = amostra.principal
    if not amostra.publicavel or principal is None:
        limitacoes.append(
            f"amostra de {amostra.n_eventos} eventos: abaixo do minimo de "
            f"{am.N_MINIMO_EXPERIMENTAL} para publicar qualquer faixa")
        return Estimativa(
            tipo_evento=amostra.tipo_evento, simbolo=simbolo, faixa=None,
            valor_central=None, horizonte=None,
            horizonte_base=amostra.horizonte, direcao=DIRECAO_INDEFINIDA,
            n_amostra=amostra.n_eventos, similaridade=fator,
            confianca=CONFIANCA_BAIXA, experimental=True, publicavel=False,
            mediana_historica=(principal.mediana if principal else None),
            intervalo_historico=(principal.intervalo_historico
                                 if principal else None),
            parcela_ja_precificada=precificado, base_retorno=base,
            fatores_ampliam=ampliam, fatores_reduzem=reduzem,
            condicoes_invalidam=invalidam,
            limitacoes=tuple(dict.fromkeys(limitacoes)),
            detalhes={"cobertura_similaridade": cobertura},
        )

    s = max(0.0, min(1.0, fator_uso / 100.0))
    atenuacao = ATENUACAO_PISO + (1.0 - ATENUACAO_PISO) * s
    desconto = 1.0 - (precificado or 0.0)
    escala = atenuacao * desconto

    central = principal.mediana * escala
    lo, hi = principal.p25 * escala, principal.p75 * escala
    meio, semi = (lo + hi) / 2.0, abs(hi - lo) / 2.0
    alargamento = 1.0 + K_ALARGAMENTO / sqrt(max(1, amostra.n_eventos))
    semi *= alargamento
    faixa = (round(meio - semi, 6), round(meio + semi, 6))

    confianca = _confianca(amostra, fator, cobertura, precificado=precificado)
    if amostra.experimental:
        limitacoes.append(
            "estimativa marcada como EXPERIMENTAL: a base historica ainda nao "
            "atende o piso de robustez")
    limitacoes.append(
        f"faixa alargada em {(alargamento - 1) * 100:.0f}% para refletir a "
        f"incerteza de uma amostra de {amostra.n_eventos} eventos")
    if amostra.pregoes_ate_o_pior is None or (
            amostra.pregoes_ate_o_pior.n < am.N_MINIMO_EXPERIMENTAL):
        limitacoes.append(
            "horizonte derivado da janela de medicao, nao do tempo observado "
            "ate o pior ponto: nao ha registros suficientes desse tempo")

    return Estimativa(
        tipo_evento=amostra.tipo_evento,
        simbolo=simbolo,
        faixa=faixa,
        valor_central=round(central, 6),
        horizonte=horizonte,
        horizonte_base=amostra.horizonte,
        direcao=_direcao(central),
        n_amostra=amostra.n_eventos,
        similaridade=fator,
        confianca=confianca,
        experimental=amostra.experimental,
        publicavel=True,
        intervalo_historico=principal.intervalo_historico,
        mediana_historica=principal.mediana,
        parcela_ja_precificada=precificado,
        base_retorno=base,
        fatores_ampliam=ampliam,
        fatores_reduzem=reduzem,
        condicoes_invalidam=invalidam,
        limitacoes=tuple(dict.fromkeys(limitacoes)),
        detalhes={
            "atenuacao_similaridade": round(atenuacao, 4),
            "desconto_ja_precificado": round(desconto, 4),
            "alargamento": round(alargamento, 4),
            "cobertura_similaridade": cobertura,
            "media_historica": principal.media,
            "desvio_historico": principal.desvio,
            "p10": principal.p10,
            "p90": principal.p90,
        },
    )
