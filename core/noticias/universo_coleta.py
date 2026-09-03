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
"""
from __future__ import annotations

import logging

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


def montar(modo: str, *, engine=None, limite: int = LIMITE_TICKERS
           ) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Universo do modo e as limitações que ele impôs.

    Devolve ``((tickers...), (limitacoes...))``. Tupla vazia de tickers é
    consulta ampla legítima no modo normal e é ausência de alvo nos outros --
    quem lê distingue pelas limitações, que dizem qual dos dois aconteceu.
    """
    alvos = cad.PRIORIDADES.get(modo, cad.PRIORIDADES[cad.MODO_NORMAL])
    tickers: list[str] = []
    limitacoes: list[str] = []

    if cad.ALVO_CARTEIRA in alvos:
        achados, motivo = da_carteira(engine=engine)
        tickers.extend(achados)
        if motivo:
            limitacoes.append(f"carteira: {motivo}")

    if cad.ALVO_CANDIDATOS in alvos and len(tickers) < limite:
        achados, motivo = dos_candidatos(engine=engine,
                                         excluir=tuple(tickers))
        tickers.extend(achados)
        if motivo:
            limitacoes.append(f"candidatos: {motivo}")

    if len(tickers) > limite:
        limitacoes.append(
            f"universo truncado em {limite} de {len(tickers)} ativos "
            f"(cota dos provedores); a prioridade do modo {modo} decidiu quem "
            f"ficou")
        tickers = tickers[:limite]

    if not tickers and cad.ALVO_MERCADO not in alvos:
        limitacoes.append(
            f"modo {modo} não cobre mercado amplo e nenhum ativo foi resolvido: "
            f"a consulta sai sem foco")

    return tuple(tickers), tuple(limitacoes)
