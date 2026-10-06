"""Investimentos executa só a aba aberta, e a seleção sobrevive ao 🔒 → 🧠.

Com ``st.tabs`` sem estado o Streamlit roda as seis abas em todo rerun: cada
clique na Inteligência dos Ativos refazia Dashboard, Histórico, Carteira,
Análise e Imposto de Renda antes de responder (05/10/2026).
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import views.investimentos as inv
from views import inteligencia_ativos as ia


def _render() -> ast.FunctionDef:
    arvore = ast.parse(textwrap.dedent(inspect.getsource(inv.render)))
    return next(n for n in arvore.body if isinstance(n, ast.FunctionDef))


def test_abas_tem_estado_e_reexecutam_ao_trocar():
    chamada = next(
        n for n in ast.walk(_render())
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "st.tabs"
    )
    kw = {k.arg: ast.unparse(k.value) for k in chamada.keywords}
    assert kw.get("on_change") == "'rerun'"
    assert kw.get("key") == "ABA_INVESTIMENTOS_KEY"


def test_cada_aba_so_roda_quando_aberta():
    render = _render()
    abas = [f"tab{i}" for i in range(1, 6)]
    guardadas = set()
    for no in ast.walk(render):
        if isinstance(no, ast.If) and ast.unparse(no.test) in {
                f"{a}.open" for a in abas}:
            aba = ast.unparse(no.test).removesuffix(".open")
            assert [ast.unparse(w.items[0].context_expr)
                    for w in no.body if isinstance(w, ast.With)] == [aba]
            guardadas.add(aba)
    assert guardadas == set(abas)
    # Nenhum ``with tabN:`` fora do seu ``if``: ele rodaria em todo rerun.
    soltos = [
        ast.unparse(no.items[0].context_expr)
        for no in render.body
        if isinstance(no, ast.With)
        and ast.unparse(no.items[0].context_expr) in abas
    ]
    assert soltos == []


def _rotulo(disponivel: bool) -> str:
    return ia.rotulo_aba(type("L", (), {"disponivel": disponivel})())


def test_aba_da_ia_acompanha_a_troca_de_icone():
    travada, liberada = _rotulo(False), _rotulo(True)
    assert travada != liberada
    assert inv._aba_mantida(travada, liberada) == liberada
    assert inv._aba_mantida(liberada, travada) == travada
    assert inv._aba_mantida(liberada, liberada) == liberada


def test_outras_abas_sao_reafirmadas_pela_chave():
    # O id das abas muda com o rótulo da IA; sem reafirmar, quem estava no
    # Imposto de Renda voltaria ao Dashboard quando a Estratégia libera.
    assert inv._aba_mantida("🧾  Imposto de Renda", _rotulo(True)) == \
        "🧾  Imposto de Renda"


def test_quem_estava_na_carteira_cai_na_analise():
    # A Carteira foi absorvida pela Análise → Visão Geral (06/10/2026).
    assert inv._aba_mantida("💼  Carteira", _rotulo(True)) == "🔍  Análise"
    assert "💼  Carteira" not in inspect.getsource(inv.render)


def test_sem_selecao_nao_escreve_nada():
    assert inv._aba_mantida(None, _rotulo(True)) is None
    assert inv._aba_mantida("", _rotulo(True)) is None
