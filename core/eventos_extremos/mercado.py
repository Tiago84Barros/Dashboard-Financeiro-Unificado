"""O que os preços já fizeram -- a classe de evidência que pode contradizer.

A evidência de mercado é a única das três que consegue discordar da manchete, e
é por isso que ela existe separada. Sem ela, "houve um colapso bancário" e "os
preços não se moveram" seriam a mesma coisa para o motor.

O que dá para medir hoje, e o que não dá
----------------------------------------
Este projeto tem preço diário com volume da B3 (``market.b3_security_history``,
1,6 M linhas) e dos EUA (``market_us.prices_daily``, 13,3 M). Não tem VIX, ouro,
petróleo, cesta de commodities, spread de crédito nem câmbio diário. Os cinco
indicadores de :data:`evidencias.MEDIVEIS_HOJE` saem medidos; os outros seis
saem ``None`` e a cobertura mostra o buraco. Preencher os ausentes com ``0,0``
transformaria "não tenho a fonte" em "o mercado está calmo", que é exatamente a
inversão que este pacote foi escrito para não cometer.

Três armadilhas que este módulo evita de propósito
--------------------------------------------------
**A referência não pode conter o choque.** Se a volatilidade dos últimos 10
pregões for comparada com a de uma janela que inclui esses mesmos 10 pregões, o
próprio choque infla o denominador e a razão sai amortecida -- o evento esconde
a si mesmo. Aqui a janela de referência termina onde a curta começa.

**Mercado defasado não é mercado calmo.** Se o último pregão da série for mais
antigo que :data:`DEFASAGEM_MAXIMA_DIAS`, nada é medido e a limitação viaja
escrita. É o caminho que faz o motor de transição aplicar o teto de "sem
evidência de mercado" em vez de concluir tranquilidade a partir de silêncio.

**Índice de poucos ativos é um ativo disfarçado.** O índice equiponderado herda
o piso de ``minimo_ativos`` de :mod:`core.memoria_mercado.benchmark`: um dia com
3 papéis é o retorno desses 3 papéis, não o mercado.

Módulo puro: sem SQL, sem rede, sem Streamlit. Quem carrega as séries é o
chamador (``scripts/construir_memoria_mercado.py::carregar_series`` faz isso).
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from math import isfinite, sqrt
from statistics import median

from core.eventos_extremos import evidencias as ev
from core.memoria_mercado import benchmark as bmk
from core.memoria_mercado.serie import (
    PREGOES_MINIMOS_VOLATILIDADE,
    SeriePrecos,
)

logger = logging.getLogger(__name__)

#: Janela curta: o que o mercado fez "agora". Dez pregões é o menor intervalo em
#: que o desvio-padrão amostral ainda diz alguma coisa
#: (:data:`serie.PREGOES_MINIMOS_VOLATILIDADE`) e ainda é curto o bastante para
#: um crash de duas semanas aparecer inteiro dentro dele.
JANELA_CURTA = 10

#: Janela de referência, terminando onde a curta começa. Cerca de seis meses.
JANELA_REFERENCIA = 120

#: Pregões mínimos de referência para a comparação ser comparação.
REFERENCIA_MINIMA = 40

#: Dias corridos que o último pregão pode ter de atraso antes de a série deixar
#: de descrever o presente. Cobre feriado prolongado; não cobre série parada.
DEFASAGEM_MAXIMA_DIAS = 7

#: Teto de símbolos usados na correlação par a par. O custo é quadrático e o
#: ganho satura; a seleção é por ordem alfabética, não por amostragem, porque
#: ordenação parcial já produziu resultados diferentes para a mesma entrada
#: neste repositório.
MAXIMO_ATIVOS_CORRELACAO = 40

#: Pares mínimos com datas em comum para uma correlação valer.
OBSERVACOES_MINIMAS_CORRELACAO = 8

#: Indicadores para os quais este projeto não tem fonte. Ficam ``None``.
SEM_FONTE_HOJE = tuple(sorted(set(ev.CORTES_DE_MERCADO) - ev.MEDIVEIS_HOJE))


@dataclass(frozen=True)
class Medicao:
    """Indicadores de mercado calculados, com o que não deu para calcular."""

    medicoes: dict[str, float | None]
    fontes: dict[str, str]
    limitacoes: tuple[str, ...]
    ate: dt.date | None = None
    ativos: int = 0

    @property
    def mediu_algo(self) -> bool:
        return any(v is not None for v in self.medicoes.values())

    def para_evidencia(self) -> ev.Evidencia:
        return ev.mercado(self.medicoes, fontes=self.fontes,
                          limitacoes=self.limitacoes)


#: Pregões por ano, para anualizar a volatilidade realizada.
PREGOES_POR_ANO = 252


def _volatilidade_realizada(retornos) -> float | None:
    """Volatilidade anualizada **não centrada** na média da própria janela.

    Existe em vez de :func:`benchmark.volatilidade_anualizada` por um defeito
    medido, não por preferência. Aquela função é desvio-padrão amostral, e
    portanto subtrai a média da janela antes de elevar ao quadrado -- num
    tombo direcional, a média *é* o tombo. Uma queda de 3% ao dia por dez
    pregões (-26% no total) sai com volatilidade **zero**, e a razão contra a
    referência sai 0,0: o motor conclui "mercado calmo" no meio de um crash,
    sem erro nenhum aparecer. É o modo de falha de
    ``memoria: defeito-silencioso-vs-erro``.

    A convenção de mercado para volatilidade realizada é assumir média diária
    zero justamente por isso, e é o que se faz aqui. A diferença some no ruído
    (retorno médio diário é ~0) e aparece exatamente onde precisa aparecer.

    :func:`benchmark.volatilidade_anualizada` fica como está: ela serve a
    Memória de Mercado, cujos resultados publicados não podem mudar por causa
    deste módulo.
    """
    valores = [r for r in (retornos or ())
               if r is not None and isfinite(r)]
    if len(valores) < 2:
        return None
    ms = sum(r * r for r in valores) / len(valores)
    return sqrt(ms) * sqrt(PREGOES_POR_ANO) if ms >= 0 else None


def _indice_ate(serie: SeriePrecos, quando: dt.date | None) -> int | None:
    """Posição do último pregão em ou antes de ``quando``.

    Deliberadamente o último *antes*, e não o primeiro *depois*: o detector roda
    no presente e só pode olhar o que já fechou. Pegar o pregão seguinte seria
    ler um preço que ainda não existe.
    """
    if serie.vazia:
        return None
    if quando is None:
        return len(serie) - 1
    for i in range(len(serie) - 1, -1, -1):
        if serie.datas[i] <= quando:
            return i
    return None


def _correlacao(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 2 or n != len(b):
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / sqrt(va * vb)


def _correlacao_media(series: list[SeriePrecos], inicio: dt.date,
                      fim: dt.date) -> float | None:
    """Correlação média par a par dos retornos diários no intervalo.

    Alinha por data em comum em vez de por posição. Juntar duas séries pela
    posição num quadro ordenado não levanta erro e inverte a conclusão -- é um
    defeito que este repositório já registrou.
    """
    retornos: dict[str, dict[dt.date, float]] = {}
    for s in series:
        mapa: dict[dt.date, float] = {}
        for i in range(1, len(s.datas)):
            d = s.datas[i]
            if d < inicio or d > fim:
                continue
            anterior = s.fechamentos[i - 1]
            if anterior > 0:
                mapa[d] = s.fechamentos[i] / anterior - 1.0
        if len(mapa) >= OBSERVACOES_MINIMAS_CORRELACAO:
            retornos[s.simbolo] = mapa

    simbolos = sorted(retornos)[:MAXIMO_ATIVOS_CORRELACAO]
    if len(simbolos) < 2:
        return None

    valores: list[float] = []
    for i, x in enumerate(simbolos):
        for y in simbolos[i + 1:]:
            comuns = sorted(retornos[x].keys() & retornos[y].keys())
            if len(comuns) < OBSERVACOES_MINIMAS_CORRELACAO:
                continue
            c = _correlacao([retornos[x][d] for d in comuns],
                            [retornos[y][d] for d in comuns])
            if c is not None:
                valores.append(c)
    return sum(valores) / len(valores) if valores else None


def _razao_de_volume(series: list[SeriePrecos], i_fim_por_simbolo: dict[str, int],
                     janela: int, referencia: int) -> float | None:
    """Mediana, entre símbolos, da razão volume recente / volume de referência.

    Mediana das razões por símbolo, e não razão dos volumes somados: a soma
    salta quando a cobertura de símbolos muda, e o salto de cobertura viraria
    "liquidez secou". A razão por símbolo é imune a isso.
    """
    razoes: list[float] = []
    for s in series:
        i_fim = i_fim_por_simbolo.get(s.simbolo)
        if i_fim is None or not s.volumes:
            continue
        recentes = [v for v in s.volumes[max(0, i_fim - janela + 1):i_fim + 1]
                    if v is not None and v > 0]
        base_ini = max(0, i_fim - janela - referencia + 1)
        base = [v for v in s.volumes[base_ini:max(0, i_fim - janela + 1)]
                if v is not None and v > 0]
        if len(recentes) < 3 or len(base) < REFERENCIA_MINIMA // 4:
            continue
        mediana_base = median(base)
        if mediana_base > 0:
            razoes.append((sum(recentes) / len(recentes)) / mediana_base)
    return median(razoes) if razoes else None


def _dispersao(series: list[SeriePrecos], i_fim_por_simbolo: dict[str, int],
               janela: int) -> float | None:
    """Desvio-padrão, entre símbolos, do retorno da janela.

    Mede se os ativos relacionados se moveram juntos ou cada um para um lado --
    a especificação pede "comportamento de ativos relacionados", e dispersão é a
    forma medível dessa pergunta.
    """
    retornos: list[float] = []
    for s in series:
        i_fim = i_fim_por_simbolo.get(s.simbolo)
        if i_fim is None or i_fim - janela < 0:
            continue
        r = s.retorno(i_fim - janela, janela)
        if r is not None:
            retornos.append(r)
    n = len(retornos)
    if n < 3:
        return None
    m = sum(retornos) / n
    return sqrt(sum((r - m) ** 2 for r in retornos) / (n - 1))


def medir(series, *, quando: dt.date | None = None,
          janela: int = JANELA_CURTA, referencia: int = JANELA_REFERENCIA,
          minimo_ativos: int = 20) -> Medicao:
    """Calcula os indicadores de mercado que as séries disponíveis sustentam.

    Args:
        series: iterável (ou dicionário) de :class:`SeriePrecos` com volume.
        quando: data da avaliação. ``None`` usa o fim das séries.
        janela: pregões da janela curta.
        referencia: pregões da janela de referência, que termina onde a curta
            começa -- nunca a inclui.
        minimo_ativos: piso de papéis por dia para o índice sintético existir.

    Returns:
        Uma :class:`Medicao`. Indicador sem fonte ou sem janela válida sai
        ``None``, jamais ``0,0``.
    """
    lista = sorted((series.values() if isinstance(series, dict) else series or ()),
                   key=lambda s: s.simbolo)
    lista = [s for s in lista if not s.vazia]

    medicoes: dict[str, float | None] = {c: None for c in ev.CORTES_DE_MERCADO}
    fontes: dict[str, str] = {}
    limitacoes: list[str] = [
        "sem fonte para " + ", ".join(SEM_FONTE_HOJE)
        + ": indicadores não medidos (não são zero)"
    ]

    if not lista:
        limitacoes.append("nenhuma série de preços disponível: "
                          "evidência de mercado não medida")
        return Medicao(medicoes, fontes, tuple(limitacoes), None, 0)

    indice = bmk.indice_equiponderado(lista, nome="mercado",
                                      minimo_ativos=minimo_ativos)
    if indice.vazia:
        limitacoes.append(
            f"nenhum pregão com {minimo_ativos}+ papéis: índice de referência "
            "não construído")
        return Medicao(medicoes, fontes, tuple(limitacoes), None, len(lista))

    i_fim = _indice_ate(indice, quando)
    if i_fim is None:
        limitacoes.append("séries não alcançam a data da avaliação: "
                          "evidência de mercado não medida")
        return Medicao(medicoes, fontes, tuple(limitacoes), None, len(lista))

    ate = indice.datas[i_fim]
    hoje = quando or ate
    defasagem = (hoje - ate).days
    if defasagem > DEFASAGEM_MAXIMA_DIAS:
        # Mercado fechado ou série parada. Não medir é a resposta certa: o
        # silêncio dos preços não contradiz manchete nenhuma.
        limitacoes.append(
            f"último pregão em {ate:%d/%m/%Y}, {defasagem} dias atrás: mercado "
            "fechado ou série desatualizada, evidência de mercado não medida")
        return Medicao(medicoes, fontes, tuple(limitacoes), ate, len(lista))

    fonte_indice = indice.fonte or "índice equiponderado"
    i_fim_por_simbolo = {s.simbolo: _indice_ate(s, ate) for s in lista}
    i_fim_por_simbolo = {k: v for k, v in i_fim_por_simbolo.items() if v is not None}

    # ── Queda do índice na janela curta, já orientada ─────────────────────────
    if i_fim - janela >= 0:
        r = indice.retorno(i_fim - janela, janela)
        if r is None:
            limitacoes.append(
                f"janela de {janela} pregões reprovada no portão de densidade: "
                "queda do índice não medida")
        else:
            # Só queda é severidade. Alta do índice é 0,0 medido -- calmo, não
            # ausente -- e não pode virar 1,0 pelo módulo do movimento.
            medicoes["indices"] = max(0.0, -r)
            fontes["indices"] = fonte_indice
    else:
        limitacoes.append(f"índice tem menos de {janela} pregões: "
                          "queda do índice não medida")

    # ── Volatilidade curta contra a de referência ─────────────────────────────
    inicio_curta = i_fim - janela
    inicio_ref = inicio_curta - referencia
    if janela >= PREGOES_MINIMOS_VOLATILIDADE and inicio_ref >= 0:
        vol_curta = _volatilidade_realizada(
            indice.retornos_diarios(inicio_curta, i_fim))
        # A referência TERMINA onde a curta começa. Incluir a janela curta aqui
        # deixaria o choque inflar o próprio denominador e sair amortecido.
        vol_ref = _volatilidade_realizada(
            indice.retornos_diarios(inicio_ref, inicio_curta))
        if vol_curta is not None and vol_ref is not None and vol_ref > 0:
            medicoes["volatilidade"] = vol_curta / vol_ref
            fontes["volatilidade"] = fonte_indice
        else:
            limitacoes.append("volatilidade de referência nula ou não "
                              "calculável: razão de volatilidade não medida")
    else:
        limitacoes.append(
            f"histórico insuficiente ({referencia} pregões de referência "
            f"após a janela curta): razão de volatilidade não medida")

    # ── Liquidez: queda do volume contra a mediana ────────────────────────────
    razao = _razao_de_volume(lista, i_fim_por_simbolo, janela, referencia)
    if razao is None:
        limitacoes.append("volume ausente ou histórico curto: "
                          "queda de liquidez não medida")
    else:
        medicoes["liquidez"] = max(0.0, 1.0 - razao)
        fontes["liquidez"] = "volume negociado das séries do painel"

    # ── Correlação: quanto ela subiu contra a referência ──────────────────────
    corte = indice.data_em(inicio_curta) if inicio_curta >= 0 else None
    corte_ref = indice.data_em(max(0, inicio_ref)) if inicio_ref >= 0 else None
    if corte is not None and corte_ref is not None:
        atual = _correlacao_media(lista, corte, ate)
        base = _correlacao_media(lista, corte_ref, corte)
        if atual is None or base is None:
            limitacoes.append("pares insuficientes com datas em comum: "
                              "aumento de correlação não medido")
        else:
            # Só o aumento conta. Correlação que cai é diversificação
            # funcionando, e não pode entrar como estresse pelo módulo.
            medicoes["correlacao"] = max(0.0, atual - base)
            fontes["correlacao"] = f"{len(lista)} séries do painel"
    else:
        limitacoes.append("histórico insuficiente para comparar correlações")

    # ── Dispersão entre ativos relacionados ───────────────────────────────────
    disp = _dispersao(lista, i_fim_por_simbolo, janela)
    if disp is None:
        limitacoes.append("menos de 3 séries com janela válida: "
                          "dispersão entre relacionados não medida")
    else:
        medicoes["relacionados"] = disp
        fontes["relacionados"] = f"{len(lista)} séries do painel"

    logger.debug("evidência de mercado até %s: %s medidos de %s",
                 ate, sum(1 for v in medicoes.values() if v is not None),
                 len(medicoes))
    return Medicao(medicoes, fontes, tuple(limitacoes), ate, len(lista))
