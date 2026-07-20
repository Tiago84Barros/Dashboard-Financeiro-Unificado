"""Backfill resiliente SEC EDGAR para o warehouse americano.

Executa lotes pequenos até que todas as companhias elegíveis tenham balanços
trimestrais na versão corrente do parser. Preserva pelo menos 5,5 GB livres,
tolera timeouts por lote e só recalcula os artefatos analíticos após concluir a
ingestão. O estado fica em ``data/us_refresh/status.json``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "data" / "us_refresh"
STATUS = STATE_DIR / "status.json"
LOG = STATE_DIR / "refresh.jsonl"
LOCK = STATE_DIR / "refresh.lock"
MIN_FREE_GB = 5.5
BATCH_SIZE = 50
WORKERS = 4


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(**payload) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = {"updated_at": utcnow(), **payload}
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATUS)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False) + "\n")


def run(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, str(ROOT / "run_us_ingest.py"), *args],
        cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=timeout, check=False,
    )


def parse_result(proc: subprocess.CompletedProcess[str]) -> dict:
    for line in reversed((proc.stdout or "").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {"returncode": proc.returncode, "stdout_tail": (proc.stdout or "")[-500:],
            "stderr_tail": (proc.stderr or "")[-500:]}


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return 2
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)

    batch = 0
    consecutive_no_progress = 0
    try:
        write_status(state="running", pid=os.getpid(), phase="ingestion", batch=batch)
        while True:
            free_gb = shutil.disk_usage(ROOT.anchor).free / (1024 ** 3)
            if free_gb <= MIN_FREE_GB:
                write_status(state="paused", phase="disk_guard", batch=batch,
                             free_gb=round(free_gb, 2), minimum_free_gb=MIN_FREE_GB)
                return 3
            batch += 1
            try:
                proc = run([
                    "bootstrap", "--warehouse", "--limit", str(BATCH_SIZE),
                    "--years", "20", "--budget", "180", "--workers", str(WORKERS),
                    "--no-prices", "--json",
                ], timeout=900)
                result = parse_result(proc)
            except subprocess.TimeoutExpired:
                write_status(state="running", phase="ingestion_timeout", batch=batch,
                             free_gb=round(free_gb, 2), retry_in_seconds=60)
                time.sleep(60)
                continue

            processed = int(result.get("processed") or 0)
            errors = int(result.get("errors") or 0)
            write_status(state="running", phase="ingestion", batch=batch,
                         free_gb=round(free_gb, 2), result=result)
            if processed == 0:
                break
            consecutive_no_progress = consecutive_no_progress + 1 if errors >= processed else 0
            if consecutive_no_progress >= 3:
                write_status(state="paused", phase="source_errors", batch=batch,
                             result=result, reason="three batches without progress")
                return 4
            time.sleep(5)

        post_steps = [
            (["enrich", "--warehouse", "--json"], 1800),
            (["validate", "--warehouse", "--json"], 600),
            (["snapshot", "--warehouse", "--json"], 1800),
            (["score-history", "--warehouse", "--start-year", "2006",
              "--end-year", "2025", "--json"], 2400),
            (["backtest", "--warehouse", "--top-n", "20",
              "--transaction-cost-bps", "10", "--slippage-bps", "5",
              "--bootstrap-samples", "5000", "--json"], 1200),
        ]
        for args, timeout in post_steps:
            phase = args[0]
            write_status(state="running", phase=phase, batch=batch)
            proc = run(args, timeout=timeout)
            result = parse_result(proc)
            if proc.returncode != 0:
                write_status(state="paused", phase=phase, batch=batch, result=result)
                return proc.returncode or 5
            write_status(state="running", phase=phase, batch=batch, result=result)

        write_status(state="completed", phase="done", batch=batch)
        return 0
    finally:
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
