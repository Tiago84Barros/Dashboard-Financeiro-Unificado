"""Índice de referência por ativo e o cálculo do retorno anormal.

O requisito é separar "o que este ativo fez" de "o que o mercado inteiro fez no
mesmo dia". Sem isso, uma queda de 6% num pregão em que o índice caiu 5,5% vira
prova de que a notícia derrubou a ação -- quando a reação específica foi de meio
ponto percentual.

Dois modelos, e o segundo existe porque o requisito pede evolução posterior:

``MODELO_DIFERENCA``  ``AR = r_ativo - r_indice``. Assume beta 1 e alfa 0. É o
                      padrão porque não precisa de janela de estimação, e
                      portanto continua funcionando para ativo recém-listado --
                      exatamente o caso em que o outro modelo falharia calado.
``MODELO_MERCADO``    ``AR = r_ativo - (alfa + beta * r_indice)``, com alfa e
                      beta estimados por MQO numa janela **anterior** ao evento.
                      Corrige o viés que o primeiro tem para ativo de beta
                      distante de 1: uma ação de beta 1,8 num dia de queda geral
                      "produz" retorno anormal negativo sem que nada específico
                      tenha acontecido.

O próximo degrau natural é multifator -- o repositório já tem
``core/ff_risk_model.py``. A porta está aberta em :func:`retorno_anormal`, que
recebe o modelo como parâmetro e não tem nenhum ``if`` de modelo fora daqui.

**Degradação declarada.** Quando ``MODELO_MERCADO`` é pedido mas a janela de
estimação não fecha, a função **não** devolve ``None``: ela cai para a diferença
simples e escreve a troca em ``limitacoes``. Devolver ``None`` jogaria fora um
número utilizável; trocar o modelo em silêncio publicaria um número com o rótulo
do outro. O terceiro caminho -- degradar e dizer -- é o único que preserva os
dois.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt

from core.memoria_mercado.serie import SeriePrecos

MODELO_DIFERENCA = "diferenca"
MODELO_MERCADO = "mercado"

MODELOS = (MODELO_DIFERENCA, MODELO_MERCADO)

#: Pregões da janela de estimação de alfa/beta, tomados ANTES do evento.
JANELA_ESTIMACAO = 120

#: Intervalo em pregões entre o fim da janela de estimação e o evento. Existe
#: para que vazamento de informação anterior à divulgação (o preço mexendo antes
#: da notícia sair) não contamine o beta que servirá de contrafactual.
INTERVALO_ANTECEDENCIA = 5

#: Abaixo disto o par (alfa, beta) é ruído: a estimativa de beta com 20 pontos
#: tem erro-padrão da ordem do próprio beta.
PREGOES_MINIMOS_ESTIMACAO = 60

#: Beta fora desta faixa é sinal de série corrompida (split não ajustado,
#: preço parado, um único outlier dominando a regressão), não de ativo exótico.
BETA_MINIMO, BETA_MAXIMO = -1.0, 4.0

#: Rótulo da procedência quando o índice não é um índice de verdade, e sim a
#: média equiponderada do próprio painel. Ver :func:`indice_equiponderado`.
FONTE_SINTETICA = "equiponderado_local"


@dataclass(frozen=True)
class Beta:
    """Resultado da janela de estimação. ``None`` não chega aqui: se não deu, o
    chamador recebe ``None`` no lugar do objeto inteiro."""

    alfa: float
    beta: float
    n: int
    r2: float | None
    inicio: object = None
    fim: object = None


def estimar_beta(ativo: SeriePrecos, indice: SeriePrecos, i0_ativo: int,
                 *, janela: int = JANELA_ESTIMACAO,
                 antecedencia: int = INTERVALO_ANTECEDENCIA,
                 minimo: int = PREGOES_MINIMOS_ESTIMACAO) -> Beta | None:
    """MQO de ``r_ativo`` contra ``r_indice`` na janela anterior ao evento.

    Casa os retornos **por data**, não por posição. Casar por posição em dois
    quadros ordenados é o defeito de ``memoria: juncao-por-posicao-em-quadro-
    ordenado``: não levanta erro nenhum e inverte a conclusão quando as duas
    séries têm feriados diferentes -- que é o caso normal entre uma ação
    brasileira e um índice americano.
    """
    fim = i0_ativo - max(0, antecedencia)
    inicio = fim - max(1, janela)
    if inicio < 1 or fim <= inicio:
        return None

    indice_por_data = {d: i for i, d in enumerate(indice.datas)}
    pares: list[tuple[float, float]] = []
    for i in range(max(1, inicio), min(fim, len(ativo.datas))):
        d_atual, d_anterior = ativo.datas[i], ativo.datas[i - 1]
        j_atual = indice_por_data.get(d_atual)
        j_anterior = indice_por_data.get(d_anterior)
        if j_atual is None or j_anterior is None:
            continue
        p0, p1 = ativo.fechamentos[i - 1], ativo.fechamentos[i]
        q0, q1 = indice.fechamentos[j_anterior], indice.fechamentos[j_atual]
        if p0 <= 0 or q0 <= 0:
            continue
        pares.append((q1 / q0 - 1.0, p1 / p0 - 1.0))

    n = len(pares)
    if n < minimo:
        return None

    media_x = sum(x for x, _ in pares) / n
    media_y = sum(y for _, y in pares) / n
    sxx = sum((x - media_x) ** 2 for x, _ in pares)
    sxy = sum((x - media_x) * (y - media_y) for x, y in pares)
    if sxx <= 0:
        return None

    beta = sxy / sxx
    if not isfinite(beta) or not (BETA_MINIMO <= beta <= BETA_MAXIMO):
        return None
    alfa = media_y - beta * media_x

    syy = sum((y - media_y) ** 2 for _, y in pares)
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else None

    return Beta(alfa=round(alfa, 8), beta=round(beta, 6), n=n,
                r2=(round(r2, 6) if r2 is not None else None),
                inicio=ativo.datas[max(1, inicio)],
                fim=ativo.datas[min(fim, len(ativo.datas)) - 1])


def retorno_anormal(
    retorno_ativo: float | None,
    retorno_indice: float | None,
    *,
    modelo: str = MODELO_DIFERENCA,
    beta: Beta | None = None,
    pregoes: int | None = None,
) -> tuple[float | None, str | None, tuple[str, ...]]:
    """Devolve ``(retorno_anormal, modelo_aplicado, limitacoes)``.

    ``retorno_anormal`` é ``None`` -- e ``modelo_aplicado`` também -- sempre que
    faltar qualquer uma das duas pernas. **Benchmark ausente não vira benchmark
    zero**: tratar a falta de índice como "o mercado ficou parado" transformaria
    todo retorno bruto em retorno anormal, o que é o defeito de
    ``memoria: fallback-nunca-contradiz`` na sua forma mais cara.

    ``pregoes`` só é usado para acumular o alfa diário no modelo de mercado: um
    alfa de 0,02% ao dia vale 1,2% em 60 pregões, e ignorá-lo enviesaria o
    retorno anormal de horizonte longo na direção do próprio alfa histórico.
    """
    limitacoes: list[str] = []
    if retorno_ativo is None:
        return None, None, ("retorno do ativo nao medido: sem retorno anormal",)
    if retorno_indice is None:
        return None, None, (
            "indice de referencia indisponivel: retorno anormal nao calculado",)

    if modelo == MODELO_MERCADO:
        if beta is None:
            limitacoes.append(
                "janela de estimacao insuficiente para alfa/beta: retorno "
                "anormal calculado por diferenca simples (beta assumido 1)")
            return (retorno_ativo - retorno_indice, MODELO_DIFERENCA,
                    tuple(limitacoes))
        acumulado_alfa = beta.alfa * (pregoes if pregoes and pregoes > 0 else 1)
        esperado = acumulado_alfa + beta.beta * retorno_indice
        if beta.r2 is not None and beta.r2 < 0.05:
            limitacoes.append(
                f"beta com poder explicativo baixo (R2={beta.r2:.2f}): o "
                "contrafactual de mercado explica pouco deste ativo")
        return retorno_ativo - esperado, MODELO_MERCADO, tuple(limitacoes)

    if modelo != MODELO_DIFERENCA:
        limitacoes.append(
            f"modelo '{modelo}' desconhecido: usada a diferenca simples")
    return retorno_ativo - retorno_indice, MODELO_DIFERENCA, tuple(limitacoes)


def indice_equiponderado(series, *, nome: str = "mercado",
                         minimo_ativos: int = 20) -> SeriePrecos:
    """Índice sintético: média equiponderada dos retornos diários do painel.

    Existe por uma razão medida, não por elegância: o armazém local **não tem**
    série utilizável de índice. ``SPY`` e ``QQQ`` guardam 9 linhas cada;
    ``BOVA11``, 220; ``IFIX``, 133. Um retorno anormal calculado contra 9 dias
    de índice seria pior do que nenhum.

    O que este índice **não** é: ele não é o Ibovespa nem o S&P 500. Ele é
    equiponderado (o índice real é ponderado por valor de mercado), é limitado
    ao universo que o painel cobre e herda o viés de sobrevivência do painel --
    ``memoria: painel-so-com-entradas``. Por isso a série sai marcada com
    :data:`FONTE_SINTETICA`, e :mod:`core.memoria_mercado.retornos` propaga essa
    marca até a limitação impressa na tela.

    ``minimo_ativos`` protege as pontas: um "índice" de 3 ativos num dia é o
    retorno desses 3 ativos, e o dia é descartado em vez de entrar como mercado.
    """
    por_data: dict[object, list[float]] = {}
    for s in series or ():
        for i in range(1, len(s.datas)):
            anterior = s.fechamentos[i - 1]
            if anterior <= 0:
                continue
            por_data.setdefault(s.datas[i], []).append(
                s.fechamentos[i] / anterior - 1.0)

    datas = sorted(d for d, rs in por_data.items() if len(rs) >= minimo_ativos)
    if not datas:
        return SeriePrecos(simbolo=nome, datas=(), fechamentos=(), volumes=(),
                           fonte=FONTE_SINTETICA)

    nivel = 100.0
    pares = [(datas[0], nivel)]
    for d in datas[1:]:
        retornos = por_data[d]
        nivel *= 1.0 + sum(retornos) / len(retornos)
        pares.append((d, nivel))

    return SeriePrecos.de_pares(nome, pares, fonte=FONTE_SINTETICA)


def volatilidade_anualizada(retornos, *, pregoes_ano: int = 252) -> float | None:
    """Desvio-padrão amostral dos retornos diários, anualizado."""
    valores = [r for r in (retornos or ()) if r is not None and isfinite(r)]
    n = len(valores)
    if n < 2:
        return None
    media = sum(valores) / n
    var = sum((r - media) ** 2 for r in valores) / (n - 1)
    if var < 0:
        return None
    return sqrt(var) * sqrt(pregoes_ano)
