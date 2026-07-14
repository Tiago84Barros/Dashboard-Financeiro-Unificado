# -*- coding: utf-8 -*-
"""
Limpeza de assets ÓRFÃOS: linhas gravadas em market.* sob o símbolo DIVERGENTE
da brapi em vez do ticker-B3 requisitado.

Contexto (2026-07): a brapi devolve, para ~9 empresas, um símbolo diferente do
ticker de negociação B3 (rebrand ELET3->AXIA3, CCRO3->MOTV3; troca de classe
AZUL4->AZUL3; erro EMBR3->EMBJ3). O ingest_ticker força tudo para o ticker-B3
(_reconcile_ticker) e registra o de->para em market.ticker_alias, mas o
renormalize NÃO fazia isso até a correção deste PR — cada backfill regravava as
linhas sob o símbolo da brapi, criando um asset invisível no app (que casa por
ticker-B3 via public.setores). Sintoma: market.assets tinha AXIA3 com 0
dividendos enquanto ELET3 tinha 55.

Este script remove o que JÁ FOI gravado errado: para cada par
(brapi_symbol -> b3_ticker) de market.ticker_alias, remapeia as linhas-fato do
símbolo órfão para o ticker-B3 (pulando as que já existem sob o B3, para não
violar as UNIQUE) e apaga o asset órfão — o ON DELETE CASCADE das FKs remove as
linhas duplicadas que sobraram. Idempotente.

Uso:
  python scripts/fix_orphan_alias_tickers.py            # dry-run (só relata)
  python scripts/fix_orphan_alias_tickers.py --apply    # remapeia + apaga órfão
  python scripts/fix_orphan_alias_tickers.py --apply --no-reprocess
  python scripts/fix_orphan_alias_tickers.py --symbols AXIA3 MOTV3
"""
import argparse
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

# Tabela-fato -> colunas da chave natural (além de ticker). Usadas para remapear
# sem colidir com a UNIQUE quando o ticker-B3 já tem a mesma linha.
_FACT_KEYS = {
    "historical_prices": ("date",),
    "income_statements": ("period", "year", "quarter"),
    "balance_sheets": ("period", "year", "quarter"),
    "cash_flow_statements": ("period", "year", "quarter"),
    "dividends": ("event_date", "type", "amount"),
    "calculated_metrics": ("period", "year", "quarter", "metric_name"),
}


def _load_aliases(conn, symbols):
    """(brapi_symbol -> b3_ticker) de market.ticker_alias, só os divergentes."""
    try:
        rows = conn.execute(text(
            "SELECT brapi_symbol, b3_ticker FROM market.ticker_alias")).fetchall()
    except Exception:
        return {}
    want = {s.upper().replace(".SA", "") for s in symbols} if symbols else None
    out = {}
    for sym, b3 in rows:
        sym = str(sym or "").upper().replace(".SA", "")
        b3 = str(b3 or "").upper().replace(".SA", "")
        if not sym or not b3 or sym == b3:
            continue
        if want and sym not in want:
            continue
        out[sym] = b3
    return out


def _orphan_counts(conn, sym):
    """Nº de linhas órfãs sob `sym` por tabela + se há asset órfão."""
    counts = {}
    for tbl in _FACT_KEYS:
        n = conn.execute(text(
            f"SELECT count(*) FROM market.{tbl} WHERE ticker=:s"),
            {"s": sym}).scalar() or 0
        if n:
            counts[tbl] = int(n)
    has_asset = bool(conn.execute(text(
        "SELECT 1 FROM market.assets WHERE ticker=:s"), {"s": sym}).scalar())
    return counts, has_asset


def _remap(conn, sym, b3):
    """Remapeia sym->b3 (pulando conflitos) e apaga o asset órfão (cascade
    remove os duplicados que sobraram). Retorna nº de linhas-fato remapeadas."""
    # garante o asset-B3 (herda company_id/tipo do órfão se ainda não existir)
    conn.execute(text("""
        INSERT INTO market.assets (company_id, ticker, asset_type, exchange, currency, is_active)
        SELECT company_id, :b3, asset_type, exchange, currency, is_active
          FROM market.assets WHERE ticker=:s
        ON CONFLICT (ticker) DO NOTHING
    """), {"s": sym, "b3": b3})
    remapped = 0
    for tbl, keys in _FACT_KEYS.items():
        match = " AND ".join(f"x.{c} IS NOT DISTINCT FROM o.{c}" for c in keys)
        res = conn.execute(text(f"""
            UPDATE market.{tbl} o SET ticker=:b3
             WHERE o.ticker=:s
               AND NOT EXISTS (SELECT 1 FROM market.{tbl} x
                                WHERE x.ticker=:b3 AND {match})
        """), {"s": sym, "b3": b3})
        remapped += res.rowcount or 0
    # apaga o asset órfão — CASCADE limpa as linhas duplicadas que sobraram
    conn.execute(text("DELETE FROM market.assets WHERE ticker=:s"), {"s": sym})
    return remapped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--apply", action="store_true",
                    help="executa o remap + delete (default: dry-run)")
    ap.add_argument("--no-reprocess", action="store_true",
                    help="não recalcular métricas dos tickers-B3 afetados após --apply")
    ap.add_argument("--symbols", nargs="*", default=None,
                    help="restringe a estes símbolos divergentes (brapi_symbol)")
    args = ap.parse_args()

    from core.database import get_engine
    engine = get_engine()
    if engine is None:
        print("ERRO: banco não configurado (DATABASE_URL/SUPABASE_UNIFICADO_URL).")
        return 1

    with engine.connect() as conn:
        aliases = _load_aliases(conn, args.symbols)
    if not aliases:
        print("nenhum alias divergente em market.ticker_alias — nada a fazer.")
        return 0

    afetados_b3, total_orfas = set(), 0
    for sym, b3 in sorted(aliases.items()):
        with engine.connect() as conn:
            counts, has_asset = _orphan_counts(conn, sym)
        n = sum(counts.values())
        if not n and not has_asset:
            continue
        total_orfas += n
        afetados_b3.add(b3)
        detalhe = ", ".join(f"{t}={c}" for t, c in sorted(counts.items())) or "—"
        asset_tag = " +asset" if has_asset else ""
        if args.apply:
            with engine.begin() as conn:
                remapped = _remap(conn, sym, b3)
            print(f"  {sym} -> {b3}: {remapped} linha(s) remapeada(s), "
                  f"órfão removido ({detalhe}{asset_tag})")
        else:
            print(f"  {sym} -> {b3}: {n} linha(s) órfã(s) ({detalhe}{asset_tag})")

    verbo = "remapeadas/limpas" if args.apply else "seriam remapeadas (dry-run)"
    print(f"\nlinhas-fato órfãs {verbo}: {total_orfas} em {len(afetados_b3)} ticker(s)-B3")

    if args.apply and afetados_b3 and not args.no_reprocess:
        print("\nreprocessando métricas dos tickers-B3 afetados...")
        from data_pipeline.market import ingest
        prog = ingest.reprocess_metrics(tickers=sorted(afetados_b3))
        print(f"reprocess_metrics: {prog}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
