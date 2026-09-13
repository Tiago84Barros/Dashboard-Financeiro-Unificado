"""A carteira que não se forma não pode levar a aba junto.

As três saídas antecipadas de ``_carteira_integrada`` trocavam a tela inteira
pela composição de revisão: sumiam a correlação entre os candidatos e a caixa de
chat -- justamente o que responde "por que nada passou?". Nenhuma das duas
depende de a carteira ter se formado, e é quando ela não se forma que elas mais
servem. Estes testes existem para que um próximo ``return None`` não as leve de
novo.
"""
from __future__ import annotations

import ast
import pathlib

import pandas as pd

from views.fiis import _diagnostico_da_vitrine, _recorte_de_correlacao

_FONTE = pathlib.Path(__file__).resolve().parents[1] / "views" / "fiis.py"


def _funcao(nome: str) -> ast.FunctionDef:
    arvore = ast.parse(_FONTE.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.FunctionDef) and no.name == nome:
            return no
    raise AssertionError(f"{nome} não existe mais em views/fiis.py")


def _chamadas(nos) -> set[str]:
    nomes = set()
    for no in nos:
        for filho in ast.walk(no):
            if isinstance(filho, ast.Call) and isinstance(filho.func, ast.Name):
                nomes.add(filho.func.id)
    return nomes


def test_todo_ramo_que_mostra_revisao_mantem_o_chat():
    funcao = _funcao("_carteira_integrada")
    ramos = [no for no in ast.walk(funcao)
             if isinstance(no, ast.If) and "render_portfolio_review" in _chamadas(no.body)]
    assert ramos, "nenhum ramo de revisão encontrado: o teste perdeu o alvo"
    for ramo in ramos:
        assert "_render_fii_chat" in _chamadas(ramo.body), (
            f"o ramo da linha {ramo.lineno} volta a esconder a caixa de chat "
            "quando a carteira não se forma")


def test_ramo_com_candidatos_mostra_a_correlacao_deles():
    """Sem elegíveis não há matriz; com candidatos avaliados, há -- e ela aparece."""
    funcao = _funcao("_carteira_integrada")
    ramos = [no for no in ast.walk(funcao)
             if isinstance(no, ast.If) and "fii_review" in _chamadas(no.body)
             and "_diagnostico_de_factibilidade" in _chamadas(no.body)]
    assert len(ramos) == 1
    assert "_render_matriz_correlacao" in _chamadas(ramos[0].body)


def test_diagnostico_da_vitrine_separa_dado_velho_de_filtro_apertado():
    inputs = pd.DataFrame([{"ticker": "AAAA11"}])
    inputs.attrs.update(snapshot_stale_warning=True, snapshot_as_of="20/08/2026",
                        snapshot_age_days=24, snapshot_max_age_days=7)
    linhas = _diagnostico_da_vitrine(
        inputs, {"universe_count": 5, "exclusion_counts": {"dy ausente": 5}})
    assert any("Republicar a vitrine" in linha for linha in linhas)
    assert any("falta dado, não folga" in linha for linha in linhas)
    # Vitrine no prazo e exclusão por mérito não inventam diagnóstico nenhum.
    assert _diagnostico_da_vitrine(
        pd.DataFrame(), {"universe_count": 5,
                         "exclusion_counts": {"p/vp acima do limite": 2}}) == []


def test_recorte_de_correlacao_limita_o_heatmap_pela_nota():
    tickers = [f"AA{indice:02d}11" for indice in range(20)]
    corr = pd.DataFrame(1.0, index=tickers, columns=tickers)
    # Nota decrescente na ordem dos tickers: os cinco primeiros é que sobrevivem.
    scored = [{"ticker": ticker, "type_score": 100 - nota}
              for nota, ticker in enumerate(tickers)]
    recorte = _recorte_de_correlacao(corr, scored, limite=5)
    assert list(recorte.columns) == tickers[:5]
    assert _recorte_de_correlacao(pd.DataFrame(), scored).empty
    # Um único candidato não faz matriz: melhor vazio do que uma célula sozinha.
    assert _recorte_de_correlacao(corr, scored[:1]).empty
