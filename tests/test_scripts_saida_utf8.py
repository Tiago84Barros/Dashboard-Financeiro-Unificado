# -*- coding: utf-8 -*-
"""Os scripts da cadeia FII escrevem UTF-8 mesmo com a saída num pipe cp1252.

Em 19/11/2026 o walk-forward FII rodado à mão no Windows (``| Tee-Object``)
quebrou no ``print`` final: o cp1252 não tem o ``−`` (U+2212) dos bloqueios.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MENOS = "−"

SCRIPTS_DA_CADEIA_FII = (
    "scripts/run_fii_optimizer_pit_local.py",
    "scripts/publish_fii_selection_from_local.py",
    "scripts/publish_fii_selection_snapshot.py",
)


def _rodar(codigo: str) -> subprocess.CompletedProcess:
    ambiente = dict(os.environ)
    ambiente.pop("PYTHONUTF8", None)
    ambiente["PYTHONIOENCODING"] = "cp1252"  # o que o Windows usa num pipe
    ambiente["PYTHONPATH"] = str(ROOT)
    return subprocess.run([sys.executable, "-c", codigo], capture_output=True,
                          env=ambiente, cwd=ROOT, timeout=60)


def test_sem_o_helper_o_menos_quebra_em_cp1252():
    """Controle: reproduz o defeito, senão o teste seguinte não prova nada."""
    r = _rodar(f"print({MENOS!r})")
    assert r.returncode != 0
    assert b"UnicodeEncodeError" in r.stderr


def test_com_o_helper_o_menos_sai_em_utf8():
    r = _rodar("from scripts._saida_utf8 import saida_utf8\n"
               "saida_utf8()\n"
               f"print('excesso médio de {MENOS}0,123 p.p./mês')")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert r.stdout.decode("utf-8").strip() == f"excesso médio de {MENOS}0,123 p.p./mês"


def test_helper_tolera_fluxo_sem_reconfigure(monkeypatch):
    import io

    from scripts._saida_utf8 import saida_utf8

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    saida_utf8()
    print(MENOS)
    assert sys.stdout.getvalue() == MENOS + "\n"


@pytest.mark.parametrize("caminho", SCRIPTS_DA_CADEIA_FII)
def test_main_dos_scripts_chama_o_helper_antes_de_tudo(caminho):
    arvore = ast.parse((ROOT / caminho).read_text(encoding="utf-8"))
    main = next(n for n in arvore.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    chamadas = [n for n in main.body if isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Call)
                and getattr(n.value.func, "id", None) == "saida_utf8"]
    assert chamadas, f"{caminho}: main() não chama saida_utf8()"
    # Antes do parse_args e de qualquer print: a primeira instrução que não é import.
    primeira = next(n for n in main.body
                    if not isinstance(n, (ast.Import, ast.ImportFrom)))
    assert primeira is chamadas[0]
