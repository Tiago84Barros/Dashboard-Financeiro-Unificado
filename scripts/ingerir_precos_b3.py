"""Ingere preco diario de acoes da B3 (COTAHIST, BDI 02) NO ARMAZEM LOCAL.

Motivo e limites estao em `data_pipeline/market/b3_precos.py`. Em resumo: a
serie de acoes da B3 que o armazem tinha era pontual (1.542 datas em 26 anos) e
os horizontes curtos da Memoria de Mercado saiam nao medidos por causa dela.

Uso
---
    python scripts/ingerir_precos_b3.py                      # simula, nao grava
    python scripts/ingerir_precos_b3.py --apply --cache-apenas
    python scripts/ingerir_precos_b3.py --apply --anos 2026

`--cache-apenas` usa os ZIPs ja baixados em `local_staging/fii_b3_cotahist/`
(584 MB em disco desde julho) e nao toca a rede. Sem ele, o ano corrente é
rebaixado da B3, que é o que mantem a serie fresca.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_pipeline.market import b3_cotahist, b3_precos  # noqa: E402
from data_pipeline.market.fii_b3_history import CACHE_ROOT  # noqa: E402
from scripts.construir_memoria_mercado import warehouse_url  # noqa: E402

logger = logging.getLogger("b3.precos")


def _anos_do_argumento(bruto: str | None) -> range:
    corrente = datetime.now(timezone.utc).year
    if not bruto:
        return range(b3_precos.PRIMEIRO_ANO, corrente + 1)
    if "-" in bruto:
        inicio, fim = bruto.split("-", 1)
        return range(int(inicio), int(fim) + 1)
    ano = int(bruto)
    return range(ano, ano + 1)


def simular(anos: range) -> dict:
    """Le os ZIPs do cache e conta, sem abrir conexao de escrita."""
    relatorio = {"anos": [], "linhas": 0, "ausentes": []}
    for ano in anos:
        caminho = CACHE_ROOT / f"COTAHIST_A{ano}.ZIP"
        if not caminho.exists():
            relatorio["ausentes"].append(ano)
            continue
        linhas = b3_precos.preparar_linhas(
            b3_cotahist.ler_linhas(caminho.read_bytes()))
        fora_de_um = sum(1 for linha in linhas
                         if (linha.get("fator_cotacao") or 1) != 1)
        relatorio["anos"].append({
            "ano": ano, "linhas": len(linhas),
            "tickers": len({linha["ticker"] for linha in linhas}),
            "pregoes": len({linha["trade_date"] for linha in linhas}),
            "fator_diferente_de_um": fora_de_um,
        })
        relatorio["linhas"] += len(linhas)
        logger.info("%s: %s linhas", ano, len(linhas))
    return relatorio


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="grava de fato; sem isso apenas simula")
    parser.add_argument("--anos", default=None,
                        help="ano unico (2026) ou intervalo (2010-2026)")
    parser.add_argument("--cache-apenas", action="store_true",
                        help="usa so os ZIPs ja baixados, sem tocar a rede")
    args = parser.parse_args()
    anos = _anos_do_argumento(args.anos)

    if not args.apply:
        relatorio = simular(anos)
        logger.info("SIMULACAO (nada gravado): %s",
                    json.dumps(relatorio, ensure_ascii=False, sort_keys=True))
        return 0

    engine = create_engine(warehouse_url(), pool_pre_ping=True)
    b3_precos.exigir_local(engine)
    relatorio = b3_precos.ingerir(engine, anos=anos,
                                  cache_apenas=args.cache_apenas)
    with engine.connect() as conn:
        relatorio["no_armazem"] = dict(conn.execute(text("""
            SELECT count(*) AS linhas, count(DISTINCT trade_date) AS pregoes,
                   count(DISTINCT ticker) AS tickers,
                   min(trade_date)::text AS de, max(trade_date)::text AS ate
              FROM market.b3_security_history
        """)).mappings().one())
    logger.info("%s", json.dumps(relatorio, ensure_ascii=False,
                                 sort_keys=True, default=str))
    return 0 if relatorio["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
