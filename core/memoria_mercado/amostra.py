"""Vários eventos comparáveis viram uma distribuição -- ou viram uma recusa.

O requisito é explícito: *"Não use apenas o evento passado mais conhecido.
Sempre que possível, trabalhe com uma amostra de eventos semelhantes e informe o
tamanho da amostra."* Este módulo é onde isso deixa de ser intenção e vira
portão.

Três patamares, e os três aparecem na saída
-------------------------------------------
``n < 8``      nenhuma faixa é publicada. Não é conservadorismo estético: com 5
               observações a mediana muda de sinal se uma delas sair, e o
               intervalo p10-p90 é literalmente o mínimo e o máximo.
``8 <= n < 30`` faixa publicada e marcada **experimental**, com a confiança
               reduzida. É a situação que o requisito antecipa ("caso ainda não
               exista base histórica suficiente"). O exemplo conceitual do
               próprio enunciado -- 8 eventos comparáveis -- cai aqui.
``n >= 30``    faixa publicada sem a marca. 30 é o mesmo piso que
               ``core.noticias.impacto.N_MINIMO_BASE`` já usava; alinhá-los é
               deliberado, para que a ponte entre os dois módulos não tenha dois
               conceitos de "suficiente".

Os dois módulos têm barras diferentes de propósito, e a diferença é sobre o que
cada um faz com o número. O Motor Conjuntural publica probabilidade ao lado de
uma notícia e por isso exige 30. A Memória de Mercado publica uma faixa
explicitamente marcada como experimental e por isso aceita 8. Colapsar os dois
patamares num só perderia informação nas duas direções.

Estatística de posição, não de significância
--------------------------------------------
Mediana e percentis, não média e desvio, para o número central. Reação a evento
tem cauda pesada: uma única aquisição com prêmio de 60% desloca a média de uma
amostra de 12 e não desloca a mediana. Média e desvio ficam publicados ao lado
porque o requisito pede, mas quem manda na estimativa é a mediana.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt

from core.memoria_mercado.retornos import (
    PERSISTENTE,
    REVERSAO,
    REVERSAO_PARCIAL,
    SEM_MOVIMENTO,
    EventoMedido,
)

#: Piso para publicar qualquer faixa, ainda que marcada como experimental.
N_MINIMO_EXPERIMENTAL = 8

#: Piso para a faixa deixar de ser experimental. Mesmo valor de
#: ``core.noticias.impacto.N_MINIMO_BASE``, e a igualdade é intencional.
N_MINIMO_ROBUSTO = 30

#: Abaixo desta fração de eventos com retorno anormal medido, a amostra é
#: descrita como amostra de retorno BRUTO -- o que é uma amostra bem pior, e
#: precisa aparecer assim. Ver ``memoria: procedencia-segue-a-decisao``.
COBERTURA_MINIMA_ANORMAL = 0.60


@dataclass(frozen=True)
class Estatisticas:
    """Descrição de uma distribuição observada. Todos os campos em fração."""

    n: int
    media: float
    mediana: float
    desvio: float | None
    p10: float
    p25: float
    p75: float
    p90: float
    minimo: float
    maximo: float

    @property
    def intervalo_historico(self) -> tuple[float, float]:
        """Mínimo e máximo observados. O intervalo do requisito é este."""
        return (self.minimo, self.maximo)


def _percentil(ordenados: list[float], q: float) -> float:
    """Percentil por interpolação linear (mesmo método do ``numpy`` padrão).

    Implementado à mão para o módulo continuar puro e sem depender de pandas
    numa camada que roda dentro de teste offline.
    """
    if not ordenados:
        raise ValueError("percentil de amostra vazia")
    if len(ordenados) == 1:
        return ordenados[0]
    pos = (len(ordenados) - 1) * max(0.0, min(1.0, q))
    baixo = int(pos)
    alto = min(baixo + 1, len(ordenados) - 1)
    peso = pos - baixo
    return ordenados[baixo] * (1 - peso) + ordenados[alto] * peso


def _descrever(valores) -> Estatisticas | None:
    limpos = sorted(v for v in valores if v is not None and isfinite(v))
    n = len(limpos)
    if n == 0:
        return None
    media = sum(limpos) / n
    desvio = None
    if n >= 2:
        var = sum((v - media) ** 2 for v in limpos) / (n - 1)
        desvio = sqrt(var) if var >= 0 else None
    return Estatisticas(
        n=n,
        media=media,
        mediana=_percentil(limpos, 0.50),
        desvio=desvio,
        p10=_percentil(limpos, 0.10),
        p25=_percentil(limpos, 0.25),
        p75=_percentil(limpos, 0.75),
        p90=_percentil(limpos, 0.90),
        minimo=limpos[0],
        maximo=limpos[-1],
    )


@dataclass(frozen=True)
class AmostraHistorica:
    """Os eventos comparáveis de um tipo, num horizonte, já resumidos."""

    tipo_evento: str
    horizonte: int
    n_eventos: int
    n_com_retorno_anormal: int
    eventos: tuple[EventoMedido, ...] = ()

    anormal: Estatisticas | None = None
    bruto: Estatisticas | None = None
    benchmark: Estatisticas | None = None
    setorial: Estatisticas | None = None
    volatilidade: Estatisticas | None = None
    razao_volume: Estatisticas | None = None
    drawdown: Estatisticas | None = None
    pregoes_ate_o_pior: Estatisticas | None = None
    pregoes_ate_recuperar: Estatisticas | None = None

    n_persistentes: int = 0
    n_reversoes: int = 0
    n_reversoes_parciais: int = 0
    n_sem_movimento: int = 0
    n_recuperaram: int = 0
    n_nao_recuperaram: int = 0

    simbolos: tuple[str, ...] = ()
    periodo: tuple[object, object] | None = None
    usa_retorno_bruto: bool = False
    limitacoes: tuple[str, ...] = ()

    # ── portões ────────────────────────────────────────────────────────────

    @property
    def publicavel(self) -> bool:
        """Há amostra bastante para publicar uma faixa (ainda que experimental)."""
        return self.n_eventos >= N_MINIMO_EXPERIMENTAL and self.principal is not None

    @property
    def robusta(self) -> bool:
        return self.n_eventos >= N_MINIMO_ROBUSTO and self.principal is not None

    @property
    def experimental(self) -> bool:
        """Publicável mas abaixo do piso robusto -- ou apoiada em retorno bruto."""
        return self.publicavel and (not self.robusta or self.usa_retorno_bruto)

    @property
    def principal(self) -> Estatisticas | None:
        """A distribuição que a estimativa usa: anormal se der, bruta se não.

        A troca é reportada em ``usa_retorno_bruto`` e vira limitação impressa.
        Uma amostra de retorno bruto mede "o que a ação fez", não "o que o
        evento fez", e as duas leituras divergem justamente nos episódios de
        crise, que são os que mais interessam.
        """
        return self.anormal if not self.usa_retorno_bruto else self.bruto

    @property
    def fracao_persistente(self) -> float | None:
        classificados = (self.n_persistentes + self.n_reversoes
                         + self.n_reversoes_parciais)
        if classificados == 0:
            return None
        return self.n_persistentes / classificados

    @property
    def fracao_negativa(self) -> float | None:
        st = self.principal
        if st is None or not self.eventos:
            return None
        valores = [self._valor(e) for e in self.eventos]
        medidos = [v for v in valores if v is not None]
        if not medidos:
            return None
        return sum(1 for v in medidos if v < 0) / len(medidos)

    def _valor(self, evento: EventoMedido) -> float | None:
        j = evento.janelas.get(self.horizonte)
        if j is None:
            return None
        return j.retorno_ativo if self.usa_retorno_bruto else j.retorno_anormal

    def prob_movimento_relevante(self, limiar: float) -> float | None:
        """Fração dos eventos cujo movimento superou ``limiar`` em módulo.

        É a única definição de probabilidade que este pacote publica, e ela é
        uma frequência observada -- não uma probabilidade de modelo. O nome do
        parâmetro viaja junto na saída para que "72%" nunca apareça sem o "de
        variação acima de X%".
        """
        medidos = [v for v in (self._valor(e) for e in self.eventos)
                   if v is not None]
        if not medidos:
            return None
        return sum(1 for v in medidos if abs(v) >= abs(limiar)) / len(medidos)


def resumir(eventos, *, tipo_evento: str, horizonte: int,
            cobertura_minima_anormal: float = COBERTURA_MINIMA_ANORMAL
            ) -> AmostraHistorica:
    """Resume os eventos que têm o horizonte medido. Os outros não entram.

    Filtrar por horizonte medido, e não empilhar tudo, é o que impede a mistura
    de ``memoria: foto-truncada-vira-evidencia``: um evento de 2026 que ainda
    não tem 60 pregões de futuro não é um evento de reação nula em 60 pregões,
    é um evento sem 60 pregões -- e somá-lo à amostra puxaria a mediana para
    zero exatamente na ponta mais recente.
    """
    todos = list(eventos or ())
    com_horizonte = [e for e in todos
                     if e.janelas.get(horizonte) is not None
                     and e.janelas[horizonte].medida]

    limitacoes: list[str] = []
    descartados = len(todos) - len(com_horizonte)
    if descartados:
        limitacoes.append(
            f"{descartados} de {len(todos)} eventos sem o horizonte de "
            f"{horizonte} pregoes medido: fora da amostra")

    anormais = [e.janelas[horizonte].retorno_anormal for e in com_horizonte]
    n_anormal = sum(1 for v in anormais if v is not None)
    cobertura = (n_anormal / len(com_horizonte)) if com_horizonte else 0.0
    usa_bruto = cobertura < cobertura_minima_anormal

    if usa_bruto and com_horizonte:
        limitacoes.append(
            f"apenas {n_anormal} de {len(com_horizonte)} eventos tem retorno "
            "anormal medido: amostra descreve o retorno BRUTO, que mistura o "
            "efeito do evento com o movimento geral do mercado")

    if 0 < len(com_horizonte) < N_MINIMO_EXPERIMENTAL:
        limitacoes.append(
            f"amostra de {len(com_horizonte)} eventos, abaixo do minimo de "
            f"{N_MINIMO_EXPERIMENTAL}: nenhuma faixa e publicada")
    elif N_MINIMO_EXPERIMENTAL <= len(com_horizonte) < N_MINIMO_ROBUSTO:
        limitacoes.append(
            f"amostra de {len(com_horizonte)} eventos, abaixo do piso robusto "
            f"de {N_MINIMO_ROBUSTO}: estimativa marcada como experimental")
    elif not com_horizonte:
        limitacoes.append(
            "nenhum evento historico comparavel com este horizonte medido")

    contagem = {PERSISTENTE: 0, REVERSAO: 0, REVERSAO_PARCIAL: 0,
                SEM_MOVIMENTO: 0}
    for e in com_horizonte:
        if e.persistencia in contagem:
            contagem[e.persistencia] += 1

    datas = sorted(e.data_evento for e in com_horizonte)

    return AmostraHistorica(
        tipo_evento=tipo_evento,
        horizonte=horizonte,
        n_eventos=len(com_horizonte),
        n_com_retorno_anormal=n_anormal,
        eventos=tuple(com_horizonte),
        anormal=_descrever(anormais),
        bruto=_descrever(e.janelas[horizonte].retorno_ativo
                         for e in com_horizonte),
        benchmark=_descrever(e.janelas[horizonte].retorno_benchmark
                             for e in com_horizonte),
        setorial=_descrever(e.janelas[horizonte].retorno_setorial
                            for e in com_horizonte),
        volatilidade=_descrever(e.volatilidade_pos for e in com_horizonte),
        razao_volume=_descrever(e.razao_volume for e in com_horizonte),
        drawdown=_descrever(e.drawdown for e in com_horizonte),
        pregoes_ate_o_pior=_descrever(e.pregoes_ate_o_pior
                                      for e in com_horizonte),
        pregoes_ate_recuperar=_descrever(e.pregoes_ate_recuperar
                                         for e in com_horizonte),
        n_persistentes=contagem[PERSISTENTE],
        n_reversoes=contagem[REVERSAO],
        n_reversoes_parciais=contagem[REVERSAO_PARCIAL],
        n_sem_movimento=contagem[SEM_MOVIMENTO],
        n_recuperaram=sum(1 for e in com_horizonte
                          if e.recuperacao_observada is True),
        n_nao_recuperaram=sum(1 for e in com_horizonte
                              if e.recuperacao_observada is False),
        simbolos=tuple(sorted({e.simbolo for e in com_horizonte})),
        periodo=((datas[0], datas[-1]) if datas else None),
        usa_retorno_bruto=usa_bruto,
        limitacoes=tuple(dict.fromkeys(limitacoes)),
    )
