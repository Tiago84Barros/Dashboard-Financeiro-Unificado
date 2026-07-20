"""Validate project-local Codex Skills without third-party dependencies."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / ".agents" / "skills"
EXPECTED = {
    "personal-financial-analyst",
    "financial-calculations",
    "expense-intelligence",
    "investment-portfolio-analysis",
    "financial-data-security",
    "streamlit-financial-app",
    "financial-app-quality",
    "streamlit-browser-validation",
}


def parse_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"')
    return fields


def validate() -> list[str]:
    errors: list[str] = []
    found = {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()} if SKILLS_ROOT.exists() else set()
    if found != EXPECTED:
        errors.append(f"skill set mismatch: missing={sorted(EXPECTED-found)}, extra={sorted(found-EXPECTED)}")
    for name in sorted(EXPECTED & found):
        folder = SKILLS_ROOT / name
        skill_file = folder / "SKILL.md"
        if not skill_file.is_file():
            errors.append(f"{name}: missing SKILL.md")
            continue
        text = skill_file.read_text(encoding="utf-8")
        meta = parse_frontmatter(text)
        if meta.get("name") != name:
            errors.append(f"{name}: frontmatter name does not match folder")
        if len(meta.get("description", "")) < 40:
            errors.append(f"{name}: description is missing or too short")
        if "TODO" in text:
            errors.append(f"{name}: unresolved TODO")
        if not (folder / "agents" / "openai.yaml").is_file():
            errors.append(f"{name}: missing agents/openai.yaml")
        for target in re.findall(r"\]\((references/[^)]+)\)", text):
            if not (folder / target).is_file():
                errors.append(f"{name}: missing referenced file {target}")
    return errors


if __name__ == "__main__":
    failures = validate()
    if failures:
        print("Skill validation failed:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print(f"OK: {len(EXPECTED)} project Skills are valid and discoverable.")
