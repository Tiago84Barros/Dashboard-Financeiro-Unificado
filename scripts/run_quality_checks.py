"""Run safe environment checks; pass --full for the repository-wide suite."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> None:
    print(f"> {' '.join(args)}")
    subprocess.run(args, cwd=ROOT, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    options = parser.parse_args()
    run(sys.executable, "scripts/validate_skills.py")
    run(sys.executable, "scripts/check_financial_formulas.py")
    run(sys.executable, "scripts/check_secrets.py")
    run(sys.executable, "-m", "unittest", "tests.test_codex_environment", "-v")
    if options.full:
        run(sys.executable, "-m", "pytest", "-q")
        try:
            run(sys.executable, "-m", "ruff", "check", ".")
        except subprocess.CalledProcessError:
            raise
