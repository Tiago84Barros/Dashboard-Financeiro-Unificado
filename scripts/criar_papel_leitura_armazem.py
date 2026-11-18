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
  no máximo 5 conexões.

A senha é gerada aqui e nunca impressa: a URL vai direto para o arquivo de
segredos indicado (``--gravar-em``, padrão ``.env`` da raiz) como
``ARMAZEM_SQL_DB_URL``. Rodar de novo troca a senha e regrava a linha.

    python scripts/criar_papel_leitura_armazem.py            # simula
    python scripts/criar_papel_leitura_armazem.py --apply
    python scripts/criar_papel_leitura_armazem.py --apply --gravar-em ..\\dfu-armazem-servico\\.env
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
    return cmds


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
    gravar_linha(Path(args.gravar_em), CHAVE_ENV, url_do_papel(url_admin, senha))
    print(f"papel {PAPEL} pronto; {CHAVE_ENV} gravada em {args.gravar_em}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
