"""Republica market.calculated_metrics do armazém local para a vitrine Supabase.

Por que existe, em vez de usar ``publish_b3_tickers_from_local.py``: aquele
script copia SEIS tabelas (preços, três demonstrações, dividendos e métricas)
e exige a lista de tickers na linha de comando. Para propagar uma recomputação
de indicadores sobre ~430 empresas isso significa reenviar dezenas de MB de
dados que não mudaram, num Supabase Free cujo uso de metadados já passou de
0,5 GB. Aqui só as métricas viajam: 590 kB no período ttm.

O que torna este script diferente de um upsert simples: ele REMOVE ÓRFÃS. Um
upsert só insere e atualiza, então métrica que deixou de existir na origem
sobrevive na vitrine para sempre. Isso não é hipotético — em 30/07/2026 o sinal
``FCO_Negativo`` passou a exigir confirmação por prejuízo e 32 tickers o
perderam. Sem remoção, a vitrine seguiria reprovando empresas que a origem já
tinha absolvido, e as duas bases divergiriam em silêncio.

Padrão da casa: DRY-RUN por omissão; nada é escrito sem ``--apply``.

    python scripts/publish_b3_metrics_to_supabase.py                 # simula
    python scripts/publish_b3_metrics_to_supabase.py --apply         # aplica
    python scripts/publish_b3_metrics_to_supabase.py --periods ttm,annual --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from data_pipeline.market import repository                      # noqa: E402
from scripts.publish_b3_tickers_from_local import _remote_url     # noqa: E402
from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402
from scripts.publish_us_snapshot import _engine                  # noqa: E402

# Colunas de controle que a vitrine gera sozinha.
EXCLUDED_COLUMNS = {"id", "created_at", "updated_at"}

# Universo publicável: ações e units ativas com empresa associada — o mesmo
# recorte que core/market_read._multiplos_long usa para montar os múltiplos.
SQL_UNIVERSO = """
    SELECT ticker FROM market.assets
    WHERE is_active AND asset_type IN ('stock','unit') AND company_id IS NOT NULL
    ORDER BY ticker
"""


def _columns(conn, table: str) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(text("""
            SELECT column_name, is_generated
            FROM information_schema.columns
            WHERE table_schema='market' AND table_name=:t
            ORDER BY ordinal_position
        """), {"t": table})
        if str(row[0]) not in EXCLUDED_COLUMNS and str(row[1]) != "ALWAYS"
    ]


def _chave(row: dict) -> tuple:
    """Chave natural de calculated_metrics (ver repository._CONFLICT)."""
    return (str(row["ticker"]), str(row["period"]), int(row["year"] or 0),
            int(row["quarter"] or 0), str(row["metric_name"]))


def publish(periods: list[str], *, apply: bool = False,
            limit: int | None = None) -> dict:
    remote_url = _remote_url()
    if not remote_url:
        raise RuntimeError(
            "Supabase não configurado — defina SUPABASE_DB_URL no .env")

    source = create_engine(_warehouse_url(), pool_pre_ping=True)
    target = _engine(remote_url)
    repository.reset_db_cols_cache()
    resultado: dict[str, object] = {
        "modo": "APLICADO" if apply else "SIMULACAO (nada foi escrito)",
        "periodos": periods,
    }

    try:
        with source.connect() as src:
            tickers = [str(r[0]) for r in src.execute(text(SQL_UNIVERSO))]
            if limit:
                tickers = tickers[:limit]
            colunas = [c for c in _columns(src, "calculated_metrics")]
            origem = [dict(r) for r in src.execute(text(f"""
                SELECT {','.join(f'"{c}"' for c in colunas)}
                FROM market.calculated_metrics
                WHERE ticker = ANY(:tks) AND period = ANY(:ps)
            """), {"tks": tickers, "ps": periods}).mappings()]

        resultado["tickers"] = len(tickers)
        resultado["linhas_origem"] = len(origem)

        with target.begin() as dst:
            colunas_destino = set(_columns(dst, "calculated_metrics"))
            faltando = [c for c in colunas if c not in colunas_destino]
            if faltando:
                # Não é erro de dado: é migration pendente na vitrine. Falhar
                # alto é melhor que publicar métrica truncada em silêncio.
                raise RuntimeError(
                    "vitrine sem as colunas " + ", ".join(faltando)
                    + " — rode as migrations antes de publicar")

            remotas = [dict(r) for r in dst.execute(text("""
                SELECT ticker, period, year, quarter, metric_name
                FROM market.calculated_metrics
                WHERE ticker = ANY(:tks) AND period = ANY(:ps)
            """), {"tks": tickers, "ps": periods}).mappings()]

            chaves_origem = {_chave(r) for r in origem}
            orfas = [r for r in remotas if _chave(r) not in chaves_origem]
            resultado["linhas_vitrine_antes"] = len(remotas)
            resultado["orfas_a_remover"] = len(orfas)
            resultado["exemplos_orfas"] = sorted(
                {f"{r['ticker']}·{r['metric_name']}" for r in orfas})[:15]

            if not apply:
                dst.rollback()
                resultado["linhas_a_gravar"] = len(origem)
                return resultado

            gravadas = repository.upsert(
                dst, "calculated_metrics",
                [{k: v for k, v in r.items() if k != "raw_payload_id"}
                 for r in origem])
            resultado["linhas_gravadas"] = gravadas

            removidas = 0
            for r in orfas:
                removidas += dst.execute(text("""
                    DELETE FROM market.calculated_metrics
                    WHERE ticker=:ticker AND period=:period AND year=:year
                      AND quarter=:quarter AND metric_name=:metric_name
                """), dict(r)).rowcount or 0
            resultado["orfas_removidas"] = removidas
    finally:
        source.dispose()
        target.dispose()
    return resultado


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help="grava de fato (sem isto, apenas simula)")
    p.add_argument("--periods", default="ttm",
                   help="períodos separados por vírgula. 'ttm' (padrão, ~590 kB) "
                        "é o que o piso de qualidade e a carteira leem; "
                        "'annual' (~7,4 MB) alimenta o score ponto-no-tempo")
    p.add_argument("--limit", type=int, default=None,
                   help="processa só os N primeiros tickers (teste de fumaça)")
    args = p.parse_args()

    periods = [s.strip() for s in str(args.periods).split(",") if s.strip()]
    saida = publish(periods, apply=bool(args.apply), limit=args.limit)
    print(json.dumps(saida, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
