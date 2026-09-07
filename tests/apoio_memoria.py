"""Séries e painéis sintéticos para os testes da Memória de Mercado.

Por que séries construídas e não dados reais: um teste apoiado no dado real
mediria a cobertura do armazém, não o comportamento do código -- e mudaria de
resultado a cada ingestão. Aqui a cobertura é um parâmetro, o que permite
exercitar de propósito os dois lados do portão de densidade.

O motivo original era mais estreito e já não vale: o armazém tinha preço diário
para EUA e FII e não para ações da B3 (1.542 datas em 26 anos). Desde 02/09/2026
as três fontes são diárias (`market.b3_security_history`, 4.134 pregões). A
razão de construir a série sobreviveu à correção da cobertura, e é bom que
tenha: é justamente por não depender do armazém que estes testes seguiram
valendo quando ele mudou.

Nada de aleatório: o ruído diário vem de ``sin`` sobre o índice do pregão. É
determinístico entre execuções e entre máquinas, que é o requisito de
``memoria: determinismo-carteira-b3``. Volatilidade zero não serviria -- a
razão de volatilidade sairia ``None`` e metade das asserções mediria a ausência
do dado em vez do dado.
"""
from __future__ import annotations

from datetime import date, timedelta
from math import sin

from core.memoria_mercado import benchmark as bmk
from core.memoria_mercado.retornos import EventoMedido, medir_evento
from core.memoria_mercado.serie import SeriePrecos

#: Início do calendário sintético. Uma segunda-feira, para o offset e o dia da
#: semana ficarem previsíveis quando um teste precisar de uma sexta-feira.
INICIO = date(2015, 1, 5)

#: Amplitude do ruído diário, em fração. 0,6% ao dia dá ~9,5% ao ano
#: anualizado: baixo o bastante para não afogar um choque de 8% e alto o
#: bastante para a volatilidade pré e pós existirem.
RUIDO = 0.006


def dias_uteis(n: int, inicio: date = INICIO) -> list[date]:
    """``n`` dias de semana consecutivos. Sem feriado: o portão de densidade
    tolera exatamente um pregão de folga, e feriado sintético só embaralharia
    qual asserção está falhando."""
    dias: list[date] = []
    d = inicio
    while len(dias) < n:
        if d.weekday() < 5:
            dias.append(d)
        d += timedelta(days=1)
    return dias


def dias_esparsos(n: int, passo: int = 15, inicio: date = INICIO) -> list[date]:
    """Calendário com um preço a cada ``passo`` dias corridos.

    É a forma da série de ações da B3 no armazém: ~24 observações por ano até
    2013. Serve para provar que o portão de densidade continua reprovando o que
    ele existe para reprovar.
    """
    return [inicio + timedelta(days=i * passo) for i in range(n)]


def serie(simbolo: str, dias, *, base: float = 100.0, deriva: float = 0.0,
          choques: dict | None = None, ruido: float = RUIDO,
          volumes=None, volume_base: float = 1_000_000.0,
          fonte: str | None = None) -> SeriePrecos:
    """Série de preços determinística.

    ``choques`` mapeia ``data -> retorno extra`` aplicado NAQUELE pregão (em
    cima da deriva e do ruído). ``volumes`` mapeia ``data -> volume``; datas
    fora do mapa usam ``volume_base``. ``volumes={}`` mantém o volume constante;
    ``volumes=False`` produz série sem volume nenhum.
    """
    choques = dict(choques or {})
    pares: list[tuple] = []
    preco = float(base)
    sem_volume = volumes is False
    mapa_volume = {} if sem_volume else dict(volumes or {})

    for i, d in enumerate(dias):
        if i:
            r = deriva + ruido * sin(i * 1.7) + float(choques.get(d, 0.0))
            preco *= (1.0 + r)
        if sem_volume:
            pares.append((d, preco))
        else:
            pares.append((d, preco, float(mapa_volume.get(d, volume_base))))
    return SeriePrecos.de_pares(simbolo, pares, fonte=fonte)


def evento(simbolo: str, *, reacao: float, dias=None, offset: int = 200,
           recuperacao: float = 0.0, indice: SeriePrecos | None = None,
           setorial: SeriePrecos | None = None, tipo_evento: str = "resultado",
           chave: str | None = None, modelo: str = bmk.MODELO_DIFERENCA,
           setor: str | None = None, choque_indice: float = 0.0,
           volume_pos: float | None = None, volumes=None,
           **kwargs) -> EventoMedido:
    """Um evento medido, com a reação escrita à mão.

    ``reacao`` é aplicada no pregão ``offset + 1``, de modo que ela apareça
    inteira já no horizonte de 1 pregão. ``recuperacao`` é distribuída entre os
    pregões 21 e 40 depois do evento -- é assim que se constrói uma reversão sem
    tocar no horizonte curto.
    """
    dias = list(dias if dias is not None else dias_uteis(offset + 200))
    data_evento = dias[offset]

    choques = {dias[offset + 1]: reacao}
    if recuperacao:
        por_dia = (1.0 + recuperacao) ** (1.0 / 20.0) - 1.0
        for i in range(offset + 21, min(offset + 41, len(dias))):
            choques[dias[i]] = por_dia

    if volume_pos is not None:
        volumes = {d: volume_pos for d in dias[offset + 1:offset + 61]}

    ativo = serie(simbolo, dias, choques=choques, volumes=volumes, **kwargs)

    if indice is None and choque_indice:
        indice = serie("IDX", dias, choques={dias[offset + 1]: choque_indice},
                       ruido=RUIDO / 2)

    medido = medir_evento(
        chave=chave or f"{simbolo}:{data_evento.isoformat()}",
        simbolo=simbolo, tipo_evento=tipo_evento, data_evento=data_evento,
        ativo=ativo, indice=indice, setorial=setorial, modelo=modelo,
        setor=setor,
    )
    assert medido is not None, "o calendário sintético sempre tem t=0"
    return medido


def indice_plano(dias, *, simbolo: str = "IDX",
                 fonte: str | None = None) -> SeriePrecos:
    """Índice sem tendência: o retorno anormal fica ~igual ao bruto.

    Útil quando o teste é sobre a amostra e não sobre o benchmark -- mas com
    ``retorno_anormal`` medido, para a amostra não cair no caminho do retorno
    bruto e mudar o que está sendo exercitado.
    """
    return serie(simbolo, dias, ruido=RUIDO / 3, fonte=fonte)


def painel(n: int, *, reacao: float = -0.06, dispersao: float = 0.04,
           tipo_evento: str = "resultado", passo: int = 40,
           com_indice: bool = True, recuperacao: float = 0.0,
           horizonte_minimo: int = 60) -> list[EventoMedido]:
    """``n`` eventos comparáveis, um por símbolo, espalhados no tempo.

    Um símbolo por evento e um passo de 40 pregões entre eles são deliberados:
    amostra de símbolo único e amostra concentrada em menos de 12 meses são
    condições invalidantes em :mod:`core.memoria_mercado.estimativa`, e um
    painel que caísse nelas testaria o invalidante em vez do que o teste diz
    testar.

    A dispersão existe para que ``p25`` e ``p75`` não coincidam: uma amostra de
    reações idênticas produz faixa de largura zero e esconde o alargamento.
    """
    total = 200 + n * passo + horizonte_minimo + 200
    dias = dias_uteis(total)
    indice = indice_plano(dias) if com_indice else None

    eventos: list[EventoMedido] = []
    for i in range(n):
        # Espalhamento determinístico em torno da reação central, simétrico o
        # bastante para a mediana ficar perto de `reacao`.
        desvio = dispersao * sin(i * 2.1)
        eventos.append(evento(
            f"ATV{i:02d}", reacao=reacao + desvio, dias=dias,
            offset=200 + i * passo, tipo_evento=tipo_evento,
            indice=indice, recuperacao=recuperacao,
            chave=f"{tipo_evento}:{i:02d}",
        ))
    return eventos


def cenario(**kwargs) -> dict:
    """Cenário completo, com todas as dimensões medidas.

    Os valores são o Brasil de 2015 arredondado: Selic 13%, juros americanos
    perto de zero, IPCA 9%, dólar a 3,90. Servem de linha de base para um teste
    mexer numa dimensão só e ver o fator cair.
    """
    base = {
        "tipo_evento": "resultado",
        "intensidade_evento": 1.0,
        "juros_br": 13.0,
        "juros_us": 0.25,
        "inflacao": 9.0,
        "cambio": 3.90,
        "commodity": 50.0,
        "valuation": 12.0,
        "endividamento": 2.0,
        "expectativa_lucro": 1.0,
        "liquidez": 1.0,
        "volatilidade": 0.30,
        "politico_regulatorio": "estavel",
        "situacao_setorial": "normal",
        "parcela_ja_precificada": 0.10,
    }
    base.update(kwargs)
    return base
