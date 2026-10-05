"""
scripts/update_asset_quotes.py
Atualiza asset_quotes via yfinance para todos os ativos cadastrados em `assets`.

Uso:
    py -3.9 scripts/update_asset_quotes.py [--periodo 1mo] [--apenas-sem-cotacao]
                                           [--moeda USD] [--bruto]

Opcoes:
    --periodo          Periodo yfinance: 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max  (padrao: 1mo)
    --apenas-sem-cotacao  Processa apenas ativos sem nenhuma cotacao em asset_quotes
    --moeda            Processa apenas ativos dessa moeda (BRL ou USD)
    --bruto            Grava o fechamento negociado, sem ajuste por proventos

Historico do exterior (Nomad) para a Evolucao Patrimonial:
    py -3.9 scripts/update_asset_quotes.py --periodo max --moeda USD --bruto
    A serie recompoe o exterior de cada mes por quantidade x fechamento x USDBRL;
    sem cotacao na data o exterior entra como zero. --moeda USD baixa so os ETFs
    da Nomad, sem reescrever o historico das acoes da B3. --bruto porque o
    fechamento ajustado de um ETF que paga provento (SGOV, TFLO) fica abaixo do
    preco daquele dia, e o patrimonio do passado sairia menor do que foi.

Comportamento:
    - Idempotente: usa ON CONFLICT DO UPDATE — seguro para rodar mais de uma vez.
    - Ativos BRL recebem sufixo .SA automaticamente.
    - Falha em um ticker nao interrompe os demais.
    - Nao altera schema, nao deleta dados, nao expoe credenciais.
    - Requer SUPABASE_DB_URL (ou SUPABASE_UNIFICADO_URL) e MOCK_MODE=false no .env.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Garante que o root do projeto esta no path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

_SQL_UPSERT = """
    INSERT INTO asset_quotes
        (asset_id, timestamp, open, high, low, close, volume)
    VALUES
        (:asset_id, :ts, :open, :high, :low, :close, :volume)
    ON CONFLICT (asset_id, timestamp) DO UPDATE
        SET close  = EXCLUDED.close,
            open   = EXCLUDED.open,
            high   = EXCLUDED.high,
            low    = EXCLUDED.low,
            volume = EXCLUDED.volume
"""

_SQL_TODOS = "SELECT id::text AS id, ticker, currency FROM assets ORDER BY ticker"

_SQL_SEM_COTACAO = """
    SELECT id::text AS id, ticker, currency
    FROM   assets
    WHERE  NOT EXISTS (
        SELECT 1 FROM asset_quotes aq WHERE aq.asset_id = assets.id
    )
    ORDER  BY ticker
"""

_SQL_POR_MOEDA = """
    SELECT id::text AS id, ticker, currency
    FROM   assets
    WHERE  upper(coalesce(currency, 'BRL')) = :moeda
    ORDER  BY ticker
"""


def _sql_ativos(apenas_sem_cotacao: bool, moeda: str | None) -> tuple[str, dict]:
    if moeda and apenas_sem_cotacao:
        raise ValueError("--moeda e --apenas-sem-cotacao nao se combinam.")
    if moeda:
        return _SQL_POR_MOEDA, {"moeda": moeda.upper()}
    return (_SQL_SEM_COTACAO if apenas_sem_cotacao else _SQL_TODOS), {}


def run(periodo: str = "1mo", apenas_sem_cotacao: bool = False,
        moeda: str | None = None, bruto: bool = False) -> dict:
    try:
        import yfinance as yf
    except ImportError:
        print("[ERRO] yfinance nao instalado. Execute: pip install yfinance")
        sys.exit(1)

    from sqlalchemy import text

    from core.config import settings
    from core.database import get_engine

    if settings.MOCK_MODE:
        print("[AVISO] MOCK_MODE=true — o script ira tentar o banco mesmo assim.")

    engine = get_engine()
    if engine is None:
        print("[ERRO] Engine nao criado. Verifique SUPABASE_DB_URL no .env.")
        sys.exit(1)

    sql, params = _sql_ativos(apenas_sem_cotacao, moeda)
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()

    if not rows:
        print("[OK] Nenhum ativo para processar.")
        return {"total": 0, "sucesso": 0, "sem_dados": 0, "erros": 0, "total_pts": 0,
                "sem_cotacao": [], "com_erro": []}

    total       = len(rows)
    sucesso     = 0
    sem_dados   = 0
    erros       = 0
    total_pts   = 0
    sem_cotacao_list: list[str] = []
    com_erro_list:    list[str] = []

    escopo = (f"moeda {moeda.upper()}" if moeda
              else "apenas sem cotacao" if apenas_sem_cotacao else "todos")
    print(f"Processando {total} ativo(s) — periodo={periodo} ({escopo}"
          f"{', fechamento bruto' if bruto else ''})")
    print("-" * 60)

    for i, r in enumerate(rows, 1):
        currency = (r.currency or "BRL").upper()
        ticker_yf = f"{r.ticker}.SA" if currency == "BRL" else r.ticker
        prefix = f"  [{i:>3}/{total}] {ticker_yf:<16}"

        try:
            hist = yf.download(
                ticker_yf,
                period=periodo,
                progress=False,
                auto_adjust=not bruto,
                actions=False,
            )

            if hist is None or hist.empty:
                sem_dados += 1
                sem_cotacao_list.append(ticker_yf)
                print(f"{prefix} SEM DADOS")
                continue

            records = []
            # Handle MultiIndex columns from yfinance >= 0.2.x
            if hasattr(hist.columns, "levels"):
                hist.columns = hist.columns.get_level_values(0)

            for ts, row_data in hist.iterrows():
                close_val = float(row_data.get("Close", 0) or 0)
                if close_val > 0:
                    records.append({
                        "asset_id": r.id,
                        "ts":       ts.to_pydatetime(),
                        "open":     float(row_data.get("Open",   0) or 0) or None,
                        "high":     float(row_data.get("High",   0) or 0) or None,
                        "low":      float(row_data.get("Low",    0) or 0) or None,
                        "close":    close_val,
                        "volume":   float(row_data.get("Volume", 0) or 0) or None,
                    })

            if records:
                with engine.begin() as conn:
                    conn.execute(text(_SQL_UPSERT), records)
                total_pts += len(records)
                sucesso   += 1
                print(f"{prefix} OK  {len(records):>4} pts")
            else:
                sem_dados += 1
                sem_cotacao_list.append(ticker_yf)
                print(f"{prefix} SEM DADOS VALIDOS")

        except Exception as exc:
            erros += 1
            com_erro_list.append(ticker_yf)
            print(f"{prefix} ERRO  {str(exc)[:60]}")

    print("-" * 60)
    print(f"Resultado: {sucesso} OK | {sem_dados} sem dados | {erros} erros | {total_pts:,} pts inseridos")

    if sem_cotacao_list:
        print(f"Sem cotacao ({len(sem_cotacao_list)}): {', '.join(sem_cotacao_list[:20])}"
              + (" ..." if len(sem_cotacao_list) > 20 else ""))
    if com_erro_list:
        print(f"Com erro   ({len(com_erro_list)}): {', '.join(com_erro_list)}")

    # Contagem final em asset_quotes
    with engine.connect() as conn:
        n_quotes  = conn.execute(text("SELECT COUNT(*) FROM asset_quotes")).scalar()
        n_ativos  = conn.execute(text("SELECT COUNT(DISTINCT asset_id) FROM asset_quotes")).scalar()
        last_date = conn.execute(text("SELECT MAX(timestamp) FROM asset_quotes")).scalar()

    print()
    print(f"asset_quotes agora: {n_quotes:,} linhas | {n_ativos} ativos com cotacao")
    print(f"Ultima data: {last_date.date() if last_date else 'N/A'}")

    return {
        "total":           total,
        "sucesso":         sucesso,
        "sem_dados":       sem_dados,
        "erros":           erros,
        "total_pts":       total_pts,
        "sem_cotacao":     sem_cotacao_list,
        "com_erro":        com_erro_list,
        "n_quotes_final":  n_quotes,
        "n_ativos_final":  n_ativos,
        "last_date":       str(last_date.date()) if last_date else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Atualiza asset_quotes via yfinance")
    parser.add_argument("--periodo",            default="1mo",
                        choices=["1mo","3mo","6mo","1y","2y","5y","10y","max"],
                        help="Periodo yfinance (padrao: 1mo)")
    parser.add_argument("--apenas-sem-cotacao", action="store_true",
                        help="Processa apenas ativos sem cotacao")
    parser.add_argument("--moeda", choices=["BRL", "USD"],
                        help="Processa apenas ativos dessa moeda")
    parser.add_argument("--bruto", action="store_true",
                        help="Fechamento negociado, sem ajuste por proventos")
    args = parser.parse_args()
    run(periodo=args.periodo, apenas_sem_cotacao=args.apenas_sem_cotacao,
        moeda=args.moeda, bruto=args.bruto)
