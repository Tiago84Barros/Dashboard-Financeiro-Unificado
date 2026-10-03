"""Busca de empresa por ticker ou nome (aba Análise de Empresas B3)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.busca_empresa import buscar_empresas, normalizar

RAIZ = Path(__file__).resolve().parents[1]

_SET = pd.DataFrame({
    "ticker": ["BBAS3", "PETR3", "PETR4", "WEGE3", "ITUB4", "BBDC4", "VALE3"],
    "nome_empresa": [
        "Banco do Brasil S.A.", "Petróleo Brasileiro S.A. Petrobras",
        "Petróleo Brasileiro S.A. Petrobras", "WEG S.A.", "Itaú Unibanco Holding",
        "Banco Bradesco", "Vale S.A.",
    ],
})


def _tks(consulta, universo=()):
    return [tk for tk, _ in buscar_empresas(consulta, _SET, universo)]


def test_ticker_exato_vence_em_qualquer_caixa_e_com_sufixo():
    assert _tks("bbas3") == ["BBAS3"]
    assert _tks("PETR4.SA") == ["PETR4"]


def test_nome_sem_acento_e_sem_caixa():
    assert _tks("banco do brasil") == ["BBAS3"]
    assert _tks("itau") == ["ITUB4"]
    assert _tks("petroleo") == ["PETR3", "PETR4"]


def test_nome_ambiguo_devolve_todos_os_candidatos():
    assert set(_tks("banco")) == {"BBAS3", "BBDC4"}


def test_prefixo_de_ticker():
    assert _tks("PETR") == ["PETR3", "PETR4"]


def test_palavra_casa_pelo_inicio_nao_pelo_meio():
    # "ale" está dentro de "Vale", mas não começa palavra nenhuma.
    assert _tks("ale") == []


def test_erro_de_digitacao_no_nome():
    assert _tks("banco do brazil") == ["BBAS3"]


def test_ticker_sem_nome_cadastrado_vem_do_universo():
    assert buscar_empresas("enat3", _SET, ["ENAT3"]) == [("ENAT3", "ENAT3")]


def test_nada_casa_e_banco_vazio_nao_quebra():
    assert _tks("xyzw9") == []
    assert buscar_empresas("banco", pd.DataFrame(), []) == []
    assert buscar_empresas("   ", _SET) == []


def test_normalizar():
    assert normalizar("  Itaú-Unibanco  S.A. ") == "ITAU UNIBANCO S A"


def test_tela_usa_a_busca_por_nome():
    fonte = (RAIZ / "views" / "empresas_b3.py").read_text(encoding="utf-8")
    corpo = fonte.split("def _tab_analise(")[1].split("# ── Header ──")[0]
    assert "buscar_empresas(" in corpo
