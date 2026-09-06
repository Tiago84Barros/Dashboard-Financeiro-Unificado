"""Que ativos a coleta cobre em cada modo, na ordem em que eles importam.

A prioridade sai de ``cadencia.PRIORIDADES``; aqui só se resolve **quem** é cada
alvo:

``carteira``     o que o usuário de fato detém, dos snapshots ativos.
``candidatos``   o que ele estuda comprar -- os snapshots não ativos.
``mercado_amplo`` nenhum ticker: a consulta vai ampla, de propósito.

Nada aqui levanta. Carteira ilegível devolve tupla vazia com o motivo escrito, e
o job segue para o alvo seguinte. Uma coleta que aborta porque o portfólio não
carregou trocaria uma degradação por um apagão.

Truncar é decisão, e ela é declarada
------------------------------------
Cada provedor tem cota, e uma consulta com 80 tickers não custa o mesmo que uma
com 8. ``LIMITE_TICKERS`` corta, mas o corte volta em ``limitacoes`` -- um
universo silenciosamente truncado apresentaria cobertura parcial como completa,
que é exatamente o modo de falha que o requisito nomeia.

O corte gira, e não pode ser alfabético
---------------------------------------
Até 06/09/2026 o corte era ``tickers[:limite]`` sobre uma lista ordenada por
``sorted()``, e a limitação dizia *"a prioridade do modo decidiu quem ficou"*.
Não decidiu: quem decidiu foi o alfabeto. Com 32 ativos na carteira e teto de
20, a medição desse dia mostrou que a consulta saía sempre com ``A..LIFE11`` e
**nunca** com ``PETR4``, ``SBSP3``, ``VIVT3``, ``WEGE3``, ``TJX``, ``PGR``,
``PODD`` -- as posições cujo nome cai na segunda metade do alfabeto não tinham
notícia coletada em ciclo nenhum, desde sempre, de forma determinística. Um
ponto cego estável é pior que um aleatório: ele não aparece na média.

Agora a janela **gira por ciclo** dentro de cada nível de prioridade. A ordem
alfabética continua (ela é o que torna a consulta reproduzível dentro do mesmo
ciclo), mas o ponto de partida anda, e em ``ceil(n/limite)`` ciclos o universo
inteiro foi perguntado. A limitação passa a dizer qual recorte saiu e quantos
faltam -- texto derivado da medição, não da intenção.

Girar **dentro** do nível, e não sobre a lista concatenada, é o que preserva a
prioridade: carteira nunca cede vaga a candidato porque a janela andou.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from core.noticias import cadencia as cad

logger = logging.getLogger(__name__)

#: Teto de tickers por consulta. Vem do limite prático dos provedores, não de
#: uma preferência: acima disso a query é recusada ou truncada pelo lado deles,
#: e aí o corte fica invisível.
LIMITE_TICKERS = 20


def _simbolos(snaps: dict) -> tuple[str, ...]:
    saida = []
    for classe in snaps.values():
        for simbolo in (classe or {}):
            texto = str(simbolo or "").strip().upper()
            if texto and texto not in saida:
                saida.append(texto)
    # Ordem alfabética: a mesma configuração precisa produzir a mesma consulta
    # em execuções diferentes, ou o histórico de ciclos deixa de ser comparável.
    return tuple(sorted(saida))


def da_carteira(*, engine=None) -> tuple[tuple[str, ...], str]:
    """Tickers detidos. Segundo elemento é o motivo, quando vier vazio."""
    try:
        from core.portfolio.registry import asset_classes
        from core.portfolio.repository import load_active_snapshots

        snaps = {c: load_active_snapshots(c, engine=engine)
                 for c in asset_classes()}
    except Exception as exc:  # noqa: BLE001 - alvo ausente não derruba a coleta
        logger.warning("Carteira ilegivel para a coleta: %s", exc)
        return (), f"carteira indisponível ({type(exc).__name__})"

    simbolos = _simbolos(snaps)
    if not simbolos:
        return (), "nenhum snapshot ativo de carteira"
    return simbolos, ""


def dos_candidatos(*, engine=None,
                   excluir: tuple[str, ...] = ()) -> tuple[tuple[str, ...], str]:
    """Tickers estudados e ainda não detidos: os modelos não ativos."""
    try:
        from core.portfolio.registry import asset_classes
        from core.portfolio.repository import active_model_id, load_snapshots
    except Exception as exc:  # noqa: BLE001
        return (), f"candidatos indisponíveis ({type(exc).__name__})"

    fora = {t.upper() for t in excluir}
    achados: list[str] = []
    for classe in asset_classes():
        try:
            ativo = active_model_id(classe, engine=engine)
            if not ativo:
                continue
            snaps = load_snapshots(classe, ativo, engine=engine)
        except Exception as exc:  # noqa: BLE001
            logger.info("Candidatos de %s ilegiveis (%s)", classe, exc)
            continue
        for simbolo in (snaps or {}):
            texto = str(simbolo or "").strip().upper()
            if texto and texto not in fora and texto not in achados:
                achados.append(texto)
    if not achados:
        return (), "nenhum candidato distinto da carteira"
    return tuple(sorted(achados)), ""


def janela_de(modo: str, *, agora: datetime | None = None,
              intervalo_min: float | None = None) -> int:
    """Índice do ciclo corrente, para girar o recorte do universo.

    Derivado do relógio, e não de estado gravado, de propósito: três processos
    que não compartilham disco (runner do Actions, container do Streamlit,
    máquina do desenvolvedor) coletam contra o mesmo teto de cota, e um
    contador por processo faria cada um girar sozinho -- todos partindo do
    mesmo lugar, que é exatamente o ponto cego que esta rotação existe para
    desfazer. O relógio os três compartilham.

    Devolve ``0`` se a cadência não puder ser lida: girar é melhoria, e uma
    melhoria não pode derrubar a coleta.
    """
    if intervalo_min is None:
        try:
            intervalo_min = cad.cadencia(modo).intervalo_min
        except Exception as exc:  # noqa: BLE001 - cadência é detalhe aqui
            logger.info("Cadencia ilegivel para girar o universo (%s)", exc)
            return 0
    if not intervalo_min or intervalo_min <= 0:
        return 0
    momento = agora or datetime.now(timezone.utc)
    return int(momento.timestamp() // (float(intervalo_min) * 60.0))


def _recorte(itens: tuple[str, ...], vagas: int, janela: int) -> tuple[str, ...]:
    """``vagas`` itens a partir do ponto que a janela indica, circularmente."""
    if vagas <= 0 or not itens:
        return ()
    if len(itens) <= vagas:
        return itens
    giros = math.ceil(len(itens) / vagas)
    inicio = (janela % giros) * vagas
    girado = itens[inicio:] + itens[:inicio]
    return girado[:vagas]


def montar(modo: str, *, engine=None, limite: int = LIMITE_TICKERS,
           janela: int | None = None, agora: datetime | None = None
           ) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Universo do modo e as limitações que ele impôs.

    Devolve ``((tickers...), (limitacoes...))``. Tupla vazia de tickers é
    consulta ampla legítima no modo normal e é ausência de alvo nos outros --
    quem lê distingue pelas limitações, que dizem qual dos dois aconteceu.

    ``janela`` é injetável para teste; por omissão sai do relógio, via
    :func:`janela_de`. Duas chamadas dentro do mesmo ciclo devolvem o mesmo
    universo -- a consulta precisa ser reproduzível para o histórico de ciclos
    ser comparável.
    """
    alvos = cad.PRIORIDADES.get(modo, cad.PRIORIDADES[cad.MODO_NORMAL])
    if janela is None:
        janela = janela_de(modo, agora=agora)
    tickers: list[str] = []
    limitacoes: list[str] = []
    niveis: list[tuple[str, tuple[str, ...]]] = []

    if cad.ALVO_CARTEIRA in alvos:
        achados, motivo = da_carteira(engine=engine)
        niveis.append(("carteira", achados))
        if motivo:
            limitacoes.append(f"carteira: {motivo}")

    if cad.ALVO_CANDIDATOS in alvos:
        ja = tuple(s for _, itens in niveis for s in itens)
        if len(ja) >= limite:
            # Nem consulta: a carteira já preencheu o teto e a leitura dos
            # candidatos custaria banco para produzir uma lista que nenhuma
            # vaga receberia. Mas o silêncio vira texto -- quem lê a limitação
            # precisa saber que não houve candidato *e* que não se perguntou.
            limitacoes.append(
                f"candidatos não consultados: a carteira ({len(ja)}) já "
                f"preencheu o teto de {limite} ativos por consulta")
        else:
            achados, motivo = dos_candidatos(engine=engine, excluir=ja)
            niveis.append(("candidatos", achados))
            if motivo:
                limitacoes.append(f"candidatos: {motivo}")

    # A vaga é consumida por nível, na ordem da prioridade: a carteira só cede
    # espaço ao candidato depois de inteira. O giro acontece dentro do nível
    # que não coube, e não sobre a lista concatenada -- girar o concatenado
    # promoveria candidato na frente de posição detida quando a janela andasse.
    for nome, itens in niveis:
        vagas = limite - len(tickers)
        if vagas <= 0:
            if itens:
                limitacoes.append(
                    f"{nome}: {len(itens)} ativos não couberam nesta rodada; "
                    f"a prioridade do modo {modo} deu a vaga ao nível anterior")
            continue
        escolhidos = _recorte(itens, vagas, janela)
        if len(itens) > len(escolhidos):
            giros = math.ceil(len(itens) / vagas)
            limitacoes.append(
                f"{nome} truncada em {len(escolhidos)} de {len(itens)} ativos "
                f"(cota dos provedores): saiu o recorte {janela % giros + 1} de "
                f"{giros}, e a janela gira a cada ciclo -- o universo inteiro é "
                f"perguntado em {giros} ciclos")
        tickers.extend(escolhidos)

    if not tickers and cad.ALVO_MERCADO not in alvos:
        limitacoes.append(
            f"modo {modo} não cobre mercado amplo e nenhum ativo foi resolvido: "
            f"a consulta sai sem foco")

    return tuple(tickers), tuple(limitacoes)
