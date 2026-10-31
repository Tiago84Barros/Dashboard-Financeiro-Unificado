from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class CodexEnvironmentTests(unittest.TestCase):
    def test_project_skills_are_valid(self) -> None:
        self.assertEqual(load_script("validate_skills").validate(), [])

    def test_synthetic_fixture_is_explicit_and_complete(self) -> None:
        payload = json.loads((ROOT / "tests" / "fixtures" / "synthetic_financial_data.json").read_text(encoding="utf-8"))
        self.assertIs(payload["metadata"]["synthetic"], True)
        self.assertGreaterEqual(len(payload["transactions"]), 10)
        self.assertTrue(any(item.get("duplicate_candidate") for item in payload["transactions"]))
        self.assertTrue(any(item.get("anomaly_candidate") for item in payload["transactions"]))
        self.assertIsNone(payload["missing_data_case"]["amount"])

    def test_formula_examples(self) -> None:
        formulas = load_script("check_financial_formulas")
        self.assertAlmostEqual(formulas.savings_rate(5000, 3000), 0.4)
        self.assertEqual(formulas.net_worth([10000, 5000], [4000]), 11000)
        self.assertEqual(formulas.reserve_months(9000, 3000), 3.0)
        with self.assertRaises(ValueError):
            formulas.savings_rate(0, 1)

    def test_no_embedded_secrets(self) -> None:
        # O CI do GitHub so roda pytest: sem isto, o gate de segredos do
        # `run_quality_checks.py` ficou vermelho na main sem ninguem ver.
        findings = load_script("check_secrets").scan()
        self.assertEqual([f"{path}:{line}: {label}" for path, line, label in findings], [])

    def test_secret_scanner_still_flags_credentialed_url(self) -> None:
        # montada em tempo de execucao, para o proprio teste nao virar achado
        url = "postgresql://u:" + "x" * 12 + "@db.example.test:5432/postgres"
        pattern = load_script("check_secrets").PATTERNS["credentialed database URL"]
        self.assertIsNotNone(pattern.search(url))
        self.assertIsNone(pattern.search("postgresql://u:{senha}@db.example.test:5432/postgres"))


if __name__ == "__main__":
    unittest.main()
