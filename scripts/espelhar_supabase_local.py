"""Espelha no armazém local os dados de usuário do Supabase (controle financeiro e carteira).

O app publicado grava lançamentos, cartão, carteira e Tesouro **só** no Supabase.
O ``public`` do armazém local era uma cópia de bootstrap que parou no tempo
(1.345 lançamentos contra 1.539, sem Tesouro, sem eventos de investimento, sem
regras do cartão). Este script traz o estado do Supabase para o local.

O que ele faz, por tabela de ``TABELAS``:

1. cria no local a tabela que falta (DDL do próprio Supabase via ``pg_dump``,
   sem política de RLS, gatilho nem FK para ``auth``, que não existem aqui);
2. acrescenta coluna que o Supabase ganhou depois da cópia;
3. troca o conteúdo inteiro numa transação só, com os gatilhos de FK desligados
   durante a carga e as FKs conferidas no fim — se alguma referência quebrar,
   nada é gravado;
4. acerta as sequências e confere a contagem linha a linha contra a origem.

O que ele **não** faz:

- não toca o Supabase (só lê);
- não copia tabela em que o local escreve por conta própria (documentos,
  notícias, modelos de carteira gerados localmente, ``setores``). Se uma tabela
  de ``TABELAS`` tiver linha que só existe no local, o script se recusa, a não
  ser que ela esteja em ``DESCARTA_SO_LOCAL`` com o motivo;
- não substitui ``market.*``: preço e provento da B3 têm séries próprias no
  local e no Supabase (ver ``armazem-local-nao-tem-preco-diario-da-b3``).

Antes de gravar, faz ``pg_dump`` das tabelas locais afetadas em
``local_staging/backups/`` (fora do git: tem dado financeiro pessoal).

Simula por omissão. Uso:
    python scripts/espelhar_supabase_local.py            # mostra o que faria
    python scripts/espelhar_supabase_local.py --apply    # grava no local
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONTAINER = "dfu_warehouse"
PASTA_BACKUP = ROOT / "local_staging" / "backups"
BACKUPS_MANTIDOS = 14
# Carimbo de cada execução (é o que a rotina diária e o verificador leem) e as
# chaves que cada tabela recebeu, para separar "apagada no app" de "criada no
# local" na execução seguinte.
META = "public.espelho_supabase_meta"
CHAVES = "public.espelho_supabase_chaves"

# Dados de usuário que só o app publicado escreve, e portanto têm o Supabase
# como fonte. A ordem não importa: a carga roda com as FKs desligadas.
TABELAS = (
    # controle financeiro
    "profiles", "user_settings", "financial_institutions", "accounts", "cards",
    "categories", "card_category_rules", "transactions", "budgets", "debts",
    "financial_goals", "alerts", "import_batches", "import_logs",
    "bank_statement_movements", "bank_statement_classification_rules",
    # investimentos e carteira
    "assets", "asset_quotes", "portfolios", "portfolio_positions",
    "portfolio_position_snapshots", "portfolio_snapshots", "portfolio_snapshot_items",
    "portfolio_snapshot_analysis", "portfolio_asset_snapshots",
    "portfolio_allocation_targets", "investment_transactions",
    "investment_movement_events", "dividends", "tesouro_lots", "tesouro_market_rates",
    "benchmarks", "benchmark_quotes", "fii_portfolio_models", "fii_portfolio_model_items",
    "confianca_snapshots", "recomendacao_auditoria",
    # macro que as telas e as LLMs leem
    "macro", "info_economica", "info_economica_mensal",
)

# Linhas que só existem no local e podem ser descartadas, com o motivo. O backup
# guarda-as de qualquer forma.
DESCARTA_SO_LOCAL = {
    "portfolio_positions": (
        "posições zeradas de 23/05/2026 que o Supabase já limpou "
        "(upsert-sem-delete-deixa-fossil)"),
}

_NOME = re.compile(r"^[a-z_][a-z0-9_]*$")
# Instruções do DDL do Supabase que não têm como existir no armazém local.
_DDL_DESCARTE = re.compile(
    r"POLICY|ROW LEVEL SECURITY|CREATE TRIGGER|REFERENCES auth\.|OWNER TO|"
    r"GRANT |REVOKE |COMMENT ON|pg_catalog\.set_config|^SET |^SELECT ",
    re.IGNORECASE,
)


def _q(tabela: str) -> str:
    if not _NOME.fullmatch(tabela):
        raise ValueError(f"nome de tabela inválido: {tabela!r}")
    return f'public."{tabela}"'


def _motores():
    from sqlalchemy import create_engine

    from core.database import get_engine
    from scripts.publish_fii_selection_from_local import _warehouse_url

    return get_engine(), create_engine(_warehouse_url())


def _existe(conn, tabela: str) -> bool:
    from sqlalchemy import text

    return conn.execute(text("SELECT to_regclass(:t)"), {"t": _q(tabela)}).scalar() is not None


def _colunas(conn, tabela: str) -> dict[str, str]:
    from sqlalchemy import text

    return {r[0]: r[1] for r in conn.execute(text(
        "SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
        "WHERE a.attrelid = to_regclass(:t) AND a.attnum > 0 AND NOT a.attisdropped "
        "ORDER BY a.attnum"), {"t": _q(tabela)})}


def _pk(conn, tabela: str) -> list[str]:
    from sqlalchemy import text

    return [r[0] for r in conn.execute(text(
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
        "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = to_regclass(:t) AND i.indisprimary"), {"t": _q(tabela)})]


def _expr_chave(colunas: list[str]) -> str:
    """Chave primária como um texto só, montado pelo Postgres dos dois lados.

    Montar no banco, e não em Python, é o que torna comparável a chave lida do
    Supabase, a do local e a guardada em ``CHAVES``: timestamp e numeric viram
    texto do mesmo jeito nos três.
    """
    return "concat_ws('|', " + ", ".join(f'"{c}"::text' for c in colunas) + ")"


def _chaves_espelhadas(conn, tabela: str) -> set[str]:
    from sqlalchemy import text

    if conn.execute(text("SELECT to_regclass(:t)"), {"t": CHAVES}).scalar() is None:
        return set()
    return {r[0] for r in conn.execute(
        text(f"SELECT chave FROM {CHAVES} WHERE tabela = :t"), {"t": tabela})}


def diagnosticar(sup, loc) -> list[dict]:
    """Uma linha por tabela: contagens, linhas só-local, colunas que faltam."""
    from sqlalchemy import text

    linhas = []
    with sup.connect() as s, loc.connect() as lc:
        for tabela in TABELAS:
            d = {"tabela": tabela, "existe_local": _existe(lc, tabela), "so_local": 0,
                 "apagadas_na_origem": 0, "colunas_faltam": [], "bloqueio": ""}
            if not _existe(s, tabela):
                d["bloqueio"] = "não existe no Supabase"
                linhas.append(d)
                continue
            d["n_supabase"] = s.execute(text(f"SELECT count(*) FROM {_q(tabela)}")).scalar()
            if d["existe_local"]:
                d["n_local"] = lc.execute(text(f"SELECT count(*) FROM {_q(tabela)}")).scalar()
                cols_s, cols_l = _colunas(s, tabela), _colunas(lc, tabela)
                d["colunas_faltam"] = [(c, t) for c, t in cols_s.items() if c not in cols_l]
                chave = _pk(s, tabela)
                if chave and d["n_local"]:
                    sel = _expr_chave(chave)
                    origem = {r[0] for r in s.execute(text(f"SELECT {sel} FROM {_q(tabela)}"))}
                    so_local = [r[0] for r in lc.execute(text(f"SELECT {sel} FROM {_q(tabela)}"))
                                if r[0] not in origem]
                    # Linha que o espelho anterior trouxe e sumiu da origem foi
                    # apagada no app: sai junto. Só a que o local criou bloqueia.
                    espelhadas = _chaves_espelhadas(lc, tabela)
                    d["apagadas_na_origem"] = sum(1 for k in so_local if k in espelhadas)
                    d["so_local"] = len(so_local) - d["apagadas_na_origem"]
                elif not chave and d["n_local"]:
                    d["bloqueio"] = "sem chave primária para comparar"
                if d["so_local"] and tabela not in DESCARTA_SO_LOCAL:
                    d["bloqueio"] = f"{d['so_local']} linha(s) só no local"
            else:
                d["n_local"] = None
            linhas.append(d)
    return linhas


def _docker(*args: str, env: dict | None = None, entrada: bytes | None = None) -> bytes:
    cmd = ["docker", "exec"]
    for chave in (env or {}):
        cmd += ["-e", chave]
    if entrada is not None:
        cmd.append("-i")
    cmd += [CONTAINER, *args]
    proc = subprocess.run(cmd, input=entrada, capture_output=True,
                          env={**os.environ, **(env or {})})
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()[-800:])
    return proc.stdout


def fazer_backup(tabelas: list[str]) -> Path | None:
    """``pg_dump`` das tabelas locais que serão trocadas; devolve o arquivo."""
    if not tabelas:
        return None
    PASTA_BACKUP.mkdir(parents=True, exist_ok=True)
    carimbo = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = PASTA_BACKUP / f"espelho_antes_{carimbo}.dump"
    args = ["pg_dump", "-U", "postgres", "-d", "postgres", "-Fc"]
    for t in tabelas:
        args += ["-t", _q(t)]
    destino.write_bytes(_docker(*args))
    podar_backups(PASTA_BACKUP)
    return destino


def podar_backups(pasta: Path, manter: int = BACKUPS_MANTIDOS) -> list[Path]:
    """Rodando todo dia, sem poda a pasta cresce para sempre. Fica o mais novo."""
    antigos = sorted(pasta.glob("espelho_antes_*.dump"), reverse=True)[manter:]
    for arquivo in antigos:
        arquivo.unlink()
    return antigos


def ddl_do_supabase(tabelas: list[str]) -> list[str]:
    """DDL das tabelas ausentes, tirado do Supabase e limpo do que só existe lá."""
    from sqlalchemy.engine import make_url

    from core.config import settings

    url = make_url(settings.db_url)
    args = ["pg_dump", "-h", url.host, "-p", str(url.port or 5432), "-U", url.username,
            "-d", url.database, "--schema-only", "--no-owner", "--no-privileges"]
    for t in tabelas:
        args += ["-t", _q(t)]
    sql = _docker(*args, env={"PGPASSWORD": url.password or "",
                              "PGSSLMODE": "require"}).decode("utf-8")
    return limpar_ddl(sql)


def limpar_ddl(sql: str) -> list[str]:
    """Instruções do dump que o armazém local consegue executar."""
    # Comentário e meta-comando do psql (`\restrict`, do pg_dump 17.6+) não são
    # SQL: o driver recusa a instrução inteira.
    sql = "\n".join(l for l in sql.splitlines() if not l.startswith(("--", "\\")))
    instrucoes = []
    for bruto in sql.split(";\n"):
        inst = bruto.strip()
        if inst and not _DDL_DESCARTE.search(inst):
            instrucoes.append(inst)
    return instrucoes


def _copiar(s_raw, l_raw, tabela: str, colunas: list[str]) -> None:
    lista = ", ".join(f'"{c}"' for c in colunas)
    buf = io.BytesIO()
    s_raw.cursor().copy_expert(f"COPY (SELECT {lista} FROM {_q(tabela)}) TO STDOUT", buf)
    buf.seek(0)
    l_raw.cursor().copy_expert(f"COPY {_q(tabela)} ({lista}) FROM STDIN", buf)


def _fks_quebradas(cur, tabelas: set[str]) -> list[str]:
    """Confere toda FK do ``public`` que toca uma tabela trocada."""
    cur.execute("""
        SELECT c.conname, c.conrelid::regclass::text, c.confrelid::regclass::text,
               array(SELECT attname FROM pg_attribute WHERE attrelid = c.conrelid
                     AND attnum = ANY(c.conkey) ORDER BY array_position(c.conkey, attnum)),
               array(SELECT attname FROM pg_attribute WHERE attrelid = c.confrelid
                     AND attnum = ANY(c.confkey) ORDER BY array_position(c.confkey, attnum))
          FROM pg_constraint c
         WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace""")
    quebradas = []
    for nome, filha, mae, cf, cm in cur.fetchall():
        nf, nm = filha.split(".")[-1].strip('"'), mae.split(".")[-1].strip('"')
        if nf not in tabelas and nm not in tabelas:
            continue
        on = " AND ".join(f'f."{a}" = m."{b}"' for a, b in zip(cf, cm))
        nao_nulo = " AND ".join(f'f."{a}" IS NOT NULL' for a in cf)
        cur.execute(f"SELECT count(*) FROM {filha} f WHERE {nao_nulo} AND NOT EXISTS "
                    f"(SELECT 1 FROM {mae} m WHERE {on})")
        n = cur.fetchone()[0]
        if n:
            quebradas.append(f"{nome}: {n} linha(s) de {filha} sem par em {mae}")
    return quebradas


def _acertar_sequencias(cur, tabela: str) -> None:
    cur.execute("""
        SELECT a.attname, pg_get_serial_sequence(%s, a.attname)
          FROM pg_attribute a
         WHERE a.attrelid = to_regclass(%s) AND a.attnum > 0 AND NOT a.attisdropped""",
                (_q(tabela), _q(tabela)))
    for coluna, seq in cur.fetchall():
        if seq:
            cur.execute(f'SELECT setval(%s, COALESCE((SELECT max("{coluna}") FROM {_q(tabela)}), 0) + 1, false)',
                        (seq,))


def aplicar(sup, loc, diag: list[dict]) -> dict:
    from sqlalchemy import text

    bloqueadas = [d for d in diag if d["bloqueio"]]
    if bloqueadas:
        raise RuntimeError("recusado: " + "; ".join(f"{d['tabela']} ({d['bloqueio']})"
                                                    for d in bloqueadas))
    backup = fazer_backup([d["tabela"] for d in diag if d["existe_local"]])

    ausentes = [d["tabela"] for d in diag if not d["existe_local"]]
    avisos: list[str] = []
    if ausentes:
        with loc.begin() as lc:
            for inst in ddl_do_supabase(ausentes):
                eh_fk = "FOREIGN KEY" in inst.upper()
                try:
                    with lc.begin_nested():
                        lc.exec_driver_sql(inst)
                except Exception as exc:  # noqa: BLE001
                    # FK para tabela que não existe aqui (ou fora do grupo) não
                    # impede o espelho; tabela que não nasce, sim.
                    if not eh_fk:
                        raise
                    avisos.append(f"FK não criada: {str(exc).splitlines()[0][:160]}")
    with loc.begin() as lc:
        for d in diag:
            for coluna, tipo in d["colunas_faltam"]:
                lc.execute(text(f'ALTER TABLE {_q(d["tabela"])} ADD COLUMN IF NOT EXISTS "{coluna}" {tipo}'))

    s_raw, l_raw = sup.raw_connection(), loc.raw_connection()
    try:
        s_raw.set_session(readonly=True)
        cur = l_raw.cursor()
        cur.execute(f"CREATE TABLE IF NOT EXISTS {META} (executado_em timestamptz PRIMARY KEY, "
                    "tabelas int NOT NULL, linhas bigint NOT NULL, backup text)")
        cur.execute(f"CREATE TABLE IF NOT EXISTS {CHAVES} (tabela text NOT NULL, "
                    "chave text NOT NULL, PRIMARY KEY (tabela, chave))")
        cur.execute("SET LOCAL session_replication_role = replica")
        for d in diag:
            tabela = d["tabela"]
            with sup.connect() as s:
                colunas = list(_colunas(s, tabela))
                chave = _pk(s, tabela)
            cur.execute(f"DELETE FROM {_q(tabela)}")
            _copiar(s_raw, l_raw, tabela, colunas)
            _acertar_sequencias(cur, tabela)
            cur.execute(f"DELETE FROM {CHAVES} WHERE tabela = %s", (tabela,))
            if chave:
                cur.execute(f"INSERT INTO {CHAVES} SELECT %s, {_expr_chave(chave)} "
                            f"FROM {_q(tabela)}", (tabela,))
        cur.execute("SET LOCAL session_replication_role = origin")
        quebradas = _fks_quebradas(cur, set(TABELAS))
        if quebradas:
            raise RuntimeError("FK quebrada, nada gravado: " + "; ".join(quebradas))
        cur.execute(f"INSERT INTO {META} SELECT now(), %s, "
                    f"(SELECT count(*) FROM {CHAVES}), %s", (len(diag), str(backup or "")))
        l_raw.commit()
    except Exception:
        l_raw.rollback()
        raise
    finally:
        s_raw.close()
        l_raw.close()

    divergentes = []
    with sup.connect() as s, loc.connect() as lc:
        for tabela in TABELAS:
            ns = s.execute(text(f"SELECT count(*) FROM {_q(tabela)}")).scalar()
            nl = lc.execute(text(f"SELECT count(*) FROM {_q(tabela)}")).scalar()
            if ns != nl:
                divergentes.append(f"{tabela}: Supabase {ns} x local {nl}")
    return {"backup": backup, "avisos": avisos, "divergentes": divergentes}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="grava no armazém local")
    args = parser.parse_args(argv)

    sup, loc = _motores()
    diag = diagnosticar(sup, loc)
    for d in diag:
        local = "AUSENTE" if d["n_local"] is None else d["n_local"]
        extra = []
        if d["apagadas_na_origem"]:
            extra.append(f"{d['apagadas_na_origem']} apagada(s) no app")
        if d["so_local"]:
            extra.append(f"descarta {d['so_local']} só-local"
                         if d["tabela"] in DESCARTA_SO_LOCAL else f"{d['so_local']} só-local")
        if d["colunas_faltam"]:
            extra.append("+colunas " + ",".join(c for c, _ in d["colunas_faltam"]))
        if d["bloqueio"]:
            extra.append("BLOQUEADA: " + d["bloqueio"])
        print(f"{d['tabela']:38s} supabase={d.get('n_supabase', '-'):>6} local={local:>7} "
              + " | ".join(extra))
    if not args.apply:
        print("\nSimulação: nada gravado. Rode com --apply para espelhar.")
        return 0
    res = aplicar(sup, loc, diag)
    print(f"\nBackup do estado anterior: {res['backup']}")
    for aviso in res["avisos"]:
        print("aviso:", aviso)
    if res["divergentes"]:
        print("DIVERGÊNCIA após a carga:", "; ".join(res["divergentes"]))
        return 1
    print(f"Espelho concluído: {len(TABELAS)} tabelas com a mesma contagem do Supabase.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
