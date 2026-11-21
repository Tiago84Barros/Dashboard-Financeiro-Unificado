"""Cria (ou reconfirma) o papel ``app4_leitura`` do armazém, usado pela rota /sql.

A rota ``POST /sql`` do túnel (``scripts/servir_armazem_leitura.py``) executa
SELECTs que chegam do app publicado. A barreira de segurança dela é este
papel, não o filtro do texto: ele só enxerga o que já é dado público de
mercado.

* ``USAGE`` e ``SELECT`` nos schemas ``market`` e ``market_us`` (tabelas de
  hoje e, por ``ALTER DEFAULT PRIVILEGES``, as que o dono criar depois);
* ``SELECT`` em ``public.setores``;
* nada em ``public`` além disso (finanças pessoais, carteira), nem em
  ``memoria_mercado``, ``alertas_ativos`` ou ``auth``;
* sessão somente leitura e ``statement_timeout`` gravados no próprio papel, e
  no máximo 5 conexões;
* política de leitura (``FOR SELECT TO app4_leitura USING (true)``) em cada
  tabela desse escopo que tem RLS ligado. O papel é ``NOBYPASSRLS``: sem a
  política, o GRANT passa e o SELECT devolve **zero linhas**, sem erro -- o
  app leria "a tabela está vazia" e não cairia na reserva. Medido em
  18/11/2026: 24 tabelas (``market.b3_cotacao_atual``, ``market.fii_*``,
  ``market_us.score_panel_pub``...) e, num armazém restaurado de dump do
  Supabase (``027_data_api_rls_hardening``), também ``market.assets`` e os
  demonstrativos.

Depois do ``--apply`` o script entra **como o papel** e confere que cada
tabela que tem linha para o dono também tem para ele; a que não tem sai no
relatório e o código de saída é 1.

A senha é gerada aqui e nunca impressa: a URL vai direto para o arquivo de
segredos indicado (``--gravar-em``, padrão ``.env`` da raiz) como
``ARMAZEM_SQL_DB_URL``. Rodar de novo troca a senha e regrava a linha.

O serviço do túnel (tarefa ``DFU - Armazem leitura``) lê só o ``.env`` da
árvore principal; a pasta ``dfu-armazem-servico`` não tem ``.env``. Rode na
árvore principal ou, de outro worktree, aponte ``--gravar-em`` para lá.

    python scripts/criar_papel_leitura_armazem.py            # simula
    python scripts/criar_papel_leitura_armazem.py --apply    # na árvore principal
    python scripts/criar_papel_leitura_armazem.py --apply --gravar-em ..\\Dashboard-Financeiro-Unificado\\.env
"""
from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

PAPEL = "app4_leitura"
SCHEMAS = ("market", "market_us")
TABELAS_AVULSAS = ("public.setores",)
CHAVE_ENV = "ARMAZEM_SQL_DB_URL"
POLITICA = f"{PAPEL}_select"


def _escopo_sql(alias_ns: str = "n", alias_rel: str = "c") -> str:
    """Predicado SQL das tabelas que o papel pode ler (schemas + avulsas)."""
    schemas = ", ".join(f"'{s}'" for s in SCHEMAS)
    avulsas = " OR ".join(
        f"({alias_ns}.nspname = '{t.split('.')[0]}' AND {alias_rel}.relname = '{t.split('.')[1]}')"
        for t in TABELAS_AVULSAS)
    return f"({alias_ns}.nspname IN ({schemas}) OR {avulsas})"


def comandos(senha: str, dono: str) -> list[str]:
    """SQL que deixa o papel no estado esperado; idempotente."""
    cmds = [
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{PAPEL}') "
        f"THEN CREATE ROLE {PAPEL} LOGIN; END IF; END $$",
        f"ALTER ROLE {PAPEL} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
        f"NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5 PASSWORD '{senha}'",
        f"ALTER ROLE {PAPEL} SET default_transaction_read_only = on",
        f"ALTER ROLE {PAPEL} SET statement_timeout = '30s'",
        f"ALTER ROLE {PAPEL} SET idle_in_transaction_session_timeout = '60s'",
        # O papel não cria nada em public (o padrão antigo do Postgres deixava).
        f"REVOKE CREATE ON SCHEMA public FROM {PAPEL}",
    ]
    for s in SCHEMAS:
        cmds += [
            f"GRANT USAGE ON SCHEMA {s} TO {PAPEL}",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA {s} TO {PAPEL}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {dono} IN SCHEMA {s} "
            f"GRANT SELECT ON TABLES TO {PAPEL}",
        ]
    cmds.append(f"GRANT USAGE ON SCHEMA public TO {PAPEL}")
    cmds += [f"GRANT SELECT ON {t} TO {PAPEL}" for t in TABELAS_AVULSAS]
    # RLS: GRANT sem política devolve zero linhas, sem erro.
    cmds.append(
        "DO $$ DECLARE r record; BEGIN "
        "FOR r IN SELECT n.nspname AS esq, c.relname AS tab FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE c.relkind IN ('r', 'p') AND c.relrowsecurity AND {_escopo_sql()} "
        "AND NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid "
        f"AND p.polname = '{POLITICA}') LOOP "
        f"EXECUTE format('CREATE POLICY {POLITICA} ON %I.%I FOR SELECT TO {PAPEL} "
        "USING (true)', r.esq, r.tab); END LOOP; END $$")
    return cmds


def tabelas_cegas(eng_dono, eng_papel) -> list[str]:
    """Tabelas do escopo com linha para o dono e nenhuma para o papel."""
    from sqlalchemy import text

    with eng_dono.connect() as c:
        tabs = c.execute(text(
            "SELECT format('%I.%I', n.nspname, c.relname) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE c.relkind IN ('r', 'p') AND c.relrowsecurity AND {_escopo_sql()} "
            "ORDER BY 1")).scalars().all()
    cegas = []
    for t in tabs:
        sql = text(f"SELECT EXISTS (SELECT 1 FROM {t})")
        with eng_dono.connect() as c:
            if not c.execute(sql).scalar():
                continue
        with eng_papel.connect() as c:
            if not c.execute(sql).scalar():
                cegas.append(t)
    return cegas


def url_do_papel(url_admin: str, senha: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(url_admin).set(username=PAPEL, password=senha).render_as_string(
        hide_password=False)


def gravar_linha(arquivo: Path, chave: str, valor: str) -> None:
    linhas = arquivo.read_text(encoding="utf-8").splitlines() if arquivo.exists() else []
    padrao = re.compile(rf"^\s*{re.escape(chave)}\s*=")
    novas = [x for x in linhas if not padrao.match(x)] + [f"{chave}={valor}"]
    arquivo.write_text("\n".join(novas) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--gravar-em", default=str(RAIZ / ".env"))
    args = ap.parse_args(argv)

    from sqlalchemy import create_engine, text

    from scripts.publish_fii_selection_from_local import _warehouse_url

    url_admin = _warehouse_url()
    eng = create_engine(url_admin)
    with eng.connect() as c:
        dono = c.execute(text("SELECT current_user")).scalar_one()
    senha = secrets.token_urlsafe(32)
    cmds = comandos(senha, dono)
    if not args.apply:
        for cmd in cmds:
            print(cmd.replace(senha, "***") + ";")
        print(f"\n(simulação; --apply cria o papel e grava {CHAVE_ENV} em {args.gravar_em})")
        return 0
    with eng.begin() as c:
        for cmd in cmds:
            c.execute(text(cmd))
    url_papel = url_do_papel(url_admin, senha)
    gravar_linha(Path(args.gravar_em), CHAVE_ENV, url_papel)
    print(f"papel {PAPEL} pronto; {CHAVE_ENV} gravada em {args.gravar_em}")
    cegas = tabelas_cegas(eng, create_engine(url_papel))
    if cegas:
        print(f"ATENÇÃO: {len(cegas)} tabela(s) com linhas que {PAPEL} não enxerga: "
              + ", ".join(cegas))
        return 1
    print(f"visibilidade conferida como {PAPEL}: nenhuma tabela com RLS fica cega")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
