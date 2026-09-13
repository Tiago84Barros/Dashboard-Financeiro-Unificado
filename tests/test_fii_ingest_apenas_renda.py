"""R3: a serie mensal de renda do FII era filtrada por um ``LIKE`` solto.

``_derive_income_observations`` excluia ``upper(type) NOT LIKE '%AMORT%'``.
Devolucao de capital nao e renda recorrente, e ``REST CAP DIN`` (restituicao
de capital em dinheiro) passa inteira por esse filtro -- sao 73 linhas em
``market.dividends``.

Medido em 13/09/2026 no armazem local: hoje as 73 linhas estao todas em
tickers que NAO estao em ``market.fiis``, entao o impacto corrente e zero. O
defeito e latente, nao inexistente: a filiacao a ``market.fiis`` muda a cada
ingestao, e no dia em que um desses tickers entrar o capital devolvido vira
"renda" sem que nada quebre.

O que este teste trava nao e a lista de tipos -- e a UNICIDADE da regra. Ja
existe um dono para ela, ``core.dividend_types``, criado no A-128 por este
mesmo motivo. Uma segunda copia num ``LIKE`` e a forma que o defeito tem de
voltar: guarda duplicada nao fica igual.
"""
from __future__ import annotations

import ast
import pathlib

from core.dividend_types import TIPOS_DEVOLUCAO_CAPITAL, eh_renda, sql_apenas_renda

_FONTE = pathlib.Path(__file__).resolve().parents[1] / "data_pipeline/market/fii_ingest.py"


def _funcao_de_derivacao() -> ast.FunctionDef:
    arvore = ast.parse(_FONTE.read_text(encoding="utf-8"))
    return next(
        no for no in ast.walk(arvore)
        if isinstance(no, ast.FunctionDef) and no.name == "_derive_income_observations")


def _texto_literal(funcao: ast.FunctionDef) -> str:
    """So os pedacos ESCRITOS A MAO da consulta -- o que foi interpolado sai.

    Lido pela AST e nao por ``grep``: um predicado montado por interpolacao
    nao existe no fonte como texto, e um escrito a mao nao existe como
    chamada. Sao duas perguntas diferentes e o teste faz as duas.
    """
    return "\n".join(
        no.value for no in ast.walk(funcao)
        if isinstance(no, ast.Constant) and isinstance(no.value, str))


def _chamadas_interpoladas(funcao: ast.FunctionDef) -> set[str]:
    """Nomes das funcoes chamadas dentro de ``{...}`` numa f-string."""
    nomes: set[str] = set()
    for no in ast.walk(funcao):
        if not isinstance(no, ast.FormattedValue):
            continue
        for interno in ast.walk(no):
            if isinstance(interno, ast.Call) and isinstance(interno.func, ast.Name):
                nomes.add(interno.func.id)
    return nomes


def test_devolucao_de_capital_nao_entra_na_serie_de_renda():
    """``REST CAP DIN`` e devolucao de capital e tem de sair junto com a
    amortizacao. O ``LIKE '%AMORT%'`` a deixava passar inteira."""
    predicado = sql_apenas_renda("type")
    for tipo in TIPOS_DEVOLUCAO_CAPITAL:
        assert tipo in predicado, f"{tipo!r} nao e excluido da serie mensal de renda"
    assert "sql_apenas_renda" in _chamadas_interpoladas(_funcao_de_derivacao()), (
        "a consulta nao aplica o predicado de renda da definicao unica")


def test_a_regra_nao_e_reescrita_a_mao_na_consulta():
    """Unicidade: a consulta tem de USAR ``core.dividend_types``, nao repetir
    a lista. Um ``LIKE '%AMORT%'`` solto e como a divergencia volta."""
    literal = _texto_literal(_funcao_de_derivacao())
    assert "AMORT" not in literal.upper(), (
        "predicado escrito a mao na consulta; use core.dividend_types.sql_apenas_renda")
    assert "REST CAP" not in literal.upper(), (
        "lista de tipos copiada na consulta; ela tem um dono unico")


def test_definicao_unica_concorda_com_o_predicado_sql():
    """O predicado SQL e o predicado Python respondem a mesma pergunta. Se um
    dia divergirem, a serie mensal e a leitura do app discordam em silencio."""
    for tipo in TIPOS_DEVOLUCAO_CAPITAL:
        assert not eh_renda(tipo)
        assert f"'{tipo}'" in sql_apenas_renda()
    for tipo in ("RENDIMENTO", "DIVIDENDO", "JCP"):
        assert eh_renda(tipo)
        assert f"'{tipo}'" not in sql_apenas_renda()
