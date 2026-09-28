"""Guarda contra "resolver a lacuna apagando o aviso".

Compara o diff do branch com a base e falha se ele REMOVE uma chamada de
``registrar_lacuna`` / ``registrar_limitacoes`` / ``aviso_lacuna`` ou um texto
de limitacao (``limitacoes=`` / ``limitacoes.append``). Linha movida (removida
aqui, acrescentada ali com o mesmo texto) nao conta.

Escape: o corpo do PR (``PR_BODY``, que o workflow de testes exporta) com uma
secao ``## Aviso removido`` seguida de justificativa.

Limite conhecido: a analise e por linha. Um texto de limitacao que continua
em outra linha, sem ``limitacoes`` nela, nao e visto.

No CI a base precisa existir -- guarda que pula em silencio nunca dispara.
    memoria: gate-que-so-dava-false
"""
from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]

_CHAMADA = re.compile(r"(?<!def )\b(registrar_lacuna|registrar_limitacoes|aviso_lacuna)\(")
_LIMITACAO = re.compile(r"\blimitacoes\b\s*(=|\+=|\.append\(|\.extend\().*[\"']")
_JUSTIFICATIVA = re.compile(r"^##\s*Aviso removido\s*$\n+(?P<texto>(?!##).+)", re.MULTILINE)


def _vigiada(linha: str) -> bool:
    return bool(_CHAMADA.search(linha) or _LIMITACAO.search(linha))


def remocoes_vigiadas(diff: str) -> list[tuple[str, str]]:
    """(arquivo, linha) removidas e nao reacrescentadas em lugar nenhum do diff.
    Arquivos de teste ficam de fora: teste pode trocar a chamada por dublê."""
    removidas: list[tuple[str, str]] = []
    acrescentadas: Counter = Counter()
    arquivo = ""
    for linha in diff.splitlines():
        if linha.startswith("+++ "):
            arquivo = linha[6:] if linha.startswith("+++ b/") else ""
            continue
        if linha.startswith("--- ") or arquivo.startswith("tests/") or not arquivo.endswith(".py"):
            continue
        if linha.startswith("-") and _vigiada(linha[1:]):
            removidas.append((arquivo, linha[1:].strip()))
        elif linha.startswith("+"):
            acrescentadas[linha[1:].strip()] += 1
    saida = []
    for arq, texto in removidas:
        if acrescentadas[texto] > 0:
            acrescentadas[texto] -= 1
        else:
            saida.append((arq, texto))
    return saida


def justificado(corpo_pr: str | None) -> bool:
    return bool(_JUSTIFICATIVA.search(corpo_pr or ""))


# ── o parser ────────────────────────────────────────────────────────────────

_DIFF = """\
diff --git a/views/fiis.py b/views/fiis.py
--- a/views/fiis.py
+++ b/views/fiis.py
@@ -1,3 +1,2 @@
-    aviso_lacuna("Sem dados.", codigo="tela.fii.sem_dados")
     x = 1
-    limitacoes.append("VPA ausente no periodo")
"""


def test_remocao_de_aviso_e_limitacao_e_detectada():
    assert remocoes_vigiadas(_DIFF) == [
        ("views/fiis.py", 'aviso_lacuna("Sem dados.", codigo="tela.fii.sem_dados")'),
        ("views/fiis.py", 'limitacoes.append("VPA ausente no periodo")'),
    ]


def test_linha_movida_nao_conta():
    movida = _DIFF + """\
diff --git a/views/outra.py b/views/outra.py
--- a/views/outra.py
+++ b/views/outra.py
@@ -1 +1,2 @@
+        aviso_lacuna("Sem dados.", codigo="tela.fii.sem_dados")
+        limitacoes.append("VPA ausente no periodo")
"""
    assert remocoes_vigiadas(movida) == []


def test_definicao_e_testes_nao_contam():
    diff = """\
--- a/core/lacunas/registro.py
+++ b/core/lacunas/registro.py
-def registrar_lacuna(fonte, codigo, mensagem):
--- a/tests/test_x.py
+++ b/tests/test_x.py
-    registrar_lacuna("motor", "c", "m")
"""
    assert remocoes_vigiadas(diff) == []


def test_justificativa_exige_texto_sob_o_titulo():
    assert justificado("Resumo\n\n## Aviso removido\nO dado agora sempre existe: ver teste X.")
    assert not justificado("## Aviso removido\n\n## Testes\n")
    assert not justificado(None)


# ── a guarda de verdade ─────────────────────────────────────────────────────

def _diff_contra_base() -> str | None:
    base = os.environ.get("LACUNAS_BASE", "origin/main")
    try:
        subprocess.run(["git", "rev-parse", "--verify", base], cwd=RAIZ, check=True,
                       capture_output=True, timeout=30)
        return subprocess.run(["git", "diff", f"{base}...HEAD", "--", "*.py"], cwd=RAIZ,
                              check=True, capture_output=True, text=True,
                              encoding="utf-8", timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def test_branch_nao_remove_aviso_de_lacuna_sem_justificativa():
    diff = _diff_contra_base()
    if diff is None:
        if os.environ.get("CI"):
            pytest.fail("guarda de lacunas sem base para comparar: o checkout do CI "
                        "precisa de fetch-depth: 0")
        pytest.skip("sem origin/main local")
    removidas = remocoes_vigiadas(diff)
    if removidas and not justificado(os.environ.get("PR_BODY")):
        linhas = "\n".join(f"  {a}: {t}" for a, t in removidas)
        pytest.fail("O branch remove avisos de lacuna:\n" + linhas + "\n"
                    "Uma lacuna se resolve mudando a condicao, nao apagando o aviso. "
                    "Se a remocao for correta, explique numa secao '## Aviso removido' "
                    "no corpo do PR.")
