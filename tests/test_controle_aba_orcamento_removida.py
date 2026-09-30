"""A aba "Orçamento" saiu da navegação do Controle Financeiro (30/09/2026).

Dois contratos: a aba não volta por descuido de renumeração -- as seções
restantes têm de continuar apontando para o render certo -- e uma seção que
deixou de existir, guardada em session_state, não pode quebrar a página de
quem estava com a aba antiga aberta quando o deploy entrou.
"""
import ast
import pathlib

import pytest

from design import componentes

_FONTE = pathlib.Path(__file__).resolve().parents[1] / "views" / "controle_financeiro.py"


def _corpo_render() -> str:
    codigo = _FONTE.read_text(encoding="utf-8")
    arvore = ast.parse(codigo)
    for no in arvore.body:
        if isinstance(no, ast.FunctionDef) and no.name == "render":
            return ast.get_source_segment(codigo, no) or ""
    raise AssertionError("views/controle_financeiro.py não define render()")


def _secoes() -> list[str]:
    corpo = _corpo_render()
    arvore = ast.parse(corpo.strip())
    for no in ast.walk(arvore):
        if (isinstance(no, ast.Assign)
                and any(getattr(a, "id", "") == "_SECOES" for a in no.targets)):
            return [e.value for e in no.value.elts]
    raise AssertionError("render() não define _SECOES")


def test_orcamento_nao_esta_mais_na_navegacao():
    assert not [s for s in _secoes() if "Orçamento" in s]


def test_a_navegacao_mantem_as_demais_secoes_na_ordem():
    secoes = _secoes()
    assert [s.split("  ", 1)[-1] for s in secoes] == [
        "Dashboard", "Análises", "Tabelas", "Cartão de Crédito",
    ]


@pytest.mark.parametrize(("indice", "render"), [
    (1, "_tab_analises"),
    (2, "_tab_tabelas"),
    (3, "_tab_cartao"),
])
def test_cada_secao_restante_chama_o_render_certo(indice: int, render: str):
    """Tirar um item da lista renumera os ramos: o teste prende o par."""
    corpo = _corpo_render()
    trecho = corpo[corpo.index(f"_SECOES[{indice}]:"):]
    fim = trecho.find("_SECOES[", len("_SECOES[x]:"))
    assert render in (trecho if fim < 0 else trecho[:fim])


def test_render_do_orcamento_continua_no_modulo_sem_rota():
    """O cálculo segue vivo para alertas e Dashboard Geral; só a aba saiu."""
    corpo = _FONTE.read_text(encoding="utf-8")
    assert "def _tab_orcamento(" in corpo
    assert "_tab_orcamento(d," not in _corpo_render()


def test_secao_que_deixou_de_existir_nao_quebra_a_pagina(monkeypatch):
    """Sessão aberta antes do deploy guarda a aba antiga em session_state."""
    estado: dict = {f"{componentes.NAV_KEY_PREFIX}cf_secao_ativa": "🎯  Orçamento"}
    vistos: dict = {}

    def _segmented_control(_label, opcoes, *, key, default, **_kw):
        vistos["estado_no_widget"] = dict(estado)
        return estado.get(key, default)

    monkeypatch.setattr(componentes.st, "session_state", estado)
    monkeypatch.setattr(componentes.st, "segmented_control", _segmented_control)

    escolhida = componentes.abas_secao(
        ["📊  Dashboard", "📈  Análises"],
        key="cf_secao_ativa",
        default="📊  Dashboard",
    )

    assert escolhida == "📊  Dashboard"
    assert f"{componentes.NAV_KEY_PREFIX}cf_secao_ativa" not in vistos["estado_no_widget"]


def test_secao_valida_guardada_sobrevive(monkeypatch):
    """A limpeza só alcança o valor órfão -- não zera a aba escolhida."""
    chave = f"{componentes.NAV_KEY_PREFIX}cf_secao_ativa"
    estado: dict = {chave: "📈  Análises"}

    monkeypatch.setattr(componentes.st, "session_state", estado)
    monkeypatch.setattr(
        componentes.st, "segmented_control",
        lambda _l, _o, *, key, default, **_kw: estado.get(key, default),
    )

    escolhida = componentes.abas_secao(
        ["📊  Dashboard", "📈  Análises"],
        key="cf_secao_ativa",
        default="📊  Dashboard",
    )

    assert escolhida == "📈  Análises"
    assert estado[chave] == "📈  Análises"
