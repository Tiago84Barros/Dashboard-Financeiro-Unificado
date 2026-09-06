"""Publica o calendário de pregão OBSERVADO, a partir do armazém local.

Por que este script existe
--------------------------
``core/pregao.py`` contava pregão como "segunda a sexta" e declarava a ausência
de feriado como lacuna consciente, com a alternativa nomeada no próprio
docstring: *tabela de feriados embutida no código envelhece em silêncio e passa
a mentir com a mesma cara de quem acerta*. O projeto já viveu isso -- ver
``memoria: aviso-que-envelhece-invertido``.

A saída não é uma tabela escrita à mão: é o **complemento observado** da série
de preços. Dia útil em que a bolsa inteira não negociou nenhum papel não é
opinião sobre o calendário, é o calendário. Medido em 06/09/2026:

* B3   -- 213 dias úteis ausentes entre 2010-01-04 e 2026-09-01 (~12,8/ano)
* NYSE -- 156 dias úteis ausentes entre 2010-01-04 e 2026-08-20 (~9,3/ano)

Grava-se o **complemento** (os feriados), e não os pregões, por dois motivos:
são 369 datas em vez de 8 mil, e a semântica do artefato fica explícita -- dia
útil menos feriado, dentro da janela coberta.

A armadilha que o guarda evita
------------------------------
Ausência de linha tem duas causas possíveis, e elas são opostas: **feriado** e
**ingestão truncada**. Derivar feriado de ausência sem piso de cobertura é
exatamente o erro de ``memoria: foto-truncada-vira-evidencia``, onde 636
encerramentos de FII que nunca houve foram gravados a partir de uma foto
parcial. Um feriado é isolado ou emenda um fim de semana; uma ingestão faltando
grava semanas inteiras. Por isso o script **recusa publicar** quando encontra
sequência de mais de três dias úteis seguidos sem negócio, e quando um ano
completo sai fora da faixa plausível de feriados.

Uso (simulação por omissão, como o resto do projeto)
----------------------------------------------------
    python scripts/publicar_calendario_pregao.py
    python scripts/publicar_calendario_pregao.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: E402

from sqlalchemy import text  # noqa: E402

from core.pregao import ARTEFATO_CALENDARIO  # noqa: E402

logger = logging.getLogger("calendario_pregao")

#: De onde sai cada praça. A tabela é a série **bruta** de negócios, e não a
#: ajustada: preço ajustado é reescrito por evento corporativo e pode existir
#: em data que não teve pregão. Ver ``memoria:
#: armazem-local-nao-tem-preco-diario-da-b3``.
FONTES = {
    "B3": ("market.b3_security_history", "trade_date"),
    "NYSE": ("market_us.prices_daily", "date"),
}

#: Faixa plausível de feriados de bolsa num ano completo. Fora disso não se
#: publica: ou a ingestão está furada, ou o calendário mudou de natureza -- e
#: nos dois casos alguém precisa olhar antes de o número virar decisão.
FERIADOS_POR_ANO = (5, 20)

#: Maior emenda de dias úteis que ainda pode ser feriado. Carnaval e recessos
#: emendam no máximo dois; três dá folga. Acima disso é buraco de ingestão se
#: passando por feriado.
MAX_DIAS_UTEIS_SEGUIDOS = 3

#: A janela começa aqui, e não no mínimo da tabela: ``market_us.prices_daily``
#: tem preço desde 1962, mas com um punhado de símbolos -- um "feriado" de 1970
#: seria ausência de cobertura, não ausência de pregão.
INICIO = date(2010, 1, 4)


def _dias_negociados(conn, tabela: str, coluna: str, desde: date) -> set[date]:
    linhas = conn.execute(text(
        f"SELECT DISTINCT {coluna} AS d FROM {tabela} WHERE {coluna} >= :desde"
    ), {"desde": desde})
    return {r[0] for r in linhas}


def _sequencias(dias: list[date]) -> list[list[date]]:
    """Agrupa dias úteis consecutivos (fim de semana não quebra a sequência)."""
    if not dias:
        return []
    grupos, atual = [], [dias[0]]
    for anterior, seguinte in zip(dias, dias[1:]):
        so_fim_de_semana = all(
            (anterior + timedelta(i)).weekday() >= 5
            for i in range(1, (seguinte - anterior).days))
        if so_fim_de_semana:
            atual.append(seguinte)
        else:
            grupos.append(atual)
            atual = [seguinte]
    grupos.append(atual)
    return grupos


def apurar(conn, praca: str) -> dict:
    """Feriados observados de uma praça, com as recusas já avaliadas."""
    tabela, coluna = FONTES[praca]
    negociados = _dias_negociados(conn, tabela, coluna, INICIO)
    if not negociados:
        raise ValueError(f"{praca}: {tabela} nao devolveu nenhuma data")

    fim = max(negociados)
    ausentes, dia = [], INICIO
    while dia <= fim:
        if dia.weekday() < 5 and dia not in negociados:
            ausentes.append(dia)
        dia += timedelta(days=1)

    recusas = []
    longas = [g for g in _sequencias(ausentes)
              if len(g) > MAX_DIAS_UTEIS_SEGUIDOS]
    if longas:
        recusas.append(
            f"{len(longas)} sequencia(s) de mais de {MAX_DIAS_UTEIS_SEGUIDOS} "
            f"dias uteis seguidos sem negocio -- ingestao truncada, nao feriado "
            f"(primeira: {longas[0][0]} a {longas[0][-1]})")

    por_ano = Counter(d.year for d in ausentes)
    # Ano incompleto na ponta não entra na checagem: 2026 até setembro tem 7
    # feriados e isso não é anomalia, é o ano que ainda não acabou.
    completos = {a: n for a, n in por_ano.items()
                 if a > INICIO.year and (a < fim.year or fim.month == 12)}
    fora = {a: n for a, n in completos.items()
            if not FERIADOS_POR_ANO[0] <= n <= FERIADOS_POR_ANO[1]}
    if fora:
        recusas.append(
            f"anos com feriados fora da faixa {FERIADOS_POR_ANO}: {fora}")

    return {"inicio": INICIO.isoformat(), "fim": fim.isoformat(),
            "fonte": tabela, "pregoes_observados": len(negociados),
            "feriados": [d.isoformat() for d in ausentes],
            "por_ano": dict(sorted(por_ano.items())), "recusas": recusas}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="grava o artefato; sem isso apenas simula")
    args = ap.parse_args()

    from sqlalchemy import create_engine

    from scripts.publish_fii_selection_from_local import _warehouse_url

    engine = create_engine(_warehouse_url())
    saida, recusas = {}, []
    try:
        with engine.connect() as conn:
            for praca in FONTES:
                apurado = apurar(conn, praca)
                recusas.extend(f"{praca}: {m}" for m in apurado.pop("recusas"))
                saida[praca] = apurado
                anos = max(1, len(apurado["por_ano"]))
                logger.info(
                    "%-5s %s a %s | %d pregoes | %d feriados (%.1f/ano)",
                    praca, apurado["inicio"], apurado["fim"],
                    apurado["pregoes_observados"], len(apurado["feriados"]),
                    len(apurado["feriados"]) / anos)
    finally:
        engine.dispose()

    if recusas:
        for mensagem in recusas:
            logger.error("RECUSADO -- %s", mensagem)
        return 1

    if not args.apply:
        logger.info("\nsimulacao: nada gravado. Use --apply para publicar em %s",
                    ARTEFATO_CALENDARIO)
        return 0

    ARTEFATO_CALENDARIO.parent.mkdir(parents=True, exist_ok=True)
    ARTEFATO_CALENDARIO.write_text(
        json.dumps({"gerado_em": date.today().isoformat(), "pracas": saida},
                   indent=1, sort_keys=True) + "\n", encoding="utf-8")
    logger.info("\ngravado: %s", ARTEFATO_CALENDARIO)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
