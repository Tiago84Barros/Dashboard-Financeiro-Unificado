"""Texto de exceção não chega à tela nas análises de carteira B3/EUA (LLM-A11).

O #511/#512 levaram as falhas da LLM para `core.llm_falha`; sobravam dois
pontos que interpolavam `{exc}` na tela -- o gráfico do chat da B3 e a carteira
salva dos EUA (erro de banco, que pode carregar SQL e host). A varredura cobre
os dois arquivos inteiros para o próximo `st.warning(f"... {exc}")` não voltar.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_ARQUIVOS = ("views/analise_portfolio_b3.py", "views/analise_portfolio_us.py")
_SAIDAS = {"error", "warning", "caption", "info", "write", "markdown", "toast"}


def _nomes_de_excecao(arvore: ast.AST) -> set[str]:
    return {h.name for h in ast.walk(arvore)
            if isinstance(h, ast.ExceptHandler) and h.name}


def _interpola(no: ast.AST, nomes: set[str]) -> bool:
    for sub in ast.walk(no):
        if isinstance(sub, ast.FormattedValue):
            for n in ast.walk(sub.value):
                if isinstance(n, ast.Name) and n.id in nomes:
                    return True
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                and sub.func.id == "str" and sub.args
                and isinstance(sub.args[0], ast.Name) and sub.args[0].id in nomes):
            return True
    return False


@pytest.mark.parametrize("rel", _ARQUIVOS)
def test_tela_nao_interpola_a_excecao(rel):
    arvore = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    nomes = _nomes_de_excecao(arvore)
    achados = []
    for no in ast.walk(arvore):
        if not (isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)
                and no.func.attr in _SAIDAS
                and isinstance(no.func.value, ast.Name) and no.func.value.id == "st"):
            continue
        if any(_interpola(a, nomes) for a in [*no.args, *(k.value for k in no.keywords)]):
            achados.append(no.lineno)
    assert not achados, f"{rel} mostra o texto da exceção na tela nas linhas {achados}"


def test_varredura_pega_o_padrao_antigo():
    fonte = (
        "import streamlit as st\n"
        "try:\n    pass\nexcept Exception as exc:\n"
        "    st.error(f'Não foi possível carregar a carteira salva: {exc}')\n"
    )
    arvore = ast.parse(fonte)
    nomes = _nomes_de_excecao(arvore)
    chamada = next(n for n in ast.walk(arvore) if isinstance(n, ast.Call))
    assert _interpola(chamada.args[0], nomes)
