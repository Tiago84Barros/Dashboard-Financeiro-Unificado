"""Executor headless da macro internacional, isolado no PostgreSQL Docker local."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> int:
    from data_pipeline.jobs.update_macro_international import run

    result = run()
    logging.getLogger(__name__).info(
        "macro status=%s inserted=%s failed=%s",
        result["status"],
        result["records_inserted"],
        result["records_failed"],
    )
    print(json.dumps(result, default=str))
    return 0 if result["status"] in {"success", "partial_success", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
