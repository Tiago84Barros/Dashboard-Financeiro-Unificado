"""
scripts/update_asset_quotes.py
Atualiza asset_quotes via yfinance para todos os ativos cadastrados em `assets`.

Uso:
    py -3.9 scripts/update_asset_quotes.py [--periodo 1mo] [--apenas-sem-cotacao]

Opcoes:
    --periodo          Periodo yfinance: 1mo, 3mo, 6mo, 1y, 2y, 5y  (padrao: 1mo)
    --apenas-sem-cotacao  Processa apenas ativos sem nenhuma cotacao em asset_quotes

Comportamento:
    - Idempotente: usa ON CONFLICT DO UPDATE — seguro para rodar mais de uma vez.
    - Ativos BRL recebem sufixo .SA automaticamente.
    - Falha em um ticker nao interrompe os demais.
    - Nao altera schema, nao deleta dados, nao expoe credenciais.
    - Requer SUPABASE_DB_URL (ou SUPABASE_UNIFICADO_URL) e MOCK_MODE=false no .env.
"""
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


def run(periodo: str = "1mo", apenas_sem_cotacao: bool = False) -> dict:
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

    sql = _SQL_SEM_COTACAO if apenas_sem_cotacao else _SQL_TODOS
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).fetchall()

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

    print(f"Processando {total} ativo(s) — periodo={periodo} "
          f"({'apenas sem cotacao' if apenas_sem_cotacao else 'todos'})")
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
                auto_adjust=True,
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
                        choices=["1mo","3mo","6mo","1y","2y","5y"],
                        help="Periodo yfinance (padrao: 1mo)")
    parser.add_argument("--apenas-sem-cotacao", action="store_true",
                        help="Processa apenas ativos sem cotacao")
    args = parser.parse_args()
    run(periodo=args.periodo, apenas_sem_cotacao=args.apenas_sem_cotacao)
