"""
scripts/gerar_embeddings_chunks.py
Gera embeddings OpenAI para chunks CVM sem embedding e persiste no banco App1.

Uso:
    python scripts/gerar_embeddings_chunks.py
    python scripts/gerar_embeddings_chunks.py --tickers VALE3 PETR4
    python scripts/gerar_embeddings_chunks.py --batch 200 --limit 1000
    python scripts/gerar_embeddings_chunks.py --dry-run

Variáveis de ambiente necessárias:
  - SUPABASE_DB_URL_B3  : banco App1 (Supabase) — leitura + escrita
  - OPENAI_API_KEY      : chave da API OpenAI

O script é idempotente: só processa chunks onde embedding IS NULL.
Após a execução, core/rag_b3.py ativa automaticamente a busca semântica.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gerar_embeddings")

_EMBED_MODEL = "text-embedding-3-small"
_EMBED_DIMS  = 1536
_RETRY_WAIT  = 5   # segundos entre tentativas em rate-limit
_MAX_RETRIES = 3


# ─────────────────────────────────────────────────────────────────────────────
# Conexão
# ─────────────────────────────────────────────────────────────────────────────

def _get_engine():
    url = os.getenv("SUPABASE_DB_URL_B3") or os.getenv("SUPABASE_DB_URL")
    if not url:
        logger.error("SUPABASE_DB_URL_B3 não definida. Exporte a variável e tente novamente.")
        sys.exit(1)
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 30})


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI
# ─────────────────────────────────────────────────────────────────────────────

def _get_openai_client():
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai não instalado. Execute: pip install openai")
        sys.exit(1)

    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        logger.error("OPENAI_API_KEY não definida. Exporte a variável e tente novamente.")
        sys.exit(1)

    return OpenAI(api_key=key, timeout=120)


def _embed_batch(client, texts: list[str]) -> list[list[float]] | None:
    """Gera embeddings para um lote de textos. Retorna None em falha permanente."""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = client.embeddings.create(model=_EMBED_MODEL, input=texts)
            return [item.embedding for item in resp.data]
        except Exception as exc:
            msg = str(exc).lower()
            if "rate" in msg or "429" in msg:
                wait = _RETRY_WAIT * attempt
                logger.warning("Rate-limit (tentativa %d/%d) — aguardando %ds…", attempt, _MAX_RETRIES, wait)
                time.sleep(wait)
            else:
                logger.error("Erro OpenAI: %s", exc)
                return None
    return None


def _to_pgvector(emb: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.10f}" for x in emb) + "]"


# ─────────────────────────────────────────────────────────────────────────────
# Contagem e seleção
# ─────────────────────────────────────────────────────────────────────────────

def _count_pending(conn, tickers: list[str] | None) -> int:
    if tickers:
        ph  = ", ".join(f":tk{i}" for i in range(len(tickers)))
        sql = f"SELECT COUNT(*) FROM public.docs_corporativos_chunks WHERE embedding IS NULL AND UPPER(ticker) IN ({ph})"
        params = {f"tk{i}": tk.upper() for i, tk in enumerate(tickers)}
    else:
        sql    = "SELECT COUNT(*) FROM public.docs_corporativos_chunks WHERE embedding IS NULL"
        params = {}
    return int(conn.execute(text(sql), params).scalar() or 0)


def _fetch_pending(conn, tickers: list[str] | None, batch: int, last_id: int) -> list[tuple[int, str]]:
    """Retorna lista de (id, chunk_text) sem embedding, paginando por id > last_id."""
    if tickers:
        ph  = ", ".join(f":tk{i}" for i in range(len(tickers)))
        where_tk = f"AND UPPER(ticker) IN ({ph})"
        params   = {f"tk{i}": tk.upper() for i, tk in enumerate(tickers)}
    else:
        where_tk = ""
        params   = {}

    params["lim"]     = batch
    params["last_id"] = last_id

    sql = f"""
        SELECT id, chunk_text
        FROM public.docs_corporativos_chunks
        WHERE embedding IS NULL
          AND id > :last_id
          {where_tk}
        ORDER BY id
        LIMIT :lim
    """
    rows = conn.execute(text(sql), params).fetchall()
    return [(int(r[0]), r[1] or "") for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Atualização
# ─────────────────────────────────────────────────────────────────────────────

def _update_embeddings(conn, pairs: list[tuple[int, list[float]]]) -> int:
    """
    Persiste embeddings no banco.
    Usa UPDATE com parâmetro ::vector para compatibilidade pgvector.
    """
    updated = 0
    for chunk_id, emb in pairs:
        vec_literal = _to_pgvector(emb)
        conn.execute(
            text("""
                UPDATE public.docs_corporativos_chunks
                SET embedding = (:vec)::vector
                WHERE id = :id AND embedding IS NULL
            """),
            {"vec": vec_literal, "id": chunk_id},
        )
        updated += 1
    return updated


# ─────────────────────────────────────────────────────────────────────────────
# Verificação de pré-requisitos
# ─────────────────────────────────────────────────────────────────────────────

def _check_prerequisites(conn) -> bool:
    try:
        exists = conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = 'docs_corporativos_chunks'
            )
        """)).scalar()
        if not exists:
            logger.error("Tabela docs_corporativos_chunks não encontrada no banco.")
            return False

        # Verifica se a coluna embedding existe
        col = conn.execute(text("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'docs_corporativos_chunks'
              AND column_name  = 'embedding'
        """)).scalar()
        if not int(col or 0):
            logger.error("Coluna 'embedding' não encontrada em docs_corporativos_chunks.")
            return False

        return True
    except Exception as exc:
        logger.error("Erro ao verificar pré-requisitos: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Loop principal
# ─────────────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    engine  = _get_engine()
    client  = _get_openai_client()

    tickers = [tk.upper() for tk in args.tickers] if args.tickers else None
    batch   = args.batch
    limit   = args.limit  # None = sem limite
    dry_run = args.dry_run

    with engine.connect() as conn:
        if not _check_prerequisites(conn):
            sys.exit(1)

        total_pending = _count_pending(conn, tickers)
        if limit:
            total_pending = min(total_pending, limit)

        if total_pending == 0:
            logger.info("Nenhum chunk sem embedding encontrado. Nada a fazer.")
            return

        ticker_msg = f" para {tickers}" if tickers else ""
        logger.info("Chunks sem embedding%s: %d", ticker_msg, total_pending)
        if dry_run:
            logger.info("[DRY-RUN] Nenhuma escrita será realizada.")

        processed = 0
        errors    = 0
        last_id   = 0

        while processed < total_pending:
            remaining = total_pending - processed
            fetch_n   = min(batch, remaining)

            rows = _fetch_pending(conn, tickers, fetch_n, last_id)
            if not rows:
                break

            ids   = [r[0] for r in rows]
            texts = [r[1] for r in rows]

            # Filtra textos vazios (sem embedding possível)
            valid = [(i, t) for i, t in zip(ids, texts) if t.strip()]
            if not valid:
                last_id    = ids[-1]
                processed += len(rows)
                continue

            valid_ids   = [v[0] for v in valid]
            valid_texts = [v[1] for v in valid]

            logger.info(
                "Processando lote %d–%d / %d (%d textos válidos)…",
                processed + 1, processed + len(rows), total_pending, len(valid_texts),
            )

            embeddings = _embed_batch(client, valid_texts)
            if embeddings is None:
                logger.error("Falha permanente no lote — pulando %d chunks.", len(valid_texts))
                errors    += len(valid_texts)
                last_id    = ids[-1]
                processed += len(rows)
                continue

            pairs = list(zip(valid_ids, embeddings))

            if not dry_run:
                n = _update_embeddings(conn, pairs)
                conn.commit()
                logger.info("  → %d embeddings salvos.", n)
            else:
                logger.info("  → [DRY-RUN] %d embeddings gerados (não salvos).", len(pairs))

            last_id    = ids[-1]
            processed += len(rows)

            # Pequena pausa para não sobrecarregar a API
            if processed < total_pending:
                time.sleep(0.5)

    logger.info(
        "Concluído: %d chunks processados, %d erros. "
        "Execute o app — busca semântica será ativada automaticamente.",
        processed - errors, errors,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Gera embeddings OpenAI para chunks CVM e salva no banco App1.",
    )
    p.add_argument(
        "--tickers", nargs="+", metavar="TICKER",
        help="Processar apenas esses tickers (ex: VALE3 PETR4). Padrão: todos.",
    )
    p.add_argument(
        "--batch", type=int, default=100, metavar="N",
        help="Tamanho do lote por requisição OpenAI (padrão: 100).",
    )
    p.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Máximo de chunks a processar nesta execução (padrão: sem limite).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Gera embeddings mas NÃO salva no banco.",
    )
    return p.parse_args()


if __name__ == "__main__":
    run(_parse_args())
