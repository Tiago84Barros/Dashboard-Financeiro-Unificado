"""
Backfill histórico de documentos CVM/IPE com MIX BALANCEADO por empresa.

O job diário (data_pipeline/jobs/update_cvm_ipe.py) mantém o incremental do ano
corrente com teto baixo. Este script faz a carga inicial / extensão de anos
anteriores de forma reproduzível, com seleção balanceada entre categorias para
não deixar uma categoria de alta frequência (ex.: Assembleia) afogar Fato
Relevante / Resultados na avaliação da LLM.

Estratégia:
  1. Mapa codigo_cvm → ticker do universo (reusa _codigo_to_ticker do job:
     registro oficial cvm_to_ticker ∩ setores).
  2. Baixa o(s) ano(s) do IPE (CVM Dados Abertos, .zip → CSV) e filtra pelas
     categorias relevantes (core.cvm_ipe.RELEVANT_CATEGORIES) do universo.
  3. Por empresa, seleciona até --per-ticker docs em ROUND-ROBIN entre categorias
     (1 mais recente de cada, depois a 2ª de cada, ...), priorizando os tipos mais
     analíticos no desempate.
  4. Insere em lote (docs + chunk-resumo RAG-visível), append-only, dedup por URL
     e ON CONFLICT. chunk_hash inclui a URL (evita colisão por metadados iguais).

Seguro: dry-run por padrão (mostra o que faria). Use --apply para gravar.

DESTINO: o armazém local (Docker `dfu_warehouse`), verificado por `exigir_local`.
O corpus e seus chunks não cabem no Supabase — `docs_corporativos_chunks` foi
eliminada de lá para liberar 162 MB, e a produção lê o corpus do Parquet
publicado por `scripts/publish_rag_corpus_parquet.py`, não do Postgres. Passar
`--destino remoto` existe para depuração e grava no Supabase.

RECORTE: por padrão só as categorias analíticas (`--categorias resultado,fato,
provento`). Sem recorte, subir o teto por empresa enche a cota com Assembleia e
Comunicado ao Mercado, que juntos têm mais documentos disponíveis do que todas
as categorias analíticas somadas.

ANO A ANO: `select_balanced` ordena por recência dentro de cada categoria. Rodar
2023..2026 de uma vez com teto alto entrega quase só o ano corrente — foi assim
que o corpus original ficou 71% de 2026. Rode um ano por execução com teto
pequeno; a dedup por URL faz as execuções se somarem.

Exemplos:
  python scripts/backfill_cvm_ipe.py --years 2025                       # dry-run
  for y in 2023 2024 2025 2026; do       python scripts/backfill_cvm_ipe.py --years $y --per-ticker 4 --apply; done
  python scripts/backfill_cvm_ipe.py --years 2023 --categorias todas --per-ticker 20 --apply

Depois de inserir, drene a extração de texto com
`python scripts/drenar_cvm_fulltext.py` e republique o Parquet.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.cvm_ipe as ipe  # noqa: E402
from core.b3_db import _resolve_url  # noqa: E402
from core.destino_local import exigir_local  # noqa: E402
from data_pipeline.jobs.update_cvm_ipe import _codigo_to_ticker  # noqa: E402

logger = logging.getLogger("backfill_cvm_ipe")

# Round-robin entre categorias: ordem dá leve prioridade ao mais analítico.
_BUCKET_ORDER = ["resultado", "fato", "comunicado", "provento",
                 "assembleia", "capital", "critico", "outro"]

#: Recorte analítico: o que um leitor de RI procura (release de resultados,
#: apresentação a analistas, fato relevante, provento). Medido no IPE de 2025
#: para as 292 empresas do corpus: `Assembleia` tinha 4.511 documentos
#: disponíveis e `Comunicado ao Mercado` 4.322, contra 2.977 de
#: `Dados Econômico-Financeiros`. Subir o teto por empresa SEM recorte enche a
#: cota com convocação de assembleia e aviso de data -- volume que afoga o RAG
#: sem acrescentar leitura. O default desta lista é o recorte; passar
#: ``--categorias todas`` restaura o comportamento antigo.
BUCKETS_ANALITICOS = ("resultado", "fato", "provento")


def filtrar_buckets(docs: list[dict], buckets: tuple[str, ...]) -> list[dict]:
    """Mantém só os documentos cujos buckets foram pedidos.

    Filtra ANTES de :func:`select_balanced` de propósito: o round-robin
    distribui a cota entre os buckets presentes, então tirar os volumosos aqui
    faz a mesma cota render documento analítico em vez de administrativo.
    """
    if not buckets:
        return docs
    alvo = set(buckets)
    return [d for d in docs if _bucket(d.get("categoria")) in alvo]


def _bucket(cat: str) -> str:
    c = (cat or "").lower()
    if "fato relevante" in c:
        return "fato"
    if "dados econ" in c or "press" in c:
        return "resultado"
    if "comunicado ao mercado" in c:
        return "comunicado"
    if "aviso aos acionistas" in c or "proventos" in c:
        return "provento"
    if "assembleia" in c:
        return "assembleia"
    if "oferta" in c or "deb" in c:
        return "capital"
    if "recupera" in c or "opa" in c:
        return "critico"
    return "outro"


def _doc_date(d: dict) -> date:
    return d.get("data_entrega") or d.get("data_referencia") or date.min


def select_balanced(docs: list[dict], per_ticker: int) -> list[dict]:
    """Round-robin entre categorias, recência dentro de cada, até o teto/empresa."""
    by_tk: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for d in docs:
        by_tk[d["ticker"]][_bucket(d.get("categoria"))].append(d)
    sel: list[dict] = []
    for buckets in by_tk.values():
        for lst in buckets.values():
            lst.sort(key=_doc_date, reverse=True)
        picked: list[dict] = []
        rank = 0
        while len(picked) < per_ticker:
            progressed = False
            for b in _BUCKET_ORDER:
                lst = buckets.get(b)
                if lst and rank < len(lst):
                    picked.append(lst[rank])
                    progressed = True
                    if len(picked) >= per_ticker:
                        break
            if not progressed:
                break
            rank += 1
        sel.extend(picked)
    return sel


def _engine(destino: str):
    """Resolve o banco de destino. Default: armazém local.

    O corpus e seus chunks NÃO cabem no Supabase — `docs_corporativos_chunks`
    foi eliminada de lá justamente para liberar 162 MB, e o plano free vive
    perto do teto de 500 MB. Gravar chunk de backfill no Supabase recriaria a
    tabela derrubada. Por isso o destino local é o default e é verificado por
    `exigir_local` antes de qualquer escrita, e não apenas prometido no help.
    """
    if destino == "local":
        from scripts.publish_fii_selection_from_local import _warehouse_url
        url = _warehouse_url()
        if not url:
            logger.error("Armazém local indisponível (container dfu_warehouse no ar?).")
            return None
    else:
        url = _resolve_url()
        if not url:
            logger.error("Banco não configurado (SUPABASE_DB_URL_B3 / SUPABASE_DB_URL).")
            return None
    _ssl = {} if ("localhost" in url or "127.0.0.1" in url) else {"sslmode": "require"}
    eng = create_engine(url, connect_args={"connect_timeout": 20, **_ssl})
    if destino == "local":
        exigir_local(eng, o_que="backfill CVM/IPE (docs + chunks)")
    return eng


def run(years: list[int], per_ticker: int, apply: bool, clean_previous: bool,
        buckets: tuple[str, ...] = BUCKETS_ANALITICOS, destino: str = "local") -> int:
    eng = _engine(destino)
    if eng is None:
        return 1
    logger.info("destino: %s", "armazém local (dfu_warehouse)" if destino == "local" else "REMOTO")

    with eng.connect() as conn:
        cod_map = _codigo_to_ticker(conn)
        existing = {r[0] for r in conn.execute(text(
            "SELECT url FROM public.docs_corporativos WHERE url IS NOT NULL")).fetchall() if r[0]}
    logger.info("mapa codigo_cvm→ticker: %d | urls existentes: %d", len(cod_map), len(existing))
    if not cod_map:
        logger.error("Mapa vazio — verifique a tabela cvm_to_ticker.")
        return 1

    # Coleta + filtra + dedup
    docs: list[dict] = []
    for y in years:
        content = ipe.fetch_ipe_csv(y)
        if not content:
            logger.warning("ano %s sem CSV (download falhou)", y)
            continue
        for d in ipe.filter_docs(ipe.parse_ipe_csv(content), cod_map):
            if d["url"] not in existing:
                docs.append(d)
    logger.info("documentos novos (todos): %d", len(docs))

    if buckets:
        antes = len(docs)
        docs = filtrar_buckets(docs, buckets)
        logger.info("recorte por categoria (%s): %d de %d",
                    ",".join(buckets), len(docs), antes)

    sel = select_balanced(docs, per_ticker)
    cats = Counter(d.get("categoria", "") for d in sel)
    n_emp = len({d["ticker"] for d in sel})
    logger.info("selecionados (mix balanceado, <=%d/empresa): %d em %d empresas",
                per_ticker, len(sel), n_emp)
    for cat, n in cats.most_common(12):
        logger.info("  %5d  %s", n, cat)

    if not apply:
        logger.info("DRY-RUN — nada gravado. Use --apply para inserir.")
        return 0

    raw = eng.raw_connection()
    if clean_previous:
        try:
            cur = raw.cursor()
            cur.execute("DELETE FROM public.docs_corporativos_chunks "
                        "WHERE ingestion_run_id LIKE 'cvm_ipe_backfill_%'")
            ch = cur.rowcount
            cur.execute("DELETE FROM public.docs_corporativos "
                        "WHERE ingestion_run_id LIKE 'cvm_ipe_backfill_%'")
            dd = cur.rowcount
            raw.commit()
            cur.close()
            logger.info("limpeza de backfills anteriores: -%d docs / -%d chunks", dd, ch)
        except Exception as exc:
            raw.rollback()
            logger.error("limpeza falhou: %s", exc)

    from psycopg2.extras import execute_values
    run_id = f"cvm_ipe_backfill_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    ins_docs = ins_chunks = 0
    try:
        cur = raw.cursor()
        BATCH = 500
        for i in range(0, len(sel), BATCH):
            rows = []
            for d in sel[i:i + BATCH]:
                meta = ipe.metadata_text(d)
                dt = d.get("data_entrega") or d.get("data_referencia")
                rows.append((
                    d["ticker"], dt, ipe.SOURCE_NAME,
                    (d.get("tipo") or d.get("categoria") or "Documento")[:300],
                    (d.get("assunto") or d.get("categoria") or "")[:500],
                    d["url"], meta, "pt", ipe.sha256(d["ticker"], d["url"], dt),
                    d.get("codigo_cvm"), (d.get("categoria") or "")[:300], dt,
                    ipe.sha256(meta), run_id, "ipe_meta_v1",
                ))
            got = execute_values(cur, """
                INSERT INTO public.docs_corporativos
                  (ticker, data, fonte, tipo, titulo, url, raw_text, lang, doc_hash,
                   codigo_cvm, categoria, document_date, content_hash,
                   ingestion_run_id, extraction_version)
                VALUES %s ON CONFLICT DO NOTHING
                RETURNING id, url, ticker, raw_text, categoria, document_date
            """, rows, fetch=True)
            ins_docs += len(got)
            if got:
                chunk_rows = [(gid, gtk, 0, graw, ipe.sha256("chunk", gurl, graw),
                               (gcat or "")[:300], gdt, "ipe_meta_v1", run_id)
                              for (gid, gurl, gtk, graw, gcat, gdt) in got]
                execute_values(cur, """
                    INSERT INTO public.docs_corporativos_chunks
                      (doc_id, ticker, chunk_index, chunk_text, chunk_hash, categoria,
                       document_date, chunking_version, ingestion_run_id)
                    VALUES %s ON CONFLICT (chunk_hash) DO NOTHING
                """, chunk_rows)
                ins_chunks += len(chunk_rows)
            raw.commit()
            logger.info("lote %d: +%d docs (acumulado %d)", i // BATCH + 1, len(got), ins_docs)
        cur.close()
    finally:
        raw.close()

    # invalida cobertura RAG em cache (se Streamlit ativo)
    try:
        from core.rag_b3 import get_cobertura_docs
        get_cobertura_docs.clear()
    except Exception:
        pass

    logger.info("FIM: %d docs + %d chunks inseridos | run=%s", ins_docs, ins_chunks, run_id)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill CVM/IPE com mix balanceado por empresa.")
    ap.add_argument("--years", default="2024,2025,2026",
                    help="Anos do IPE a coletar, separados por vírgula (default: 2024,2025,2026).")
    ap.add_argument("--per-ticker", type=int, default=30,
                    help="Teto de documentos por empresa (default: 30).")
    ap.add_argument("--apply", action="store_true",
                    help="Grava no banco. Sem isso, roda em dry-run.")
    ap.add_argument("--clean-previous", action="store_true",
                    help="Remove backfills anteriores (ingestion_run_id 'cvm_ipe_backfill_%%') antes de inserir.")
    ap.add_argument("--categorias", default=",".join(BUCKETS_ANALITICOS),
                    help=("Buckets de categoria a coletar, separados por vírgula "
                          f"(default: {','.join(BUCKETS_ANALITICOS)}). "
                          f"Valores: {','.join(_BUCKET_ORDER)}. Use 'todas' para não recortar."))
    ap.add_argument("--destino", choices=["local", "remoto"], default="local",
                    help="Banco de destino (default: local — o armazém Docker; 'remoto' grava no Supabase).")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    years = [int(y) for y in str(args.years).split(",") if y.strip().isdigit()]
    if not years:
        logger.error("Nenhum ano válido em --years.")
        return 1
    if str(args.categorias).strip().lower() in ("todas", "todos", ""):
        buckets: tuple[str, ...] = ()
    else:
        buckets = tuple(b.strip().lower() for b in str(args.categorias).split(",") if b.strip())
        invalidos = [b for b in buckets if b not in _BUCKET_ORDER]
        if invalidos:
            logger.error("Categorias inválidas em --categorias: %s (válidas: %s)",
                         ",".join(invalidos), ",".join(_BUCKET_ORDER))
            return 1
    return run(years, args.per_ticker, args.apply, args.clean_previous, buckets, args.destino)


if __name__ == "__main__":
    raise SystemExit(main())
