"""Scan source-like files for high-confidence embedded secrets without printing values."""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".claude", ".venv", "venv", "data", "artifacts", "local_staging", "__pycache__", ".pytest_cache", ".ruff_cache"}
ALLOWED_SUFFIXES = {".py", ".md", ".toml", ".yaml", ".yml", ".json", ".txt", ".sql", ".ini", ".cfg"}
PATTERNS = {
    "OpenAI-style key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credentialed database URL": re.compile(r"(?:postgres(?:ql)?|mysql)://[^\s:/]+:[^\s/@{}]{12,}@", re.I),
    "GitHub token": re.compile(r"\bgh[opsu]_[A-Za-z0-9]{30,}\b"),
}


def candidates() -> list[Path]:
    result: list[Path] = []
    for directory, child_dirs, filenames in os.walk(ROOT):
        child_dirs[:] = [name for name in child_dirs if name not in EXCLUDED_DIRS]
        base = Path(directory)
        for filename in filenames:
            path = base / filename
            if path.name == ".env" or path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            result.append(path)
    return result


def scan() -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    for path in candidates():
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((path.relative_to(ROOT), number, label))
    return findings


if __name__ == "__main__":
    findings = scan()
    if findings:
        print("Potential secrets found (values withheld):")
        for path, line, label in findings:
            print(f"- {path}:{line}: {label}")
        raise SystemExit(1)
    print("OK: no high-confidence embedded secrets found in scanned source files.")
