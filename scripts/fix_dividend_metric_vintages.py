# -*- coding: utf-8 -*-
"""
Scrub de vintages contaminadas por ecos de dividendos (complemento do PR #55).

Contexto: o caminho point-in-time (core.market_read._annual_long) lê a
PRIMEIRA vintage de cada (ticker, ano, métrica) em
market.calculated_metric_vintages — por design, "o valor como conhecido na
época". Para os tickers com ecos de classe/fonte CSV da brapi (caso CEB),
essa primeira vintage nasceu do banco poluído: o DY/Payout anual ficou ~2x
inflado NO SCORE E NO BACKTEST mesmo depois de market.calculated_metrics ter
sido corrigida (o reprocess só acrescenta vintages novas, que esse caminho
ignora de propósito).

A poluição não é uma observação legítima de mercado — é bug de ingestão
nosso; a vintage nunca representou o que "se sabia na época". Por isso este
script CORRIGE o valor no lugar (exceção documentada à imutabilidade da
tabela), registrando cada mudança em market.data_quality_logs para auditoria:

  1. re-deriva dos payloads brutos os (ticker, ano) com eco;
  2. para DY/Payout anuais desses pares, iguala as vintages infladas ao valor
     corrigido vigente em market.calculated_metrics (recalculado pós-limpeza
     de scripts/fix_dividends_class_mix.py); sem valor corrigido, remove;
  3. apaga o DY 'spot' (consenso brapi, que soma as classes) obsoleto em
     market.calculated_metrics quando exceder 1,5x o DY ttm próprio — o
     normalizador (metric_rows) deixou de regravá-lo nesses casos.

Uso:
  python scripts/fix_dividend_metric_vintages.py           # dry-run
  python scripts/fix_dividend_metric_vintages.py --apply
  python scripts/fix_dividend_metric_vintages.py --tickers CEBR5 CEBR6
"""
import argparse
import os
import sys
from collections import defaultdict

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from data_pipeline.market import integrity
from data_pipeline.market import repository as repo

# só sinal de eco (inflação): tolerância relativa acima da qual a vintage
# difere do valor corrigido
REL_TOL = 0.001


def affected_ticker_years(engine, tickers=None, exclude=None) -> set[tuple[str, int]]:
    """(ticker, ano) cujos dividendos tinham eco em algum payload."""
    excl = {t.upper().replace(".SA", "") for t in (exclude or [])}
    pares: set[tuple[str, int]] = set()
    with engine.connect() as conn:
        for _pid, tks, items in integrity.iter_payload_cash_dividends(
                conn, tickers, latest_only=False):
            for tk, event_iso, _typ, _amt in integrity.dividend_drop_keys(items, tks):
                if tk not in excl:
                    pares.add((tk, int(event_iso[:4])))
    return pares


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--apply", action="store_true",
                    help="executa UPDATEs/DELETEs (default: dry-run)")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--exclude", nargs="*", default=None,
                    help="tickers a pular (ex.: sob correção concorrente noutra sessão)")
    args = ap.parse_args()

    from core.database import get_engine
    engine = get_engine()
    if engine is None:
        print("ERRO: banco não configurado.")
        return 1

    pares = affected_ticker_years(engine, args.tickers, args.exclude)
    print(f"pares (ticker, ano) com eco: {len(pares)}")
    if not pares:
        print("nada a fazer.")
        return 0

    stats = defaultdict(int)
    with engine.connect() as conn:
        # 1) vintages anuais DY/Payout divergentes do valor corrigido
        alvo = conn.execute(text("""
            SELECT v.id, v.ticker, v.year, v.metric_name,
                   v.metric_value AS antigo, cm.metric_value AS corrigido
            FROM market.calculated_metric_vintages v
            LEFT JOIN market.calculated_metrics cm
              ON cm.ticker = v.ticker AND cm.period = 'annual'
             AND cm.year = v.year AND cm.metric_name = v.metric_name
            WHERE v.period = 'annual' AND v.metric_name IN ('DY', 'Payout')
              AND (v.ticker, v.year) IN :pares
        """).bindparams(pares=tuple(pares))).fetchall()

        # A vintage anual desses tickers é migration_baseline (backfill da
        # ingestão poluída), não uma observação PIT real capturada em data
        # histórica — o _annual_long anula o available_at do baseline e o
        # scorer usa o corte fiscal. Logo, alinhar ao valor limpo é "o que a
        # vintage teria sido sem o bug", não reescrita de história real.
        # Compara por DESVIO ABSOLUTO (não direção): com EPS<0 o Payout
        # inflado fica MAIS negativo, e um teste direcional o deixaria passar.
        corrigir, remover = [], []
        n_infladas = n_deprimidas = 0
        for r in alvo:
            antigo = float(r.antigo) if r.antigo is not None else None
            corr = float(r.corrigido) if r.corrigido is not None else None
            if antigo is None:
                continue
            if corr is None:
                remover.append(r)               # irrecalculável — nasceu poluída
            elif abs(antigo - corr) > REL_TOL * max(abs(corr), 1e-6):
                corrigir.append(r)              # diverge do limpo → alinhar
                if abs(antigo) > abs(corr):
                    n_infladas += 1
                else:
                    n_deprimidas += 1

        print(f"vintages anuais DY/Payout nos pares: {len(alvo)}")
        print(f"  divergentes do valor limpo -> corrigir: {len(corrigir)} "
              f"(|infladas|={n_infladas}, |deprimidas p/ Payout<0|={n_deprimidas})")
        print(f"  sem valor corrigido -> remover: {len(remover)}")
        for r in corrigir[:15]:
            print(f"  fix {r.ticker} {r.year} {r.metric_name}: {r.antigo} -> {r.corrigido}")
        if len(corrigir) > 15:
            print(f"  ... e mais {len(corrigir) - 15}")

        # 2) DY spot obsoleto (consenso brapi somando classes)
        spot = conn.execute(text("""
            SELECT s.id, s.ticker, s.metric_value AS spot, t.metric_value AS ttm
            FROM market.calculated_metrics s
            JOIN market.calculated_metrics t
              ON t.ticker = s.ticker AND t.period = 'ttm'
             AND t.metric_name = 'DY' AND t.metric_value > 0
            WHERE s.period = 'spot' AND s.metric_name = 'DY'
              AND s.ticker IN :tks
              AND s.metric_value > t.metric_value * 1.5
        """).bindparams(tks=tuple(sorted({tk for tk, _y in pares})))).fetchall()
        print(f"DY spot obsoletos (>1,5x o ttm proprio) -> remover: {len(spot)}")
        for r in spot[:15]:
            print(f"  del spot {r.ticker}: {r.spot} (ttm proprio: {r.ttm})")

    if not args.apply:
        print("\ndry-run — nada gravado. Use --apply para executar.")
        return 0

    with engine.begin() as conn:
        for r in corrigir:
            conn.execute(text("""
                UPDATE market.calculated_metric_vintages
                SET metric_value = :novo WHERE id = :id
            """), {"novo": r.corrigido, "id": r.id})
            repo.log_quality(
                conn, ticker=r.ticker, table_name="calculated_metric_vintages",
                field_name=r.metric_name, issue_type="vintage_eco_corrigida",
                old_value=r.antigo, new_value=r.corrigido, severity="info",
                source="market.compute")
            stats["corrigidas"] += 1
        for r in remover:
            conn.execute(text(
                "DELETE FROM market.calculated_metric_vintages WHERE id = :id"),
                {"id": r.id})
            repo.log_quality(
                conn, ticker=r.ticker, table_name="calculated_metric_vintages",
                field_name=r.metric_name, issue_type="vintage_eco_removida",
                old_value=r.antigo, new_value=None, severity="info",
                source="market.compute")
            stats["removidas"] += 1
        for r in spot:
            conn.execute(text(
                "DELETE FROM market.calculated_metrics WHERE id = :id"), {"id": r.id})
            repo.log_quality(
                conn, ticker=r.ticker, table_name="calculated_metrics",
                field_name="DY", issue_type="spot_eco_removido",
                old_value=r.spot, new_value=None, severity="info",
                source="brapi.dev")
            stats["spot_removidos"] += 1

    print(f"\naplicado: {dict(stats)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
