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

O cache fica em `data/cache/`, que e gitignored -- entao a Streamlit Cloud, que
roda a partir do repositorio, leria zero deslistadas. Por isso o script tambem
exporta o universo ja resolvido para `data_imports/delisted/`, que
`load_all_local` consome sem rede e sem cache. E o derivado que viaja no git,
nao o CSV bruto da CVM.

Uso:
    python scripts/atualizar_universo_deslistadas.py
    python scripts/atualizar_universo_deslistadas.py --ano-final 2026 --json
    python scripts/atualizar_universo_deslistadas.py --sem-exportar
"""
from __future__ import annotations

import argparse
import csv
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

EXPORT_DEFAULT = "data_imports/delisted/cvm_cancelamentos_fca.csv"


def exportar(delisted, destino: Path | str) -> int:
    """Grava o universo resolvido no formato que `load_from_csv` ja le.

    Ordenado por ticker para que reexecutar sem mudanca de dado produza diff
    vazio -- o arquivo e versionado, e churn diario no git esconderia o que
    realmente mudou.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    linhas = sorted(delisted, key=lambda d: d.ticker)
    with destino.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "nome", "data_delisting", "motivo", "ultimo_preco"])
        for d in linhas:
            w.writerow([d.ticker, d.nome, d.data_delisting.isoformat(),
                        d.motivo, d.ultimo_preco])
    return len(linhas)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ano-final", type=int, default=None,
                   help="ultimo ano do FCA a varrer (padrao: ano corrente)")
    p.add_argument("--json", action="store_true", help="saida em JSON")
    p.add_argument("--sem-exportar", action="store_true",
                   help="nao regravar data_imports/delisted/cvm_cancelamentos_fca.csv")
    p.add_argument("--saida", default=EXPORT_DEFAULT,
                   help=f"arquivo exportado (padrao: {EXPORT_DEFAULT})")
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

    exportados = 0
    if not args.sem_exportar and mapeados:
        exportados = exportar(mapeados, args.saida)

    saida = {
        "exportados_para_o_repo": exportados,
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
