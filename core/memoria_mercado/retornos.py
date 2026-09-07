"""Como um ativo reagiu a UM evento: a medição bruta, antes de qualquer média.

Este é o tijolo. :mod:`core.memoria_mercado.amostra` empilha vários destes; a
estimativa só olha para a pilha. Aqui não há nenhuma inferência sobre o futuro
-- só a leitura do que os preços fizeram em torno de uma data.

Todo campo do requisito tem um lugar em :class:`EventoMedido`, e todo campo pode
ser ``None``. ``None`` significa **não medido**, nunca "medido e deu zero". A
distinção é o que separa "esta ação não se moveu" de "não sabemos o que esta
ação fez", e colapsá-las é o defeito de ``memoria: medicao-que-pune-a-evidencia``.

Convenção de tempo
------------------
``t = 0`` é o primeiro pregão em ou após a data do evento (ver
:meth:`serie.SeriePrecos.indice_do_pregao`). O retorno de ``h`` pregões vai do
fechamento de ``t=0`` ao fechamento de ``t=h``. Isso significa que o retorno de
1 pregão **exclui** a sessão em que o fato foi divulgado durante o pregão -- é a
escolha conservadora: inclui-la exigiria saber a hora exata da divulgação, que
grande parte das fontes não dá, e errar essa hora para trás é vazamento.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from core.memoria_mercado import benchmark as bmk
from core.memoria_mercado.serie import (
    DENSIDADE_MINIMA,
    PREGOES_MINIMOS_VOLATILIDADE,
    SeriePrecos,
)

#: Horizontes exigidos pelo requisito, em pregões.
HORIZONTES = (1, 5, 20, 60)

#: Pregões antes do evento usados como linha de base de volume e volatilidade.
JANELA_PRE = 60

#: Pregões observados após o evento na busca por pior ponto e recuperação. 120
#: pregões (~6 meses) é o teto: além disso o "tempo de recuperação" mede o ciclo
#: da empresa, não a reação ao evento.
JANELA_ACOMPANHAMENTO = 120

#: Movimento âncora abaixo do qual perguntar "persistiu ou reverteu?" não faz
#: sentido: 0,3% de retorno anormal em 5 pregões é ruído, e classificar o ruído
#: produziria uma taxa de reversão de 50% que não mede nada.
LIMIAR_MOVIMENTO = 0.01

#: Fração do movimento inicial que precisa sobreviver até o horizonte longo para
#: o efeito ser chamado de persistente.
FRACAO_PERSISTENCIA = 0.50

PERSISTENTE = "persistente"
REVERSAO_PARCIAL = "reversao_parcial"
REVERSAO = "reversao"
SEM_MOVIMENTO = "sem_movimento"

#: Motivos de janela não medida, separados porque levam a ações diferentes:
#: histórico curto se resolve esperando, série esparsa não se resolve nunca.
MOTIVO_FORA_DA_SERIE = "historico insuficiente"
MOTIVO_ESPARSA = "serie sem densidade diaria"

#: Distância máxima, em dias corridos, entre a data do evento e o pregão zero.
#: ``SeriePrecos.indice_do_pregao`` devolve o primeiro pregão *em ou após* a
#: data, e essa convenção está certa para um fato de sábado ou de feriado. Ela
#: não tem fundo, porém: um evento anterior ao início da série casa com a
#: primeira linha existente, a quatorze anos de distância, e sai daqui um
#: ``EventoMedido`` completo, confiante e sobre outro dia.
#:
#: Medido: das 3.000 datas-ex mais antigas de ``market.dividends`` (1995-2005),
#: 2.067 recebiam ``data_pregao_zero = 2010-01-04`` -- o primeiro pregão de
#: ``market.b3_security_history`` -- e todas eram "medidas" na mesma janela.
#: É ``memoria: fallback-nunca-contradiz``: o preenchimento só tapa buraco, e
#: por isso nunca aparece como erro.
#:
#: Onze dias cobrem feriado longo emendado com fim de semana e recesso; acima
#: disso não é calendário, é ausência de série.
TOLERANCIA_PREGAO_ZERO_DIAS = 11


@dataclass(frozen=True)
class MetricasJanela:
    """Um horizonte, com as quatro leituras lado a lado.

    ``retorno_anormal`` é o número que a estimativa usa. Os outros três estão
    aqui como evidência: publicar só o anormal esconderia que uma queda de 8%
    do ativo aconteceu num dia de queda de 7,5% do mercado.
    """

    horizonte: int
    retorno_ativo: float | None = None
    retorno_benchmark: float | None = None
    retorno_setorial: float | None = None
    retorno_anormal: float | None = None
    retorno_anormal_setorial: float | None = None
    modelo_anormal: str | None = None
    densidade: float | None = None
    motivo_ausencia: str | None = None

    @property
    def medida(self) -> bool:
        return self.retorno_ativo is not None


@dataclass(frozen=True)
class EventoMedido:
    """A reação observada de um ativo a um evento datado."""

    chave: str
    simbolo: str
    tipo_evento: str
    data_evento: date
    data_pregao_zero: date | None = None
    janelas: dict[int, MetricasJanela] = field(default_factory=dict)

    volatilidade_pos: float | None = None
    volatilidade_pre: float | None = None
    razao_volatilidade: float | None = None
    volume_medio_pos: float | None = None
    volume_medio_pre: float | None = None
    razao_volume: float | None = None

    drawdown: float | None = None
    pregoes_ate_o_pior: int | None = None
    pregoes_ate_recuperar: int | None = None
    recuperacao_observada: bool | None = None

    persistencia: str | None = None
    deriva_pre_evento: float | None = None

    benchmark: str | None = None
    benchmark_sintetico: bool = False
    setor: str | None = None
    beta: bmk.Beta | None = None
    limitacoes: tuple[str, ...] = ()

    def retorno_anormal(self, horizonte: int) -> float | None:
        j = self.janelas.get(horizonte)
        return j.retorno_anormal if j else None

    def retorno(self, horizonte: int) -> float | None:
        j = self.janelas.get(horizonte)
        return j.retorno_ativo if j else None

    @property
    def horizontes_medidos(self) -> tuple[int, ...]:
        return tuple(h for h in sorted(self.janelas) if self.janelas[h].medida)

    @property
    def tem_retorno_anormal(self) -> bool:
        return any(j.retorno_anormal is not None for j in self.janelas.values())


def _motivo(serie: SeriePrecos, i0: int, h: int, densidade_minima: float) -> str:
    if i0 + h >= len(serie.datas):
        return MOTIVO_FORA_DA_SERIE
    d = serie.densidade(i0, h)
    if d is None or d < densidade_minima:
        return MOTIVO_ESPARSA
    return MOTIVO_FORA_DA_SERIE


def _classificar_persistencia(ancora: float | None,
                              final: float | None) -> str | None:
    """Persistiu, reverteu em parte ou reverteu -- ou não dá para dizer.

    Compara o movimento âncora (horizonte curto) com o de horizonte longo. A
    razão, e não a diferença, porque a pergunta é "quanto do movimento
    sobreviveu", e essa é uma pergunta relativa.
    """
    if ancora is None or final is None:
        return None
    if abs(ancora) < LIMIAR_MOVIMENTO:
        return SEM_MOVIMENTO
    razao = final / ancora
    if razao >= FRACAO_PERSISTENCIA:
        return PERSISTENTE
    if razao >= 0.0:
        return REVERSAO_PARCIAL
    return REVERSAO


def _drawdown(serie: SeriePrecos, i0: int, ate: int) -> tuple[
        float | None, int | None, int | None, bool | None]:
    """Pior queda desde ``t=0``, pregões até o fundo e até voltar ao nível.

    ``recuperacao_observada`` distingue "recuperou em 34 pregões" de "ainda não
    tinha recuperado quando a janela acabou". As duas dariam ``None`` em
    ``pregoes_ate_recuperar`` se o campo não existisse, e a segunda seria lida
    como dado faltante em vez de como o fato que ela é.
    """
    fim = min(len(serie.fechamentos) - 1, i0 + ate)
    if fim <= i0:
        return None, None, None, None
    p0 = serie.fechamentos[i0]
    if p0 <= 0:
        return None, None, None, None

    pior = 0.0
    i_pior = i0
    for i in range(i0 + 1, fim + 1):
        queda = serie.fechamentos[i] / p0 - 1.0
        if queda < pior:
            pior, i_pior = queda, i

    if pior >= 0.0:
        # Nunca ficou abaixo do nível de t=0: drawdown zero é uma MEDIÇÃO aqui,
        # não uma ausência, e por isso vai como 0.0 e não como None.
        return 0.0, 0, 0, True

    recuperou = None
    for i in range(i_pior + 1, fim + 1):
        if serie.fechamentos[i] >= p0:
            recuperou = i - i0
            break
    return pior, i_pior - i0, recuperou, recuperou is not None


def medir_evento(
    *,
    chave: str,
    simbolo: str,
    tipo_evento: str,
    data_evento,
    ativo: SeriePrecos,
    indice: SeriePrecos | None = None,
    setorial: SeriePrecos | None = None,
    modelo: str = bmk.MODELO_DIFERENCA,
    horizontes: tuple[int, ...] = HORIZONTES,
    densidade_minima: float = DENSIDADE_MINIMA,
    setor: str | None = None,
) -> EventoMedido | None:
    """Mede a reação de um ativo a um evento. ``None`` se o evento nem cabe.

    Devolve ``None`` só quando a data do evento não existe no calendário do
    ativo -- aí não há ``t=0`` e não há nada a medir. Em qualquer outro caso
    devolve o objeto com os campos que deram e ``None`` nos que não deram: um
    evento com 20 pregões de história ainda informa os horizontes 1, 5 e 20.
    """
    i0 = ativo.indice_do_pregao(data_evento)
    if i0 is None:
        return None

    # O pregão zero precisa ser o pregão *daquele* evento. Ver
    # TOLERANCIA_PREGAO_ZERO_DIAS: sem este corte, evento anterior ao início da
    # série vira medição da primeira linha que existir.
    if (ativo.datas[i0] - _data(data_evento)).days > TOLERANCIA_PREGAO_ZERO_DIAS:
        return None

    limitacoes: list[str] = []
    tem_indice = indice is not None and not indice.vazia
    if not tem_indice:
        limitacoes.append(
            "sem indice de referencia para este ativo: retorno anormal nao "
            "calculado, apenas retorno bruto")

    beta = None
    if modelo == bmk.MODELO_MERCADO and tem_indice:
        beta = bmk.estimar_beta(ativo, indice, i0)

    i0_indice = indice.indice_do_pregao(ativo.datas[i0]) if tem_indice else None
    i0_setorial = (setorial.indice_do_pregao(ativo.datas[i0])
                   if setorial is not None and not setorial.vazia else None)

    janelas: dict[int, MetricasJanela] = {}
    vistos_modelo: set[str] = set()
    for h in horizontes:
        r_ativo = ativo.retorno(i0, h, densidade_minima=densidade_minima)
        densidade = ativo.densidade(i0, h)
        if r_ativo is None:
            janelas[h] = MetricasJanela(
                horizonte=h, densidade=densidade,
                motivo_ausencia=_motivo(ativo, i0, h, densidade_minima))
            continue

        r_indice = (indice.retorno(i0_indice, h, densidade_minima=densidade_minima)
                    if (tem_indice and i0_indice is not None) else None)
        r_setor = (setorial.retorno(i0_setorial, h,
                                    densidade_minima=densidade_minima)
                   if i0_setorial is not None else None)

        anormal, modelo_aplicado, lims = bmk.retorno_anormal(
            r_ativo, r_indice, modelo=modelo, beta=beta, pregoes=h)
        anormal_setor, _, _ = bmk.retorno_anormal(
            r_ativo, r_setor, modelo=bmk.MODELO_DIFERENCA)

        for lim in lims:
            if lim not in vistos_modelo:
                vistos_modelo.add(lim)
                limitacoes.append(lim)

        janelas[h] = MetricasJanela(
            horizonte=h,
            retorno_ativo=r_ativo,
            retorno_benchmark=r_indice,
            retorno_setorial=r_setor,
            retorno_anormal=anormal,
            retorno_anormal_setorial=anormal_setor,
            modelo_anormal=modelo_aplicado,
            densidade=densidade,
        )

    # ── volatilidade e volume: pós contra pré, para a comparação ter base ──
    pre_inicio = max(0, i0 - JANELA_PRE)
    retornos_pre = ativo.retornos_diarios(pre_inicio, i0)
    retornos_pos = ativo.retornos_diarios(i0, i0 + max(horizontes))
    vol_pre = (bmk.volatilidade_anualizada(retornos_pre)
               if len(retornos_pre) >= PREGOES_MINIMOS_VOLATILIDADE else None)
    vol_pos = (bmk.volatilidade_anualizada(retornos_pos)
               if len(retornos_pos) >= PREGOES_MINIMOS_VOLATILIDADE else None)

    vol_medio_pre = ativo.volume_medio(pre_inicio, i0)
    vol_medio_pos = ativo.volume_medio(i0, i0 + max(horizontes))
    if vol_medio_pre is None or vol_medio_pos is None:
        razao_volume = None
        if not ativo.volumes or all(v is None for v in ativo.volumes):
            limitacoes.append("serie sem volume: razao de volume nao medida")
    else:
        razao_volume = (vol_medio_pos / vol_medio_pre) if vol_medio_pre > 0 else None

    dd, ate_pior, ate_recuperar, recuperou = _drawdown(
        ativo, i0, JANELA_ACOMPANHAMENTO)
    if recuperou is False:
        limitacoes.append(
            f"nao havia recuperado ao nivel de t=0 em {JANELA_ACOMPANHAMENTO} "
            "pregoes: tempo de recuperacao e um piso, nao uma medida")

    # Deriva pré-evento: quanto o ativo já tinha andado nos 20 pregões
    # anteriores. É a evidência bruta de "a informação já estava no preço"; a
    # leitura fica em `similaridade.parcela_precificada`.
    deriva = None
    if i0 - 20 >= 0:
        p_antes = ativo.fechamentos[i0 - 20]
        if p_antes > 0:
            deriva = ativo.fechamentos[i0] / p_antes - 1.0

    ancora = janelas.get(5) or janelas.get(1)
    longo = janelas.get(60) or janelas.get(20)
    persistencia = _classificar_persistencia(
        (ancora.retorno_anormal if ancora and ancora.retorno_anormal is not None
         else (ancora.retorno_ativo if ancora else None)),
        (longo.retorno_anormal if longo and longo.retorno_anormal is not None
         else (longo.retorno_ativo if longo else None)),
    ) if (ancora and longo) else None

    if indice is not None and indice.fonte == bmk.FONTE_SINTETICA:
        limitacoes.append(
            "indice de referencia e sintetico (media equiponderada do painel "
            "local), nao o indice de mercado publicado")

    nao_medidos = [h for h in horizontes if not janelas[h].medida]
    if nao_medidos:
        motivos = {janelas[h].motivo_ausencia for h in nao_medidos}
        limitacoes.append(
            f"horizontes nao medidos: {', '.join(str(h) for h in nao_medidos)} "
            f"({'; '.join(sorted(m for m in motivos if m))})")

    return EventoMedido(
        chave=chave,
        simbolo=simbolo,
        tipo_evento=tipo_evento,
        data_evento=_data(data_evento),
        data_pregao_zero=ativo.datas[i0],
        janelas=janelas,
        volatilidade_pos=vol_pos,
        volatilidade_pre=vol_pre,
        razao_volatilidade=((vol_pos / vol_pre)
                            if (vol_pos and vol_pre and vol_pre > 0) else None),
        volume_medio_pos=vol_medio_pos,
        volume_medio_pre=vol_medio_pre,
        razao_volume=razao_volume,
        drawdown=dd,
        pregoes_ate_o_pior=ate_pior,
        pregoes_ate_recuperar=ate_recuperar,
        recuperacao_observada=recuperou,
        persistencia=persistencia,
        deriva_pre_evento=deriva,
        benchmark=(indice.simbolo if tem_indice else None),
        benchmark_sintetico=bool(tem_indice and indice.fonte == bmk.FONTE_SINTETICA),
        setor=setor,
        beta=beta,
        limitacoes=tuple(dict.fromkeys(limitacoes)),
    )


def _data(valor) -> date:
    from core.memoria_mercado.serie import _como_data

    d = _como_data(valor)
    if d is None:
        raise ValueError(f"data de evento invalida: {valor!r}")
    return d
