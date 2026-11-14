"""Prepara o Supabase das contas novas (banco ``novos``) a partir do principal.

Desde 14/11/2026 o cadastro é aberto na tela de login, e toda conta criada
nasce num segundo projeto Supabase: o principal passou dos 500 MB do plano
free. As contas antigas e os dados delas continuam no principal.

O banco ``novos`` fica assim:

- **Local**: ``profiles`` e toda tabela de dados de pessoa -- as que têm
  ``user_id`` e as filhas delas (itens de modelo, logs de importação). Mesmas
  colunas, padrões, restrições e índices do principal, lidos do catálogo.
- **Remoto, por ``postgres_fdw``**: todo o resto -- cotações, fundamentos,
  macro, notícias, vitrines, ``market`` e ``market_us`` -- como tabelas
  estrangeiras que leem o principal. O app não muda: as mesmas consultas
  servem aos dois bancos, e o dado de mercado não é copiado.
- **Visões** que leem tabelas de pessoa (``v_account_balance`` & cia.) são
  recriadas localmente, para somar os dados de quem mora aqui.
- As **linhas sem dono** das tabelas de pessoa (as categorias do sistema,
  ``user_id IS NULL``) são copiadas, com o mesmo id.
- RLS ligado nas tabelas locais e nada liberado para ``anon`` e
  ``authenticated``: a API de dados do Supabase não expõe nada. O app conecta
  como dono das tabelas.

Uso::

    python scripts/preparar_supabase_novos.py            # mostra o SQL
    python scripts/preparar_supabase_novos.py --aplicar  # executa no novos

As URLs saem de ``core.config.settings`` (``db_url`` e ``SUPABASE_NOVOS_URL``) ou de
``--principal`` / ``--novos``. ``--fdw-url`` diz como o banco novos alcança o
principal; o padrão é a própria URL do principal, que deve ser a do session
pooler (porta 5432): o host direto do Supabase só tem IPv6.

Rodar de novo é seguro, e é OBRIGATÓRIO depois de cada migration aplicada no
principal: a camada remota é refeita inteira (as tabelas estrangeiras são uma
foto do catálogo), tabelas e colunas novas de pessoa são criadas, e nenhum
dado de pessoa já gravado no novos é apagado. Uma migration que muda tipo,
restrição ou índice de tabela de pessoa precisa ser aplicada também no novos.

A senha do principal vai para o mapeamento de usuário do ``postgres_fdw`` no
banco novos (visível só ao dono) e nunca é impressa: o SQL mostrado sai com a
senha trocada por ``***``.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from sqlalchemy import create_engine, make_url, text  # noqa: E402

SERVIDOR = "app4_principal"
#: Esquema de passagem para copiar as linhas sem dono; apagado no fim.
ORIGEM = "app4_origem"
#: Esquemas do Supabase que não são do app e não viram tabela estrangeira.
ESQUEMAS_DE_SISTEMA = frozenset({
    "auth", "storage", "realtime", "extensions", "vault", "graphql",
    "graphql_public", "pgsodium", "pgsodium_masks", "net", "cron",
    "supabase_functions", "supabase_migrations", "pgbouncer",
    "information_schema", ORIGEM,
})
_PAPEIS_DA_API = ("anon", "authenticated")
_NEXTVAL = re.compile(r"nextval\('([^']+)'::regclass\)")


def q(nome: str) -> str:
    """Identificador citado. Os nomes vêm do catálogo, nunca de quem usa."""
    return '"' + nome.replace('"', '""') + '"'


def lit(valor: str) -> str:
    return "'" + str(valor).replace("'", "''") + "'"


@dataclass
class Plano:
    locais: list[str]
    visoes_locais: list[str]
    esquemas_remotos: list[str]
    comandos: list[str] = field(default_factory=list)
    #: Índices de ``comandos`` que levam a senha (impressos com ``***``).
    secretos: dict[int, str] = field(default_factory=dict)


# --------------------------------------------------------------- catálogo

def tabelas_locais(conn) -> list[str]:
    """``profiles``, as tabelas com ``user_id`` e as filhas delas, pais antes."""
    linhas = conn.execute(text("""
        SELECT c.relname,
               EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid
                        AND a.attname = 'user_id' AND NOT a.attisdropped)
          FROM pg_class c
         WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
    """)).all()
    todas = {nome for nome, _ in linhas}
    locais = {nome for nome, dono in linhas if dono}
    if "profiles" in todas:
        locais.add("profiles")
    fks = conn.execute(text("""
        SELECT f.relname, m.relname
          FROM pg_constraint k
          JOIN pg_class f ON f.oid = k.conrelid
          JOIN pg_class m ON m.oid = k.confrelid
         WHERE k.contype = 'f'
           AND f.relnamespace = 'public'::regnamespace
           AND m.relnamespace = 'public'::regnamespace
    """)).all()
    mudou = True
    while mudou:
        mudou = False
        for filha, mae in fks:
            if mae in locais and filha not in locais:
                locais.add(filha)
                mudou = True
    pais = {t: {m for f, m in fks if f == t and m in locais and m != t} for t in locais}
    ordem: list[str] = []
    pendentes = set(locais)
    while pendentes:
        prontas = sorted(t for t in pendentes if pais[t] <= set(ordem))
        if not prontas:  # ciclo entre tabelas: a ordem não importa, as FKs vêm depois
            prontas = sorted(pendentes)
        ordem.extend(prontas)
        pendentes -= set(prontas)
    return ordem


def visoes_locais(conn, locais: list[str]) -> list[tuple[str, str]]:
    """Visões do ``public`` que leem (mesmo indiretamente) tabela de pessoa.

    Devolve ``(nome, CREATE VIEW ...)`` em ordem de criação.
    """
    deps = conn.execute(text("""
        SELECT DISTINCT v.relname, t.relname, t.relnamespace = 'public'::regnamespace
          FROM pg_depend d
          JOIN pg_rewrite rw ON rw.oid = d.objid
          JOIN pg_class v ON v.oid = rw.ev_class
          JOIN pg_class t ON t.oid = d.refobjid
         WHERE d.classid = 'pg_rewrite'::regclass
           AND d.refclassid = 'pg_class'::regclass
           AND v.relnamespace = 'public'::regnamespace
           AND v.relkind = 'v' AND t.oid <> v.oid
    """)).all()
    usa: dict[str, set[str]] = {}
    for visao, dep, no_public in deps:
        usa.setdefault(visao, set())
        if no_public:
            usa[visao].add(dep)
    marcadas = set(locais)
    escolhidas: set[str] = set()
    mudou = True
    while mudou:
        mudou = False
        for visao, alvos in usa.items():
            if visao not in escolhidas and alvos & (marcadas | escolhidas):
                escolhidas.add(visao)
                mudou = True
    ordem: list[str] = []
    pendentes = set(escolhidas)
    while pendentes:
        prontas = sorted(v for v in pendentes if not (usa[v] & pendentes))
        if not prontas:
            raise RuntimeError(f"visões em ciclo: {sorted(pendentes)}")
        ordem.extend(prontas)
        pendentes -= set(prontas)
    saida = []
    for visao in ordem:
        definicao, opcoes = conn.execute(text("""
            SELECT pg_get_viewdef(c.oid, true), c.reloptions
              FROM pg_class c
             WHERE c.relnamespace = 'public'::regnamespace AND c.relname = :v
        """), {"v": visao}).one()
        com = f" WITH ({', '.join(opcoes)})" if opcoes else ""
        corpo = definicao.strip().rstrip(";")
        saida.append((visao, f"CREATE VIEW public.{q(visao)}{com} AS\n{corpo}"))
    return saida


def esquemas_remotos(conn) -> list[str]:
    linhas = conn.execute(text("""
        SELECT DISTINCT n.nspname
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND n.nspname NOT LIKE 'pg\\_%'
    """)).scalars().all()
    return sorted(s for s in linhas if s not in ESQUEMAS_DE_SISTEMA)


def _colunas(conn, tabela: str) -> list[dict]:
    linhas = conn.execute(text("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid), a.attgenerated, a.attidentity
          FROM pg_attribute a
          LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
         WHERE a.attrelid = ('public.' || quote_ident(:t))::regclass
           AND a.attnum > 0 AND NOT a.attisdropped
         ORDER BY a.attnum
    """), {"t": tabela}).all()
    return [dict(nome=n, tipo=t, not_null=nn, padrao=p, gerada=g, identidade=i)
            for n, t, nn, p, g, i in linhas]


def _definicao_coluna(col: dict) -> str:
    partes = [q(col["nome"]), col["tipo"]]
    if col["gerada"] == "s":
        partes.append(f"GENERATED ALWAYS AS ({col['padrao']}) STORED")
    elif col["identidade"]:
        modo = "ALWAYS" if col["identidade"] == "a" else "BY DEFAULT"
        partes.append(f"GENERATED {modo} AS IDENTITY")
    elif col["padrao"] is not None:
        partes.append(f"DEFAULT {col['padrao']}")
    if col["not_null"]:
        partes.append("NOT NULL")
    return " ".join(partes)


def _restricoes(conn, tabela: str) -> list[tuple[str, str, str, str | None]]:
    """``(nome, tipo, definição, tabela referida no public ou None)``."""
    return [tuple(r) for r in conn.execute(text("""
        SELECT k.conname, k.contype, pg_get_constraintdef(k.oid),
               CASE WHEN m.relnamespace = 'public'::regnamespace THEN m.relname END
          FROM pg_constraint k
          LEFT JOIN pg_class m ON m.oid = k.confrelid
         WHERE k.conrelid = ('public.' || quote_ident(:t))::regclass
           AND k.contype IN ('p', 'u', 'x', 'c', 'f')
         ORDER BY position(k.contype IN 'puxcf'), k.conname
    """), {"t": tabela}).all()]


def _indices(conn, tabela: str) -> list[str]:
    """Índices que não sustentam restrição (estes nascem com a restrição)."""
    defs = conn.execute(text("""
        SELECT pg_get_indexdef(i.indexrelid)
          FROM pg_index i
         WHERE i.indrelid = ('public.' || quote_ident(:t))::regclass
           AND NOT EXISTS (SELECT 1 FROM pg_constraint k
                            WHERE k.conindid = i.indexrelid AND k.conrelid = i.indrelid
                              AND k.contype IN ('p', 'u', 'x'))
         ORDER BY 1
    """), {"t": tabela}).scalars().all()
    return [re.sub(r"^CREATE (UNIQUE )?INDEX ", r"CREATE \1INDEX IF NOT EXISTS ", d) for d in defs]


def _sequencia(conn, nome_qualificado: str) -> str:
    tipo, inicio, passo, minimo, maximo, cache, ciclo = conn.execute(text("""
        SELECT format_type(seqtypid, NULL), seqstart, seqincrement, seqmin, seqmax,
               seqcache, seqcycle
          FROM pg_sequence WHERE seqrelid = CAST(:s AS regclass)
    """), {"s": nome_qualificado}).one()
    return (f"CREATE SEQUENCE IF NOT EXISTS {nome_qualificado} AS {tipo} "
            f"INCREMENT {passo} MINVALUE {minimo} MAXVALUE {maximo} START {inicio} "
            f"CACHE {cache} {'CYCLE' if ciclo else 'NO CYCLE'}")


def _linhas_sem_dono(conn, locais: list[str]) -> list[str]:
    com_dono = set(conn.execute(text("""
        SELECT c.relname FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid
         WHERE c.relnamespace = 'public'::regnamespace AND a.attname = 'user_id'
           AND NOT a.attisdropped
    """)).scalars().all())
    saida = []
    for tabela in locais:
        if tabela in com_dono and conn.execute(text(
                f"SELECT EXISTS (SELECT 1 FROM public.{q(tabela)} WHERE user_id IS NULL)")).scalar():
            saida.append(tabela)
    return saida


# ------------------------------------------------------------------ plano

def montar_plano(principal, fdw: dict, papeis_da_api: bool = True) -> Plano:
    """Lê o catálogo do principal e devolve o SQL que prepara o novos.

    ``fdw``: host, port, dbname, user, password -- como o novos alcança o
    principal. ``papeis_da_api``: o banco novos tem ``anon``/``authenticated``
    (todo projeto Supabase tem).
    """
    with principal.connect() as conn:
        conn.execute(text("SET search_path = ''"))
        locais = tabelas_locais(conn)
        visoes = visoes_locais(conn, locais)
        remotos = esquemas_remotos(conn)
        colunas = {t: _colunas(conn, t) for t in locais}
        restricoes = {t: _restricoes(conn, t) for t in locais}
        indices = {t: _indices(conn, t) for t in locais}
        sequencias: dict[str, tuple[str, str]] = {}
        for t in locais:
            for col in colunas[t]:
                achado = _NEXTVAL.search(col["padrao"] or "")
                if achado:
                    sequencias[achado.group(1)] = (t, col["nome"])
        ddl_seq = {s: _sequencia(conn, s) for s in sequencias}
        sem_dono = _linhas_sem_dono(conn, locais)
        extensoes = set(conn.execute(text("SELECT extname FROM pg_catalog.pg_extension")).scalars())

    plano = Plano(locais=locais, visoes_locais=[v for v, _ in visoes],
                  esquemas_remotos=remotos)
    c = plano.comandos
    c.append("CREATE SCHEMA IF NOT EXISTS extensions")
    c.append("CREATE EXTENSION IF NOT EXISTS postgres_fdw WITH SCHEMA extensions")
    if "vector" in extensoes:  # colunas ``vector`` das tabelas locais
        c.append("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")

    # Visões locais saem antes: a camada remota cai inteira logo abaixo.
    for visao in reversed(plano.visoes_locais):
        c.append(f"DROP VIEW IF EXISTS public.{q(visao)}")

    c.extend(ddl_seq.values())
    for t in locais:
        defs = ",\n  ".join(_definicao_coluna(col) for col in colunas[t])
        c.append(f"CREATE TABLE IF NOT EXISTS public.{q(t)} (\n  {defs}\n)")
        for col in colunas[t]:  # migration que só somou coluna
            c.append(f"ALTER TABLE public.{q(t)} ADD COLUMN IF NOT EXISTS "
                     f"{_definicao_coluna(col)}")
    for seq, (t, coluna) in sequencias.items():
        c.append(f"ALTER SEQUENCE {seq} OWNED BY public.{q(t)}.{q(coluna)}")

    # Restrições: PK/únicas/checagens antes, FKs depois e só entre locais
    # (FK para ``assets`` e afins apontaria para tabela estrangeira).
    for fase in ("puxc", "f"):
        for t in locais:
            for nome, tipo, definicao, referida in restricoes[t]:
                if tipo not in fase:
                    continue
                if tipo == "f" and referida not in locais:
                    continue
                c.append(
                    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = "
                    f"{lit(nome)} AND conrelid = 'public.{q(t)}'::regclass) THEN "
                    f"ALTER TABLE public.{q(t)} ADD CONSTRAINT {q(nome)} {definicao}; "
                    "END IF; END $$")
    for t in locais:
        c.extend(indices[t])

    # Camada remota, refeita do zero a cada execução.
    c.append(f"DROP SERVER IF EXISTS {SERVIDOR} CASCADE")
    c.append(
        f"CREATE SERVER {SERVIDOR} FOREIGN DATA WRAPPER postgres_fdw OPTIONS ("
        f"host {lit(fdw['host'])}, port {lit(fdw['port'])}, dbname {lit(fdw['dbname'])}, "
        f"sslmode {lit(fdw.get('sslmode', 'require'))}, fetch_size '5000')")
    mapeamento = ("CREATE USER MAPPING FOR CURRENT_USER SERVER {s} OPTIONS "
                  "(user {u}, password {p})")
    plano.secretos[len(c)] = mapeamento.format(s=SERVIDOR, u=lit(fdw["user"]), p="'***'")
    c.append(mapeamento.format(s=SERVIDOR, u=lit(fdw["user"]), p=lit(fdw["password"])))
    excluidas = locais + plano.visoes_locais
    for esquema in remotos:
        if esquema != "public":
            c.append(f"CREATE SCHEMA IF NOT EXISTS {q(esquema)}")
        filtro = ""
        if esquema == "public" and excluidas:
            filtro = " EXCEPT (" + ", ".join(q(t) for t in excluidas) + ")"
        c.append(f"IMPORT FOREIGN SCHEMA {q(esquema)}{filtro} "
                 f"FROM SERVER {SERVIDOR} INTO {q(esquema)}")

    c.extend(sql for _, sql in visoes)

    # Linhas sem dono (categorias do sistema), lidas do principal pelo fdw.
    if sem_dono:
        c.append(f"DROP SCHEMA IF EXISTS {ORIGEM} CASCADE")
        c.append(f"CREATE SCHEMA {ORIGEM}")
        c.append("IMPORT FOREIGN SCHEMA public LIMIT TO ("
                 + ", ".join(q(t) for t in sem_dono)
                 + f") FROM SERVER {SERVIDOR} INTO {ORIGEM}")
        for t in sem_dono:
            cols = ", ".join(q(col["nome"]) for col in colunas[t] if col["gerada"] != "s")
            c.append(f"INSERT INTO public.{q(t)} ({cols}) SELECT {cols} FROM {ORIGEM}.{q(t)} "
                     "WHERE user_id IS NULL ON CONFLICT DO NOTHING")
        c.append(f"DROP SCHEMA {ORIGEM} CASCADE")
    for seq, (t, coluna) in sequencias.items():
        c.append(f"SELECT setval({lit(seq)}, COALESCE((SELECT max({q(coluna)}) "
                 f"FROM public.{q(t)}), 0) + 1, false)")

    # Nada exposto pela API de dados do Supabase.
    for t in locais:
        c.append(f"ALTER TABLE public.{q(t)} ENABLE ROW LEVEL SECURITY")
    if papeis_da_api:
        papeis = ", ".join(_PAPEIS_DA_API)
        for esquema in remotos:
            c.append(f"REVOKE ALL ON ALL TABLES IN SCHEMA {q(esquema)} FROM {papeis}")
            c.append(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {q(esquema)} FROM {papeis}")
            if esquema != "public":
                c.append(f"REVOKE ALL ON SCHEMA {q(esquema)} FROM {papeis}")
        c.append(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {papeis}")
        c.append(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {papeis}")
    return plano


def imprimir(plano: Plano) -> str:
    return "\n\n".join(plano.secretos.get(i, sql) + ";" for i, sql in enumerate(plano.comandos))


def aplicar(novos, plano: Plano) -> None:
    """Executa tudo numa transação: ou o novos fica pronto, ou nada muda."""
    with novos.begin() as conn:
        cursor = conn.connection.cursor()
        for i, sql in enumerate(plano.comandos):
            try:
                cursor.execute(sql)  # sem parâmetros: '%' das visões passa intacto
            except Exception as exc:
                mostrado = plano.secretos.get(i, sql)
                raise RuntimeError(f"falhou no comando {i + 1}:\n{mostrado[:600]}") from exc


def conferir(novos, plano: Plano) -> dict[str, int]:
    with novos.connect() as conn:
        locais = conn.execute(text("""
            SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace
               AND relkind = 'r' AND relname = ANY(:t)"""), {"t": plano.locais}).scalar()
        estrangeiras = conn.execute(text(
            "SELECT count(*) FROM pg_foreign_table")).scalar()
        visoes = conn.execute(text("""
            SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace
               AND relkind = 'v' AND relname = ANY(:v)"""), {"v": plano.visoes_locais}).scalar()
        perfis = conn.execute(text("SELECT count(*) FROM public.profiles")).scalar()
    return {"tabelas_locais": locais, "tabelas_estrangeiras": estrangeiras,
            "visoes_locais": visoes, "perfis": perfis}


def _partes_fdw(url: str) -> dict:
    u = make_url(url)
    if not u.host or not u.username or u.password is None:
        raise SystemExit("--fdw-url precisa de host, usuário e senha")
    sslmode = u.query.get("sslmode") or "require"
    return {"host": u.host, "port": str(u.port or 5432), "dbname": u.database or "postgres",
            "user": u.username, "password": u.password,
            "sslmode": sslmode if isinstance(sslmode, str) else sslmode[0]}


def _mesmo_banco(a: str, b: str) -> bool:
    """Mesmo host, porta, usuário e base. No pooler do Supabase o host é
    compartilhado e o projeto vai no usuário (``postgres.<ref>``)."""
    def chave(url):
        u = make_url(url)
        return (u.host, u.port or 5432, u.username, u.database or "postgres")
    return chave(a) == chave(b)


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv
    load_dotenv(RAIZ / ".env")
    from core.config import settings
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--principal", default=settings.db_url or None)
    p.add_argument("--novos", default=settings.db_url_novos or None)
    p.add_argument("--fdw-url", default=None,
                   help="como o novos alcança o principal (padrão: --principal)")
    p.add_argument("--sem-papeis-da-api", action="store_true",
                   help="banco sem anon/authenticated (Postgres puro, testes)")
    p.add_argument("--aplicar", action="store_true", help="executa no banco novos")
    a = p.parse_args(argv)
    if not a.principal:
        raise SystemExit("falta a URL do principal (settings.db_url ou --principal)")
    fdw = _partes_fdw(a.fdw_url or a.principal)
    principal = create_engine(a.principal, pool_pre_ping=True)
    plano = montar_plano(principal, fdw, papeis_da_api=not a.sem_papeis_da_api)
    print(f"-- {len(plano.locais)} tabelas locais, {len(plano.visoes_locais)} visões locais, "
          f"esquemas remotos: {', '.join(plano.esquemas_remotos)}", file=sys.stderr)
    if not a.aplicar:
        print(imprimir(plano))
        return 0
    if not a.novos:
        raise SystemExit("falta a URL do novos (SUPABASE_NOVOS_URL ou --novos)")
    if _mesmo_banco(a.novos, a.principal):
        raise SystemExit("--novos aponta para o principal: recusado")
    novos = create_engine(a.novos, pool_pre_ping=True)
    aplicar(novos, plano)
    print(conferir(novos, plano))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
