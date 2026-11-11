"""Publica no Supabase o slot de cotação das ações (market.b3_cotacao_atual).

O armazém local recebe o fechamento do COTAHIST por `run_market_ingest.py
acoes-cotahist` (migration 089). A vitrine precisa dele para o selo de
frescor e para o dossiê B3, que leem o preço pelo mesmo leitor único
(`data_pipeline.market.cotacao_pregao.preco_vigente`).

Regras:

- Só publica ticker que a vitrine já conhece em `market.assets` (como o
  publicador da brapi): o armazém tem fósseis de alias. Os retidos saem
  nomeados em `tickers_retidos`.
- Linhas pequenas (centenas de linhas, dezenas de KB): sem teto de disco.
- Upsert por ticker com guarda `IS DISTINCT FROM`: reenviar não reescreve.
  Só avança, nunca recua: o `price_at` da vitrine mais novo que o do armazém
  fica como está.
- Tabela ausente em qualquer lado (migration 089 não aplicada) NÃO vira
  "sem preço": o resultado diz qual lado e qual migration falta, e sai 0.
- DRY-RUN por omissão; só `--apply` escreve.

    python scripts/publish_b3_cotacao_from_local.py            # simula
    python scripts/publish_b3_cotacao_from_local.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import text  # noqa: E402

load_dotenv(ROOT / ".env")

TABELA = "market.b3_cotacao_atual"
MIGRATION = "089_market_b3_cotacao_atual.sql"
COLUNAS = ("ticker", "price", "price_at", "trade_date", "price_source")

_UPSERT = text(f"""
    INSERT INTO {TABELA} (ticker, price, price_at, trade_date, price_source)
    VALUES (:ticker, :price, :price_at, :trade_date, :price_source)
    ON CONFLICT (ticker) DO UPDATE SET
        price = EXCLUDED.price, price_at = EXCLUDED.price_at,
        trade_date = EXCLUDED.trade_date, price_source = EXCLUDED.price_source
    WHERE {TABELA}.price_at < EXCLUDED.price_at
      AND ({TABELA}.price, {TABELA}.trade_date, {TABELA}.price_source)
          IS DISTINCT FROM (EXCLUDED.price, EXCLUDED.trade_date, EXCLUDED.price_source)""")


def _existe(conn) -> bool:
    return bool(conn.execute(text(f"SELECT to_regclass('{TABELA}') IS NOT NULL")).scalar())


def selecionar(linhas: list[dict], conhecidos: set[str]) -> tuple[list[dict], list[str]]:
    """Separa o que a vitrine conhece do que fica retido (nomeado)."""
    ok = [r for r in linhas if str(r["ticker"]).upper() in conhecidos]
    retidos = sorted({str(r["ticker"]).upper() for r in linhas} - conhecidos)
    return ok, retidos


def publish(*, apply: bool = False, source=None, target=None) -> dict:
    if source is None or target is None:
        from scripts.publish_b3_tickers_from_local import _remote_url
        from scripts.publish_fii_selection_from_local import _warehouse_url
        from scripts.publish_us_snapshot import _engine
        remote_url = _remote_url()
        if not remote_url:
            raise RuntimeError("Supabase não configurado — defina SUPABASE_DB_URL no .env")
        source = source or _engine(_warehouse_url())
        target = target or _engine(remote_url)
    saida: dict = {"modo": "APLICADO" if apply else "SIMULACAO (nada foi escrito)",
                   "publicados": 0, "tickers_retidos": []}
    with source.connect() as src:
        if not _existe(src):
            saida["status"] = "tabela_ausente_no_armazem"
            saida["migration"] = MIGRATION
            return saida
        linhas = [dict(zip(COLUNAS, r)) for r in src.execute(text(
            f"SELECT {', '.join(COLUNAS)} FROM {TABELA} ORDER BY ticker")).fetchall()]
    with target.connect() as dst:
        if not _existe(dst):
            saida["status"] = "tabela_ausente_na_vitrine"
            saida["migration"] = MIGRATION
            saida["candidatos"] = len(linhas)
            return saida
        conhecidos = {str(r[0]).upper() for r in dst.execute(text(
            "SELECT ticker FROM market.assets WHERE asset_type IN ('stock','unit')"))}
    ok, saida["tickers_retidos"] = selecionar(linhas, conhecidos)
    saida.update(status="completed", candidatos=len(linhas), a_publicar=len(ok))
    if apply and ok:
        with target.begin() as dst:
            for r in ok:
                saida["publicados"] += dst.execute(_UPSERT, r).rowcount or 0
    return saida


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="grava de fato")
    args = p.parse_args(argv)
    saida = publish(apply=bool(args.apply))
    print(json.dumps(saida, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
