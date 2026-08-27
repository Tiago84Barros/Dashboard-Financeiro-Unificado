# -*- coding: utf-8 -*-
"""Popula o cache local do universo de acoes deslistadas da B3 (A-137).

Por que existe um script em vez de a tela baixar sozinha: a Saude dos Dados
renderiza `core.b3_validation.build_data_manifest`, e tela que baixa arquivo
fica refem da rede do usuario. Aqui o download acontece uma vez, offline em
relacao ao app, e a tela so le o cache.

Duas fontes publicas e gratuitas da CVM, ja implementadas no repositorio:

  * `cad_cia_aberta.csv` -- companhias com registro CANCELADO (o evento)
  * FCA `valor_mobiliario` por ano -- CNPJ -> Codigo_Negociacao (a identidade)

A juncao das duas e o que faltava: o cadastro sabe QUEM saiu, o FCA sabe COMO
o papel se chamava na bolsa. Nenhuma das duas resolve sozinha.

Uso:
    python scripts/atualizar_universo_deslistadas.py
    python scripts/atualizar_universo_deslistadas.py --ano-final 2026 --json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.survivorship_ingestion import (  # noqa: E402
    _anos_fca,
    load_cvm_cancelamentos,
    load_cvm_cancelamentos_raw,
    load_fca_aliases,
    resumo_ingestao,
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ano-final", type=int, default=None,
                   help="ultimo ano do FCA a varrer (padrao: ano corrente)")
    p.add_argument("--json", action="store_true", help="saida em JSON")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    anos = _anos_fca(args.ano_final)
    print(f"FCA: varrendo {anos.start}..{anos.stop - 1} "
          f"({len(anos)} formularios anuais)", file=sys.stderr)
    aliases = load_fca_aliases(anos=anos)
    cancelados = load_cvm_cancelamentos_raw()
    mapeados = load_cvm_cancelamentos()
    resumo = resumo_ingestao(incluir_cvm=True, permitir_download=False)

    saida = {
        "fca_aliases": len(aliases),
        "fca_tickers_unicos": len({a["ticker"] for a in aliases}),
        "cvm_registros_cancelados": len(cancelados),
        "cvm_mapeados_para_ticker": len(mapeados),
        "universo_total_unico": resumo["total_unicos"],
        "curados": resumo["curados"],
    }
    if args.json:
        print(json.dumps(saida, indent=2, ensure_ascii=False))
        return 0

    for chave, valor in saida.items():
        print(f"  {chave:28s} {valor}")
    if not aliases:
        print("\nNenhum alias do FCA: sem rede ou CVM fora do ar. "
              "O universo continua o curado.", file=sys.stderr)
        return 1
    if not mapeados:
        print("\nAliases obtidos, mas nenhum cancelamento casou com ticker. "
              "Verifique o cadastro em data/cache/cvm/.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
