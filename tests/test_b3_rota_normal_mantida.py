"""Limites que não fecham mantêm a rota normal da Criação de Portfólio B3.

Decisão do usuário de 16/11/2026: com poucos ativos para o cap, ou com tetos
de setor e ciclo que não cabem juntos, a tela não troca mais a carteira pela
composição para revisão (`b3_review`), que escolhe por outra regra. Ela fica
com os líderes da rota normal, avisa quais limites ficaram de fora e usa a
mesma conta de pesos que a medição OOS (`montar_carteira`) já usava nessas
safras. A revisão continua para quando não há nenhum líder.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
_FONTE = RAIZ / "views" / "portfolio_b3.py"


def _render() -> ast.FunctionDef:
    arvore = ast.parse(_FONTE.read_text(encoding="utf-8"))
    return next(no for no in ast.walk(arvore)
                if isinstance(no, ast.FunctionDef) and no.name == "render")


def test_revisao_so_entra_sem_nenhum_lider():
    """Toda chamada de `_rota_revisao` em `render` fica sob `if not proximos_uniq`."""
    render = _render()
    chamadas, guardadas = [], []
    for no in ast.walk(render):
        if not isinstance(no, ast.If):
            continue
        cond = ast.unparse(no.test).replace(" ", "")
        for filho in ast.walk(no):
            if (isinstance(filho, ast.Call) and isinstance(filho.func, ast.Name)
                    and filho.func.id == "_rota_revisao"):
                if cond == "notproximos_uniq" and filho in [
                        n for s in no.body for n in ast.walk(s)]:
                    guardadas.append(filho.lineno)
    for no in ast.walk(render):
        if (isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
                and no.func.id == "_rota_revisao"):
            chamadas.append(no.lineno)
    assert chamadas, "a revisão sumiu de vez; ela ainda cobre a tela sem líderes"
    assert sorted(set(chamadas)) == sorted(set(guardadas)), (
        f"revisão chamada fora de `if not proximos_uniq`: {chamadas} vs {guardadas}")


def _itens_da_tela(grupos):
    """Pesos como a tela monta: orçamento igual por segmento × peso local."""
    orc = 1.0 / len(grupos)
    itens = []
    for setor, locais in grupos:
        total = sum(locais.values())
        for tk, w in locais.items():
            itens.append({"tk": tk, "setor": setor, "peso": orc * w / total})
    return itens


def test_poucos_ativos_para_o_cap_pesa_como_a_medicao_oos():
    from core.b3_oos_carteira import montar_carteira
    from views.portfolio_b3 import _normalizar_pesos_relaxados

    grupos = [("Bens Industriais", {"WEGE3": 1.0}),
              ("Financeiro", {"ITUB4": 0.6, "BBAS3": 0.4})]
    oos = montar_carteira([(s, list(p), p) for s, p in grupos],
                          cap=0.25, teto_setor=1.0, teto_ciclico=1.0)
    assert oos["inviavel"] and oos["exige_revisao"]

    itens = _itens_da_tela(grupos)
    _normalizar_pesos_relaxados(itens)
    tela = {it["tk"]: it["peso"] for it in itens}
    assert tela == pytest.approx(oos["pesos"])
    assert sum(tela.values()) == pytest.approx(1.0)


def test_normalizacao_ignora_peso_invalido_e_preserva_soma():
    from views.portfolio_b3 import _normalizar_pesos_relaxados

    itens = [{"tk": "A", "peso": 0.2}, {"tk": "B", "peso": None},
             {"tk": "C", "peso": float("nan")}, {"tk": "D", "peso": 0.6}]
    _normalizar_pesos_relaxados(itens)
    assert [it["peso"] for it in itens] == pytest.approx([0.25, 0.0, 0.0, 0.75])


def test_tela_relaxada_avisa_desliga_macro_e_salvar():
    fonte = _FONTE.read_text(encoding="utf-8")
    assert "not _rota_relaxada:\n        macro_fonte = get_macro_source()" in fonte
    assert "disabled=(not _portfolio_viavel) or _rota_relaxada" in fonte
    assert "A carteira segue a regra normal, mas não fecha nos limites" in fonte
