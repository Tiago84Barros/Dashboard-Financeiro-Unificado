"""Regrava como ``stock`` as ações americanas cadastradas como ``etf``.

Por que existe
--------------
O importador de PDF da Nomad (`data_pipeline/importers/investments/nomad_pdf.py`)
gravava todo ativo como ``etf``, e ``get_or_create_asset`` nunca reescreve a
classe de um ativo que já existe. MELI, AAPL e qualquer ação comprada na Nomad
ficaram ``etf`` em ``assets.class``. O importador foi corrigido e a tela de
investimentos já classifica pelo ticker e pelo nome
(`core/classe_exterior.py`); este script corrige o cadastro, que ainda é lido
cru por outras telas.

Alvo (todas as condições)
-------------------------
* ``currency = 'USD'`` e ``class = 'etf'``;
* ``classe_ativo_usd(ticker, nome, None)`` diz ``stock_us`` -- o cadastro não
  entra no voto, porque é ele que está errado. Ativo sem sinal de ação
  (ticker fora do universo da SEC e nome sem sufixo de empresa) fica ETF.

Só existe o sentido ``etf`` → ``stock``. O inverso não é tocado: um
``stock`` em dólar fora do universo publicado pode ser ação legítima.

Idempotente: depois de regravada, a linha deixa de ser ``class = 'etf'``; o
UPDATE repete a guarda. Ao gravar, imprime o SQL que desfaz a correção.

Uso (raiz do projeto)::

    python scripts/corrige_classe_acoes_eua.py           # só mostra
    python scripts/corrige_classe_acoes_eua.py --apply   # grava
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.classe_exterior import (  # noqa: E402
    ACAO_EUA,
    classe_ativo_usd,
    universo_acoes_eua,
)

SQL_CANDIDATAS = """
SELECT id::text AS id, ticker, name
FROM assets
WHERE currency = 'USD' AND class = 'etf'
ORDER BY ticker
"""

SQL_REGRAVA = """
UPDATE assets
SET class = 'stock'
WHERE id = ANY(CAST(:ids AS uuid[]))
  AND currency = 'USD'
  AND class = 'etf'
"""


def separar(linhas: list[dict], universo: frozenset[str] | set[str]
            ) -> tuple[list[dict], list[dict]]:
    """(ações a regravar, ETFs que ficam como estão)."""
    acoes, etfs = [], []
    for linha in linhas:
        classe = classe_ativo_usd(linha["ticker"], linha["name"], None, universo)
        (acoes if classe == ACAO_EUA else etfs).append(linha)
    return acoes, etfs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="grava a nova classe (sem isso, só mostra)")
    args = parser.parse_args(argv)
    try:  # console do Windows em cp1252 estraga os acentos
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    universo = universo_acoes_eua()
    if not universo:
        print("Universo de ações EUA vazio (data/public/valuation_historico.json.gz "
              "ausente?). Sem ele a decisão fica só no nome; abortando.")
        return 1

    from sqlalchemy import text

    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        print("DATABASE_URL não configurada.")
        return 1
    with engine.connect() as conn:
        linhas = [dict(r._mapping) for r in conn.execute(text(SQL_CANDIDATAS))]

    acoes, etfs = separar(linhas, universo)
    print(f"Ativos em USD cadastrados como etf: {len(linhas)}\n")
    print(f"Viram stock ({len(acoes)}):")
    for a in acoes:
        print(f"  {a['ticker']:<8} {a['name']}")
    print(f"\nFicam etf ({len(etfs)}):")
    for e in etfs:
        print(f"  {e['ticker']:<8} {e['name']}")

    if not acoes:
        print("\nNada a corrigir.")
        return 0
    if not args.apply:
        print("\nSimulação. Rode com --apply para gravar.")
        return 0

    with engine.begin() as conn:
        n = conn.execute(text(SQL_REGRAVA), {"ids": [a["id"] for a in acoes]}).rowcount
    tickers = ", ".join(f"'{a['ticker']}'" for a in acoes)
    print(f"\nRegravados: {n}.")
    print(f"Para desfazer: UPDATE assets SET class = 'etf' WHERE ticker IN ({tickers});")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
