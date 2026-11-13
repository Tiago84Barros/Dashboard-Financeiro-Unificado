"""A rota de revisão da Criação de Portfólio B3 não pode cortar os painéis.

De 10/09 a 13/11/2026 a composição para revisão (`b3_review`) terminava em
`return` logo depois dos cards: Patches 1-5, desempenho da safra vigente e as
safras ano a ano contra Selic e Pesos Iguais sumiam sem aviso. O teste de
alcance de `render_safras` continuava verde, porque a chamada seguia viva no
caminho normal. Estes testes cobrem o caminho que faltava.
"""
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
_FONTE = RAIZ / "views" / "portfolio_b3.py"


def _render() -> ast.FunctionDef:
    arvore = ast.parse(_FONTE.read_text(encoding="utf-8"))
    return next(no for no in ast.walk(arvore)
                if isinstance(no, ast.FunctionDef) and no.name == "render")


def _eh_chamada(no: ast.AST, nome: str) -> bool:
    return (isinstance(no, ast.Expr) and isinstance(no.value, ast.Call)
            and isinstance(no.value.func, ast.Name) and no.value.func.id == nome)


def _linha_do_cabecalho_lideres(render: ast.FunctionDef) -> int:
    for no in ast.walk(render):
        if (isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
                and no.func.id == "_sec_hdr" and no.args
                and isinstance(no.args[0], ast.JoinedStr)
                and "Empresas líderes" in ast.unparse(no.args[0])):
            return no.lineno
    raise AssertionError("cabeçalho 'Empresas líderes' não encontrado em render")


def test_nenhum_return_seco_depois_das_empresas_lideres():
    """Depois do cabeçalho das líderes, todo `return` de `render` tem de vir
    logo após `_rota_revisao(...)` — que desenha os painéis antes de sair.
    Um `return` solto ali apaga Patches, safras e gráfico sem quebrar nada."""
    render = _render()
    corte = _linha_do_cabecalho_lideres(render)
    def _nos_sem_funcoes_internas(raiz):
        pilha = list(ast.iter_child_nodes(raiz))
        while pilha:
            no = pilha.pop()
            if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # o `return` de um def interno não sai de `render`
            yield no
            pilha.extend(ast.iter_child_nodes(no))

    soltos = []
    for no in [render, *_nos_sem_funcoes_internas(render)]:
        for campo in ("body", "orelse", "finalbody"):
            bloco = getattr(no, campo, None)
            if not isinstance(bloco, list):
                continue
            for i, stmt in enumerate(bloco):
                if not isinstance(stmt, ast.Return) or stmt.lineno < corte:
                    continue
                if i == 0 or not _eh_chamada(bloco[i - 1], "_rota_revisao"):
                    soltos.append(stmt.lineno)
    assert not soltos, (
        "return depois das 'Empresas líderes' sem passar pelos painéis, "
        f"linhas {soltos}"
    )


def test_rota_revisao_desenha_os_paineis(monkeypatch):
    import views.portfolio_b3 as pb3

    proposta = {"items": [
        {"ticker": "EUCA4", "weight": 0.25, "quality": 94.9, "setor": "Materiais Básicos"},
        {"ticker": "SAUD3", "weight": 0.25, "quality": 76.1, "setor": "Saúde"},
    ], "allocated_weight": 0.5, "unallocated_weight": 0.5}
    rotas = types.ModuleType("core.portfolio_review_routes")
    rotas.b3_review = lambda *a, **k: proposta
    tela = types.ModuleType("design.portfolio_review")
    desenhadas = []
    tela.render_portfolio_review = lambda p, key: desenhadas.append(p)
    monkeypatch.setitem(sys.modules, "core.portfolio_review_routes", rotas)
    monkeypatch.setitem(sys.modules, "design.portfolio_review", tela)

    chamadas = []
    monkeypatch.setattr(pb3, "_render_analises_da_carteira",
                        lambda *a, **k: chamadas.append((a, k)))
    avisos = []
    monkeypatch.setattr(pb3, "aviso_lacuna", lambda msg, **k: avisos.append(k.get("codigo")))

    seg = {"setor": "Materiais Básicos", "subsetor": "Madeira", "segmento": "Papel",
           "score_proximo": {"EUCA4": 94.9, "SUZB3": 80.0},
           "val_est_oos": 2.0, "val_selic_oos": 1.5, "val_ew_oos": 1.8}
    lider = {"tk": "SAUD3", "nome": "Saúde", "score": 76.1, "peso": 1.0,
             "setor": "Saúde", "segmento": "Hospitais", "alpha_selic": 9.0}
    df_set = pd.DataFrame({"ticker": ["EUCA4"], "nome_empresa": ["Eucatex"]})

    pb3._rota_revisao(
        resultados=[seg], aprovados=[], proximos_uniq=[lider],
        df_mult_todos=pd.DataFrame(), df_set=df_set, entry_guard={},
        cap=0.25, teto_setor=1.0, teto_ciclico=1.0, taxa_selic_aa=0.15,
        vetados=set(), df_precos_all=pd.DataFrame(), selic_hist={},
    )

    assert desenhadas == [proposta]
    assert len(chamadas) == 1, "a rota de revisão não desenhou os painéis"
    args, kwargs = chamadas[0]
    itens = {it["tk"]: it for it in args[2]}
    assert set(itens) == {"EUCA4", "SAUD3"}
    assert itens["EUCA4"]["segmento"] == "Papel"
    assert itens["EUCA4"]["nome"] == "Eucatex"
    assert itens["EUCA4"]["rank_score"] == 1
    assert itens["SAUD3"]["alpha_selic"] == 9.0, "ficha da rota normal descartada"
    assert all(it["peso"] == 0.25 for it in itens.values()), (
        "o peso exibido tem de ser o da composição para revisão")
    # Sem segmento aprovado, as safras seguem o conjunto da composição e a
    # tela diz isso.
    assert kwargs["safras_base"] == [seg]
    assert "tela.portfolio_b3.revisao_safras_sem_aprovados" in avisos


def test_rota_revisao_com_aprovados_mantem_a_base_das_safras(monkeypatch):
    import views.portfolio_b3 as pb3

    rotas = types.ModuleType("core.portfolio_review_routes")
    rotas.b3_review = lambda *a, **k: {"items": []}
    tela = types.ModuleType("design.portfolio_review")
    tela.render_portfolio_review = lambda p, key: None
    monkeypatch.setitem(sys.modules, "core.portfolio_review_routes", rotas)
    monkeypatch.setitem(sys.modules, "design.portfolio_review", tela)
    chamadas = []
    monkeypatch.setattr(pb3, "_render_analises_da_carteira",
                        lambda *a, **k: chamadas.append(k))
    aprovado = {"setor": "X", "score_proximo": {}}
    pb3._rota_revisao(
        resultados=[aprovado], aprovados=[aprovado], proximos_uniq=[],
        df_mult_todos=pd.DataFrame(), df_set=pd.DataFrame(), entry_guard={},
        cap=0.2, teto_setor=1.0, teto_ciclico=1.0, taxa_selic_aa=0.15,
        vetados=set(), df_precos_all=pd.DataFrame(), selic_hist={},
    )
    assert chamadas and chamadas[0]["safras_base"] is None
