"""
Consulta SQLite do App 2 (Dashboard-Investimentos) para entender a fonte
'verdadeira' do PM que o usuario espera ver.

A SOURCE_DB_APP2 e a base de transacoes/posicoes do app original que migrou
para o App 4. Os PMs ali sao a verdade que o usuario validou via Dashboard
Investimentos.
"""
import os, sys, sqlite3
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import settings

TICKERS = ["BBAS3", "BBAS3F", "GMAT3", "GMAT3F", "PSSA3", "PSSA3F",
           "ROMI3", "ROMI3F", "CSMG3", "CSMG3F", "BRAP3", "BRAP3F",
           "DEXP3", "DEXP3F", "ISAE3", "ISAE3F", "DIRR3", "DIRR3F"]


def fmt(v, d=2):
    if v is None:
        return "—"
    try:
        s = f"{float(v):,.{d}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(v)


def secao(t):
    print()
    print("=" * 100)
    print(f"  {t}")
    print("=" * 100)


def main():
    db_url = settings.SOURCE_DB_APP2
    if not db_url or not db_url.startswith("sqlite:"):
        print("ERRO: SOURCE_DB_APP2 nao e SQLite ou nao configurado")
        sys.exit(1)
    db_path = db_url.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        print(f"ERRO: arquivo SQLite nao existe: {db_path}")
        sys.exit(1)

    print(f"DB: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Lista tabelas
    secao("Tabelas disponiveis no SQLite")
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for r in rows:
        cnt = conn.execute(f"SELECT COUNT(*) c FROM {r['name']}").fetchone()
        print(f"  {r['name']:<40} {cnt['c']:>8} rows")

    # Tenta descobrir nome das tabelas-chave
    table_names = [r["name"] for r in rows]
    pos_table = None
    tx_table = None
    for name in table_names:
        n = name.lower()
        if "position" in n or "carteira" in n:
            pos_table = name
        if "transac" in n or "trade" in n or "operac" in n:
            tx_table = name

    if not pos_table:
        # Fallback: ver tabelas com mais rows e que contenham ticker
        for name in table_names:
            cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({name})").fetchall()]
            if any("ticker" in c.lower() or "papel" in c.lower() or "codigo" in c.lower() for c in cols):
                print(f"\n  -> Candidata pos_table: {name} (colunas: {cols[:6]}...)")

    # Para cada tabela, mostrar primeiras 2 linhas para entender schema
    secao("Schema e amostra de cada tabela")
    for name in table_names:
        cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({name})").fetchall()]
        print(f"\n  {name}: {cols}")
        try:
            sample = conn.execute(f"SELECT * FROM {name} LIMIT 1").fetchone()
            if sample:
                print(f"    sample: {dict(sample)}")
        except Exception as e:
            print(f"    (erro lendo: {e})")

    # Tentar query especifica nas tabelas candidatas
    secao("Procurando posicoes BBAS3 em todas as tabelas")
    for name in table_names:
        cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({name})").fetchall()]
        ticker_col = None
        for c in cols:
            if c.lower() in ("ticker", "papel", "codigo", "symbol", "ativo", "asset", "codigo_negociacao"):
                ticker_col = c
                break
        if not ticker_col:
            continue
        try:
            rows = conn.execute(
                f"SELECT * FROM {name} WHERE UPPER({ticker_col}) LIKE 'BBAS3%' LIMIT 10"
            ).fetchall()
            if rows:
                print(f"\n  TABLE: {name} (ticker_col={ticker_col})")
                for r in rows:
                    print(f"    {dict(r)}")
        except Exception as e:
            print(f"  Erro em {name}: {e}")

    conn.close()


if __name__ == "__main__":
    main()
