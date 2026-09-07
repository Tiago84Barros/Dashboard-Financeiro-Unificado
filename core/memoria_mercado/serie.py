"""Série de preços por pregão, com o portão que impede medir o que não existe.

O motivo deste módulo existir separado é uma medição feita no armazém local
antes de qualquer linha de código ser escrita:

===========================================  =========  =======  ==============
tabela                                        linhas     datas    leitura
===========================================  =========  =======  ==============
``market_us.prices_daily``                   13.342.783  16.267   diária de fato
``market.fii_b3_security_history``              606.552   4.099   diária de fato
``market.b3_security_history`` (ações B3)     1.627.752   4.134   diária de fato
===========================================  =========  =======  ==============

A terceira linha era ``market.historical_prices``: 137.735 linhas em 1.542
datas cobrindo 2000-2026, ou ~24 pregões por ano até 2013 -- série mensal, não
diária. Uma função que receba essa série, some um índice e chame o resultado de
"retorno em 1 pregão" devolve, na prática, o retorno de duas semanas -- sem
erro, sem aviso, com o rótulo errado. É o modo de falha registrado em
``memoria: defeito-silencioso-vs-erro``.

A defesa é :meth:`SeriePrecos.densidade`: a janela sabe quantos pregões
observados ela tem e quantos deveria ter no mesmo intervalo de calendário. Uma
janela de 5 pregões que ocupa 70 dias corridos não é uma janela de 5 pregões, e
:meth:`SeriePrecos.janela_valida` devolve ``False`` -- o que faz a métrica sair
``None`` lá na frente, em vez de sair errada.

Em 02/09/2026 a série diária de ações da B3 passou a existir, ingerida do
COTAHIST oficial (``data_pipeline/market/b3_precos.py``), e a Memória de Mercado
foi repontada para ela. **O portão não foi afrouxado por causa disso** -- os
limiares abaixo continuam onde estavam; foi a série que melhorou. Medido nos
mesmos parâmetros, um evento de 2024 em PETR4/VALE3/ITUB4/WEGE3/MGLU3 saía não
medido até 63 pregões na série antiga e passa em 1, 5, 21 e 63 na nova. O portão
segue valendo para qualquer símbolo cuja cobertura seja rala, que é o caso de
papel recém-listado ou com longa suspensão.

Módulo puro: sem SQL, sem rede, sem Streamlit.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite

#: Pregões por dia de calendário. 252 sessões em 365,25 dias é a convenção usada
#: no resto do repositório (``core.us_backtest.performance_stats``) e está aqui
#: para converter "dias corridos" em "pregões esperados".
PREGOES_POR_DIA_CORRIDO = 252.0 / 365.25

#: Fração mínima de pregões esperados que a janela precisa conter para ser
#: chamada de janela de pregões. 0,60 tolera feriado prolongado e um ou outro dia
#: sem negócio (série real de FII tem isso), e reprova com folga a série
#: mensal-disfarçada-de-diária da B3, que fica abaixo de 0,15.
DENSIDADE_MINIMA = 0.60

#: Folga de um pregão no denominador da densidade. Ela existe por uma medição,
#: não por gosto: numa série diária perfeita, uma janela de 1 pregão iniciada
#: numa sexta-feira ocupa 3 dias corridos, "esperaria" 2,07 pregões e sairia com
#: densidade 0,48 -- reprovada. O resultado seria perder o horizonte de 1 pregão
#: de **todo evento de sexta-feira**, cerca de um quinto da amostra, sem erro
#: nenhum aparecer. A conversão dias-corridos -> pregões tem resolução de uma
#: sessão; o portão não pode reprovar dentro da própria resolução dele.
#:
#: A folga é constante e some nas janelas longas (em 60 pregões ela vale 1,2%),
#: então ela não afrouxa o que o portão existe para pegar: a série da B3, com
#: ~24 pregões por ano, continua abaixo de 0,15 em qualquer horizonte.
TOLERANCIA_PREGOES = 1.0

#: Janela mínima em pregões para volatilidade fazer sentido. Abaixo disto o
#: desvio-padrão amostral é mais ruído do que medida.
PREGOES_MINIMOS_VOLATILIDADE = 10


def _como_data(valor) -> date | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return datetime.fromisoformat(str(valor)[:10]).date()
    except (TypeError, ValueError):
        return None


def _como_float(valor) -> float | None:
    if valor is None:
        return None
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return None
    return f if isfinite(f) else None


@dataclass(frozen=True)
class SeriePrecos:
    """Fechamentos ordenados de um símbolo, com volume opcional.

    ``datas`` é estritamente crescente e sem repetição -- garantido por
    :meth:`de_pares`, que é o construtor que o resto do pacote usa. Construir o
    dataclass na mão sem essa garantia quebra a busca binária de
    :meth:`indice_do_pregao`, e por isso :meth:`de_pares` existe.

    ``fonte`` viaja junto porque uma estimativa construída sobre um índice
    sintético e outra construída sobre o índice de verdade não são a mesma
    estimativa, e quem lê precisa conseguir distinguir as duas.
    """

    simbolo: str
    datas: tuple[date, ...]
    fechamentos: tuple[float, ...]
    volumes: tuple[float | None, ...] = ()
    fonte: str | None = None

    @classmethod
    def de_pares(cls, simbolo: str, pares, *, fonte: str | None = None) -> SeriePrecos:
        """Constrói a partir de ``(data, fechamento[, volume])``.

        Descarta linha sem data ou sem fechamento utilizável -- preço ausente
        permanece ausente, nunca vira zero nem é interpolado. Data repetida fica
        com a última ocorrência, que é a convenção de quem reprocessa um dia.
        """
        por_data: dict[date, tuple[float, float | None]] = {}
        for par in pares or ():
            if len(par) == 2:
                bruta, fechamento = par
                volume = None
            else:
                bruta, fechamento, volume = par[0], par[1], par[2]
            d = _como_data(bruta)
            f = _como_float(fechamento)
            if d is None or f is None or f <= 0:
                continue
            v = _como_float(volume)
            por_data[d] = (f, v if (v is None or v >= 0) else None)

        ordenadas = sorted(por_data)
        return cls(
            simbolo=simbolo,
            datas=tuple(ordenadas),
            fechamentos=tuple(por_data[d][0] for d in ordenadas),
            volumes=tuple(por_data[d][1] for d in ordenadas),
            fonte=fonte,
        )

    def __len__(self) -> int:
        return len(self.datas)

    @property
    def vazia(self) -> bool:
        return not self.datas

    # ── localização do evento no calendário ────────────────────────────────

    def indice_do_pregao(self, quando) -> int | None:
        """Índice do primeiro pregão em ou após ``quando``. ``None`` se não há.

        A convenção é *em ou após*, não *o mais próximo*: um fato divulgado num
        sábado -- ou depois do fechamento -- só pode ser absorvido pelo preço na
        sessão seguinte. Escolher o pregão anterior por estar "mais perto" faria
        o retorno de t=0 conter o dia em que o mercado ainda não sabia, que é
        vazamento de informação para trás.
        """
        d = _como_data(quando)
        if d is None or self.vazia:
            return None
        i = bisect_left(self.datas, d)
        return i if i < len(self.datas) else None

    def data_em(self, i: int) -> date | None:
        return self.datas[i] if 0 <= i < len(self.datas) else None

    def fechamento_em(self, i: int) -> float | None:
        return self.fechamentos[i] if 0 <= i < len(self.fechamentos) else None

    # ── janelas ────────────────────────────────────────────────────────────

    def densidade(self, i0: int, h: int) -> float | None:
        """Pregões observados sobre pregões esperados na janela ``i0 -> i0+h``.

        Devolve ``None`` quando a janela não cabe na série. Devolve ``1.0`` no
        caso normal de série diária (h sessões ocupam ~1,45*h dias corridos) e
        cai proporcionalmente quando a série é esparsa. O teto em 1,0 evita que
        uma semana sem feriado nenhum apareça como "mais densa que o possível",
        e :data:`TOLERANCIA_PREGOES` evita que o fim de semana reprove uma
        janela curta que está perfeitamente completa.
        """
        if h <= 0 or i0 < 0 or i0 + h >= len(self.datas):
            return None
        corridos = (self.datas[i0 + h] - self.datas[i0]).days
        if corridos <= 0:
            return None
        esperados = corridos * PREGOES_POR_DIA_CORRIDO - TOLERANCIA_PREGOES
        if esperados <= 0:
            # A janela é curta demais para o denominador dizer qualquer coisa:
            # 1 pregão em 1 dia corrido é densidade máxima por construção.
            return 1.0
        return min(1.0, h / esperados)

    def janela_valida(self, i0: int, h: int,
                      *, densidade_minima: float = DENSIDADE_MINIMA) -> bool:
        d = self.densidade(i0, h)
        return d is not None and d >= densidade_minima

    def retorno(self, i0: int, h: int,
                *, densidade_minima: float = DENSIDADE_MINIMA) -> float | None:
        """Retorno simples de ``i0`` a ``i0+h``, ou ``None`` se a janela mente.

        Duas causas distintas para o ``None`` -- janela fora da série e janela
        esparsa demais -- e as duas devolvem ``None`` de propósito: quem chama
        não deve poder confundir nenhuma delas com um retorno medido. A
        distinção entre elas é reportada por quem monta a limitação, em
        :mod:`core.memoria_mercado.retornos`.
        """
        if i0 < 0 or h <= 0 or i0 + h >= len(self.fechamentos):
            return None
        if not self.janela_valida(i0, h, densidade_minima=densidade_minima):
            return None
        p0 = self.fechamentos[i0]
        p1 = self.fechamentos[i0 + h]
        if p0 <= 0:
            return None
        return p1 / p0 - 1.0

    def retornos_diarios(self, i0: int, i1: int) -> tuple[float, ...]:
        """Retornos pregão a pregão no intervalo fechado ``[i0, i1]``."""
        i0 = max(0, i0)
        i1 = min(len(self.fechamentos) - 1, i1)
        if i1 <= i0:
            return ()
        saida = []
        for i in range(i0 + 1, i1 + 1):
            anterior = self.fechamentos[i - 1]
            if anterior > 0:
                saida.append(self.fechamentos[i] / anterior - 1.0)
        return tuple(saida)

    def volume_medio(self, i0: int, i1: int) -> float | None:
        """Volume médio no intervalo fechado, ignorando pregão sem volume.

        Volume ausente fica de fora da média em vez de entrar como zero. Entrar
        como zero é o defeito de ``memoria: medicao-que-pune-a-evidencia``:
        puniria justamente o período com pior cobertura de dados.
        """
        i0 = max(0, i0)
        i1 = min(len(self.volumes) - 1, i1)
        if i1 < i0:
            return None
        medidos = [v for v in self.volumes[i0:i1 + 1] if v is not None and v > 0]
        if not medidos:
            return None
        return sum(medidos) / len(medidos)


SERIE_VAZIA = SeriePrecos(simbolo="", datas=(), fechamentos=(), volumes=())
