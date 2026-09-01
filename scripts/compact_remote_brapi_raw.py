"""Compacta no Supabase o cache BRAPI bruto já arquivado no warehouse local."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from core.config import settings
from scripts.archive_remote_brapi_raw import chave_de_payload, chaves_sem_manifesto
from scripts.publish_fii_selection_from_local import _warehouse_url

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Janela padrão de retenção. Cobre com folga o único consumidor que olha para
# trás por tempo: o cache de `fii_v2_*` em fii_ingest, de 6 horas.
JANELA_HORAS_PADRAO = 48

# Até 01/09/2026 a compactação era `DROP TABLE ... CASCADE` + `CREATE TABLE`.
# Ela liberava espaço na hora, e cobrava caro por isso: `id` é BIGSERIAL, então
# a sequência REINICIAVA em 1 e toda referência gravada por fora passava a
# apontar para outra geração de payload. Duas vítimas medidas em 01/09/2026:
# o manifesto de arquivamento (6.851 ids colidindo) e 84.116 linhas das
# demonstrações cujo `raw_payload_id` resolvia para um payload coletado DEPOIS
# da própria linha. Nada disso dava erro -- o endpoint apontado era o certo.
# DELETE preserva a sequência e, portanto, o significado dos ids. O custo é
# precisar de VACUUM FULL depois para devolver o espaço ao disco.
def _colunas_que_referenciam(conn) -> list[tuple[str, str]]:
    """Quem guarda id de payload, LIDO DO CATÁLOGO -- nunca de uma lista fixa.

    Enumerar à mão o que preservar já apagou dado neste projeto (a coorte
    preferida sumiu porque não estava na lista branca). Uma tabela nova com
    `raw_payload_id` tem de entrar aqui sozinha, sem ninguém lembrar dela.
    """
    return [(r[0], r[1]) for r in conn.execute(text("""
        SELECT table_schema, table_name
        FROM information_schema.columns
        WHERE column_name = 'raw_payload_id'
          AND table_schema NOT IN ('pg_catalog','information_schema')
        ORDER BY 1, 2
    """)).all()]


def _sql_de_poda(referencias: list[tuple[str, str]], *, contar: bool = False) -> str:
    """Poda do que nenhum consumidor lê. Ver o preservado em cada CTE.

    `contar=True` devolve a MESMA seleção como SELECT count(*), para a simulação
    poder mostrar o número exato que o --apply vai remover. Um dry run que monta
    a consulta por conta própria mede outra coisa que não a que vai rodar.
    """
    if referencias:
        referenciados = "\n            UNION\n            ".join(
            f"SELECT raw_payload_id AS id FROM {schema}.{tabela} "
            f"WHERE raw_payload_id IS NOT NULL"
            for schema, tabela in referencias)
    else:
        referenciados = "SELECT NULL::bigint AS id WHERE false"
    return f"""
        WITH ultimo_por_chave AS (
            -- renormalize() e check_dividend_echoes() leem o payload mais
            -- recente por ticker; o fallback de FII lê o mais recente por
            -- ticker em 'quote'. Nenhum deles olha para histórico.
            SELECT DISTINCT ON (endpoint, ticker) id
            FROM market.brapi_raw_payloads
            WHERE request_status='success' AND payload_json IS NOT NULL
            ORDER BY endpoint, ticker, id DESC
        ),
        lote_fii AS (
            -- fii_ingest reconstrói a coleta a partir do ÚLTIMO lote de
            -- quote_fii_full; intervalo acima de 5 minutos abre lote novo.
            SELECT MAX(fetched_at) AS inicio FROM (
                SELECT fetched_at,
                       LAG(fetched_at) OVER (ORDER BY fetched_at) AS anterior
                FROM market.brapi_raw_payloads
                WHERE endpoint='quote_fii_full' AND request_status='success'
                  AND payload_json IS NOT NULL
            ) o WHERE anterior IS NULL OR fetched_at - anterior > INTERVAL '5 minutes'
        ),
        referenciados AS (
            {referenciados}
        )
        {"SELECT count(*) FROM market.brapi_raw_payloads p"
         if contar else "DELETE FROM market.brapi_raw_payloads p"}
        WHERE p.id NOT IN (SELECT id FROM ultimo_por_chave)
          AND p.id NOT IN (SELECT id FROM referenciados WHERE id IS NOT NULL)
          AND p.fetched_at < now() - make_interval(hours => :janela)
          AND NOT (p.endpoint='quote_fii_full'
                   AND p.fetched_at >= COALESCE((SELECT inicio FROM lote_fii), now()))
    """


def _engine(url: str, remote: bool):
    parsed = make_url(url)
    if parsed.drivername in {"postgresql", "postgres"}:
        parsed = parsed.set(drivername="postgresql+psycopg2")
    kwargs: dict = {"future": True, "connect_args": {"connect_timeout": 15}}
    if remote:
        parsed = parsed.update_query_dict({"sslmode": "require"})
        kwargs["connect_args"].update(options="-c statement_timeout=300000")
        if os.getenv("SUPABASE_DB_HOSTADDR"):
            kwargs["connect_args"]["hostaddr"] = os.environ["SUPABASE_DB_HOSTADDR"]
        kwargs["poolclass"] = NullPool
    return create_engine(parsed, **kwargs)


def audit(janela_horas: int = JANELA_HORAS_PADRAO) -> dict:
    remote = _engine(settings.db_url, True)
    local = _engine(_warehouse_url(), False)
    query = text("""
        SELECT DISTINCT content_sha256 FROM market.brapi_raw_payloads
        WHERE content_sha256 IS NOT NULL
    """)
    with remote.connect() as conn:
        remote_keys = set(conn.execute(query).scalars())
        remote_rows = [dict(r) for r in conn.execute(text("""
            SELECT endpoint, fetched_at, request_status, ticker, content_sha256
            FROM market.brapi_raw_payloads
        """)).mappings()]
        before = dict(conn.execute(text("""
            SELECT count(*) rows,pg_total_relation_size('market.brapi_raw_payloads') bytes,
                   pg_database_size(current_database()) database_bytes
            FROM market.brapi_raw_payloads
        """)).mappings().one())
        referencias = _colunas_que_referenciam(conn)
        podariam = conn.execute(
            text(_sql_de_poda(referencias, contar=True)),
            {"janela": janela_horas}).scalar_one()
    with local.connect() as conn:
        local_keys = set(conn.execute(query).scalars())
        manifest_keys = {
            chave_de_payload(row) for row in conn.execute(text("""
                SELECT endpoint, fetched_at, request_status, ticker, content_sha256
                FROM market.brapi_remote_archive_manifest
            """)).mappings()
        }
    remote.dispose()
    local.dispose()
    return {
        **before,
        "remote_unique_hashes": len(remote_keys),
        "local_unique_hashes": len(local_keys),
        "remote_hashes_missing_local": len(remote_keys - local_keys),
        "local_manifest_rows": len(manifest_keys),
        # Cobertura por conjunto: o manifesto acumula entre rodadas e guarda
        # payloads que a tabela remota ja podou, entao comparar totais acusaria
        # falta onde ha sobra. A chave nao pode ser o id remoto -- ele reinicia a
        # cada compactacao. Ver chave_de_payload em archive_remote_brapi_raw.
        "remote_payloads_sem_manifesto": len(
            chaves_sem_manifesto(remote_rows, manifest_keys)),
        "janela_horas": janela_horas,
        "tabelas_que_referenciam": [f"{s}.{t}" for s, t in referencias],
        "linhas_que_seriam_podadas": int(podariam),
        "linhas_preservadas": int(before["rows"]) - int(podariam),
    }


def compact(janela_horas: int = JANELA_HORAS_PADRAO) -> dict:
    before = audit(janela_horas)
    if before["remote_hashes_missing_local"]:
        raise RuntimeError("compactação bloqueada: payloads remotos ausentes no local")
    if before["remote_payloads_sem_manifesto"]:
        raise RuntimeError("compactação bloqueada: manifesto remoto incompleto")
    engine = _engine(settings.db_url, True)
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '300s'"))
        removidas = conn.execute(
            text(_sql_de_poda(_colunas_que_referenciam(conn))),
            {"janela": janela_horas}).rowcount
    with engine.connect() as conn:
        after = dict(conn.execute(text("""
            SELECT count(*) rows,pg_total_relation_size('market.brapi_raw_payloads') bytes,
                   pg_database_size(current_database()) database_bytes
            FROM market.brapi_raw_payloads
        """)).mappings().one())
    engine.dispose()
    return {
        "before": before, "after": after, "linhas_removidas": int(removidas or 0),
        # DELETE marca a linha morta, nao devolve disco. Sem o VACUUM FULL o
        # banco continua do mesmo tamanho e a poda parece nao ter funcionado.
        "proximo_passo": "VACUUM FULL ANALYZE market.brapi_raw_payloads;",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--janela-horas", type=int, default=JANELA_HORAS_PADRAO,
                        help="retem tudo coletado nas ultimas N horas "
                             f"(padrao: {JANELA_HORAS_PADRAO})")
    args = parser.parse_args()
    report = (compact(args.janela_horas) if args.apply
              else {"dry_run": True, "audit": audit(args.janela_horas)})
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
