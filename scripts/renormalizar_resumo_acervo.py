"""Reaplica a normalizacao de entrada ao ``resumo`` ja gravado no acervo.

Por que este script existe
--------------------------
O upsert de ``noticias_itens`` reescreve so o que e derivado -- ``entidades``,
``evento_id``, sentimento. ``titulo`` e ``resumo`` sao gravados uma vez e nunca
mais: sao a evidencia crua, e sobrescrever evidencia a cada coleta apagaria a
capacidade de comparar o que a fonte publicou com o que ela publica hoje.

Essa escolha tem um preco quando a **normalizacao de entrada** muda. Em
05/09/2026, ``core.noticias.normalizacao.sem_rodape_de_feed`` passou a cortar o
rodape que varios CMS anexam a descricao no RSS ("The post X appeared first on
Y"). Item novo passou a chegar limpo; as 21 linhas ja gravadas continuaram com o
rodape, porque o upsert nao toca ``resumo``. O acervo ficou com duas gramaticas
ao mesmo tempo, e a vitrine le o acervo.

Este script fecha essa distancia: passa a normalizacao corrente sobre o que ja
esta gravado e regrava so o que muda. Nao inventa texto -- so remove o que a
funcao de entrada removeria hoje.

Uso, no padrao do projeto (simulacao por omissao):

    python scripts/renormalizar_resumo_acervo.py
    python scripts/renormalizar_resumo_acervo.py --apply
"""
from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import text

logger = logging.getLogger("renormalizar_resumo_acervo")

_LER = text("""
    SELECT id_dedup, resumo
    FROM noticias_itens
    WHERE resumo IS NOT NULL AND resumo <> ''
""")

_GRAVAR = text("""
    UPDATE noticias_itens SET resumo = :resumo WHERE id_dedup = :id_dedup
""")


def _divergentes(conn) -> list[dict]:
    """Linhas cujo ``resumo`` gravado difere do que a entrada produziria hoje."""
    from core.noticias.normalizacao import sem_rodape_de_feed

    fora = []
    for linha in conn.execute(_LER).mappings():
        limpo = sem_rodape_de_feed(linha["resumo"])
        if limpo != linha["resumo"]:
            fora.append({"id_dedup": linha["id_dedup"], "resumo": limpo,
                         "antes": linha["resumo"]})
    return fora


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="grava; sem isto o script so simula")
    ap.add_argument("--amostra", type=int, default=3,
                    help="quantas diferencas exibir por extenso")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from core.noticias.destino import engine_acervo

    motor = engine_acervo()
    if motor is None:
        # Ausencia de configuracao nao e "nada a fazer": e nao ter medido.
        logger.error("acervo local nao configurado -- nada foi lido")
        return 2

    try:
        with motor.connect() as conn:
            fora = _divergentes(conn)
            total = conn.execute(
                text("SELECT COUNT(*) FROM noticias_itens")).scalar()

        logger.info("acervo: %s itens | resumo a renormalizar: %s",
                    total, len(fora))
        for linha in fora[:max(0, args.amostra)]:
            logger.info("  %s\n    antes: %s\n    depois: %s",
                        linha["id_dedup"], linha["antes"][-120:],
                        linha["resumo"][-120:])

        if not fora:
            logger.info("nada a fazer: acervo ja coerente com a entrada")
            return 0
        if not args.apply:
            logger.info("SIMULACAO -- rode com --apply para gravar")
            return 0

        with motor.begin() as conn:
            conn.execute(_GRAVAR, [{"id_dedup": f["id_dedup"],
                                    "resumo": f["resumo"]} for f in fora])
        logger.info("gravado: %s linhas", len(fora))

        with motor.connect() as conn:
            resta = len(_divergentes(conn))
        logger.info("verificacao apos gravar: %s divergentes", resta)
        return 0 if resta == 0 else 1
    finally:
        motor.dispose()


if __name__ == "__main__":
    sys.exit(main())
