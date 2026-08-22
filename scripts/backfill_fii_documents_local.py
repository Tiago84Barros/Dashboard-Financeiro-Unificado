"""Backfill integral e retomável dos documentos públicos de FIIs.

O banco e a propria fila sao o checkpoint. Por padrao, o worker guarda URL,
hash, versao e evidencias, mas nao retém novos binarios; isso torna viavel
processar todo o catalogo respeitando a reserva local de armazenamento.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-documents", type=int, default=0,
                        help="0 processa ate esgotar a fila elegivel.")
    parser.add_argument("--min-free-gb", type=float, default=5.0)
    parser.add_argument("--max-batch-mb", type=int, default=150)
    parser.add_argument("--max-document-mb", type=int, default=100)
    parser.add_argument("--download-timeout", type=int, default=45)
    parser.add_argument("--download-attempts", type=int, default=2)
    parser.add_argument("--host-failure-threshold", type=int, default=3)
    parser.add_argument("--host-cooldown-minutes", type=int, default=30)
    parser.add_argument("--max-documents-per-host", type=int, default=3)
    parser.add_argument("--retain-binaries", action="store_true")
    parser.add_argument("--checkpoint-every", type=int, default=1000)
    parser.add_argument("--publish-every-checkpoint", action="store_true")
    parser.add_argument("--max-processing-attempts", type=int, default=3)
    parser.add_argument("--sleep-seconds", type=float, default=.25)
    parser.add_argument("--log-file", default="local_staging/logs/fii_document_backfill.jsonl")
    parser.add_argument("--stop-file", default="local_staging/fii_document_backfill.stop")
    parser.add_argument("--pid-file", default="local_staging/fii_document_backfill.pid")
    return parser.parse_args()


def _configure_database() -> str:
    url = os.getenv("WAREHOUSE_DB_URL", "").strip()
    if not url:
        inspection = subprocess.run(
            ["docker", "inspect", "dfu_warehouse", "--format", "{{json .Config.Env}}"],
            check=True, capture_output=True, text=True,
        )
        environment = json.loads(inspection.stdout)
        password_entry = next(
            (item for item in environment if str(item).startswith("POSTGRES_PASSWORD=")),
            None,
        )
        if not password_entry:
            raise RuntimeError("WAREHOUSE_DB_URL e senha do container indisponiveis")
        password = str(password_entry).split("=", 1)[1]
        url = f"postgresql://postgres:{quote_plus(password)}@localhost:5433/postgres"
    # Definido antes de importar core.config/get_engine.
    os.environ["SUPABASE_UNIFICADO_URL"] = url
    os.environ["DATABASE_URL"] = url
    os.environ["SUPABASE_DB_URL"] = url
    return url


def _append_log(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _queue_profile(engine) -> dict:
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT count(*) total,
                   count(*) FILTER (WHERE processing_status='pending') pending,
                   count(*) FILTER (WHERE processing_status='processing') processing,
                   count(*) FILTER (WHERE processing_status='completed') completed,
                   count(*) FILTER (WHERE processing_status='needs_review') needs_review,
                   count(*) FILTER (WHERE processing_status='failed') failed
            FROM market.fii_documents
        """)).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


def _release_abandoned_claims(engine) -> int:
    from sqlalchemy import text

    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE market.fii_documents
               SET processing_status='pending',processing_started_at=NULL,
                   processing_worker=NULL
             WHERE processing_status='processing'
               AND processing_started_at < now()-interval '30 minutes'
        """))
    return max(int(result.rowcount or 0), 0)


def _retry_failed(engine, max_attempts: int) -> int:
    """Reabre falhas transitorias; rejeicoes por tamanho permanecem finais."""
    from sqlalchemy import text

    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE market.fii_documents d
               SET processing_status='pending',next_retry_at=NULL
             WHERE d.processing_status='failed'
               AND d.processing_attempts < :max_attempts
               AND NOT EXISTS (
                 SELECT 1 FROM market.fii_audit_events a
                  WHERE a.event_type='document_rejected_size'
                    AND a.entity_type='fii_document'
                    AND a.entity_id=d.id::text
               )
        """), {"max_attempts": max(int(max_attempts), 1)})
    return max(int(result.rowcount or 0), 0)


def _checkpoint(*, publish_remote: bool = False) -> dict:
    from data_pipeline.market.fii_confidence_pipeline import calibrate_parsers
    from data_pipeline.market.fii_entity_resolution import resolve_entities
    from data_pipeline.market.fii_ingest import reprocess, snapshot_methodology_v4

    report = {}
    for name, function in (
        ("calibration", calibrate_parsers),
        ("entities", resolve_entities),
        ("reprocess", reprocess),
        ("snapshot", snapshot_methodology_v4),
    ):
        try:
            report[name] = function()
        except Exception as exc:  # backfill deve continuar e deixar evidencia
            report[name] = {"status": "failed", "error": str(exc)[:1000]}
    if publish_remote:
        child_environment = os.environ.copy()
        # O processo filho deve reencontrar o Supabase nos secrets; a origem
        # local e descoberta diretamente no container Docker.
        for key in ("SUPABASE_UNIFICADO_URL", "DATABASE_URL", "SUPABASE_DB_URL",
                    "WAREHOUSE_DB_URL"):
            child_environment.pop(key, None)
        try:
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "publish_fii_selection_from_local.py")],
                check=True, capture_output=True, text=True, timeout=300,
                env=child_environment, cwd=str(ROOT),
            )
            output = [line for line in completed.stdout.splitlines() if line.strip()]
            report["remote_publish"] = (json.loads(output[-1]) if output else
                                         {"status": "completed"})
        except Exception as exc:
            report["remote_publish"] = {"status": "failed", "error": str(exc)[:1000]}
    return report


def main() -> int:
    args = _args()
    _configure_database()
    from data_pipeline.market.fii_documents import (
        _cache_root,
        process_pending_documents,
    )
    from data_pipeline.utils.db_utils import get_pipeline_engine

    engine = get_pipeline_engine()
    if engine is None:
        raise RuntimeError("banco local indisponivel")
    log_path = (ROOT / args.log_file).resolve()
    stop_path = (ROOT / args.stop_file).resolve()
    pid_path = (ROOT / args.pid_file).resolve()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text(encoding="utf-8").strip())
            os.kill(existing_pid, 0)
        except (OSError, ValueError):
            pid_path.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"backfill ja esta ativo no PID {existing_pid}")
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    min_free = max(int(args.min_free_gb * 1024**3), 0)
    workers = min(max(int(args.workers), 1), 12)
    maximum = max(int(args.max_documents), 0)
    processed = 0
    next_checkpoint = max(int(args.checkpoint_every), 1)
    released = _release_abandoned_claims(engine)
    _append_log(log_path, {
        "event": "started", "at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(), "workers": workers, "max_documents": maximum,
        "min_free_bytes": min_free, "retain_binaries": bool(args.retain_binaries),
        "released_claims": released, "queue": _queue_profile(engine),
    })

    def one(batch_limit: int) -> dict:
        return process_pending_documents(
            limit=max(int(batch_limit), 1), recent_months=0,
            max_batch_bytes=max(args.max_batch_mb, 1) * 1024**2,
            max_document_bytes=max(args.max_document_mb, 1) * 1024**2,
            min_free_bytes=min_free, retain_binary=bool(args.retain_binaries),
            download_timeout=max(int(args.download_timeout), 5),
            download_attempts=max(int(args.download_attempts), 1),
            host_failure_threshold=max(int(args.host_failure_threshold), 1),
            host_cooldown_minutes=max(int(args.host_cooldown_minutes), 1),
            max_documents_per_host=max(int(args.max_documents_per_host), 1),
        )

    def safe_one(batch_limit: int) -> dict:
        try:
            return one(batch_limit)
        except Exception as exc:
            return {"selected": 0, "extracted": 0, "failed": 1,
                    "worker_error": f"{type(exc).__name__}: {str(exc)[:500]}"}

    empty_rounds = 0
    stopped_reason = "queue_exhausted"
    try:
        while True:
            if stop_path.exists():
                stopped_reason = "stop_file"
                break
            free = shutil.disk_usage(_cache_root()).free
            if free < min_free:
                stopped_reason = "minimum_free_space"
                break
            if maximum and processed >= maximum:
                stopped_reason = "max_documents"
                break
            batch_size = workers if not maximum else min(workers, maximum - processed)
            # Uma chamada única mantém o circuit breaker e o limite por host
            # compartilhados por todo o lote. Paralelizar uma chamada por
            # documento permitia que vários downloads do mesmo host falhassem
            # antes que o circuito fosse aberto.
            results = [safe_one(batch_size)]
            selected = sum(max(int(row.get("selected") or 0), 0) for row in results)
            extracted = sum(max(int(row.get("extracted") or 0), 0) for row in results)
            processed += selected
            _append_log(log_path, {
                "event": "batch", "at": datetime.now(timezone.utc).isoformat(),
                "selected": selected, "extracted": extracted,
                "failed": sum(max(int(row.get("failed") or 0), 0) for row in results),
                "needs_review": sum(int(row.get("needs_review") or 0) for row in results),
                "provisional_promoted": sum(int(row.get("provisional_promoted") or 0)
                                             for row in results),
                "processed_session": processed, "free_bytes": free,
            })
            if selected == 0:
                empty_rounds += 1
                if empty_rounds >= 3:
                    retried = _retry_failed(engine, args.max_processing_attempts)
                    _append_log(log_path, {
                        "event": "retry_round", "at": datetime.now(timezone.utc).isoformat(),
                        "reopened": retried, "processed_session": processed,
                    })
                    if retried:
                        empty_rounds = 0
                        continue
                    break
            else:
                empty_rounds = 0
            if processed >= next_checkpoint:
                _append_log(log_path, {
                    "event": "checkpoint", "at": datetime.now(timezone.utc).isoformat(),
                    "processed_session": processed,
                    "result": _checkpoint(publish_remote=args.publish_every_checkpoint),
                    "queue": _queue_profile(engine),
                })
                next_checkpoint += max(int(args.checkpoint_every), 1)
            time.sleep(max(float(args.sleep_seconds), 0.0))
    finally:
        final_checkpoint = _checkpoint(publish_remote=args.publish_every_checkpoint)
        final = {
            "event": "finished", "at": datetime.now(timezone.utc).isoformat(),
            "reason": stopped_reason, "processed_session": processed,
            "free_bytes": shutil.disk_usage(_cache_root()).free,
            "queue": _queue_profile(engine), "checkpoint": final_checkpoint,
        }
        _append_log(log_path, final)
        print(json.dumps(final, ensure_ascii=False, default=str), flush=True)
        try:
            if pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
