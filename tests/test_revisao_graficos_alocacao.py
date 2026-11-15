"""A composição para revisão mostra os mesmos gráficos de alocação da normal.

Até 15/11/2026 a pizza setorial da B3 e os gráficos "Alocação por ativo" e
"Alocação por setor" dos EUA ficavam só no corpo da carteira normal; a rota de
revisão das duas telas terminava sem eles. Agora cada tela tem uma função
única, chamada pelos dois caminhos, e estes testes travam isso.
"""
from __future__ import annotations

import ast
import contextlib
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]


def _funcoes(caminho: str) -> dict[str, ast.FunctionDef]:
    arvore = ast.parse((RAIZ / caminho).read_text(encoding="utf-8"))
    return {no.name: no for no in ast.walk(arvore) if isinstance(no, ast.FunctionDef)}


def _chama(funcao: ast.FunctionDef, nome: str) -> list[int]:
    return [no.lineno for no in ast.walk(funcao)
            if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
            and no.func.id == nome]


def _donas_da_chave(caminho: str, chave: str) -> set[str]:
    donas = set()
    for nome, funcao in _funcoes(caminho).items():
        for no in ast.walk(funcao):
            if isinstance(no, ast.Constant) and no.value == chave:
                donas.add(nome)
    return donas


class _StFalso:
    def __init__(self):
        self.graficos = []

    def plotly_chart(self, fig, **kwargs):
        self.graficos.append((kwargs.get("key"), fig))

    def columns(self, n):
        return [contextlib.nullcontext() for _ in range(n)]

    def markdown(self, *a, **k):
        pass


def _fatias(fig) -> dict[str, float]:
    trace = fig.data[0]
    return dict(zip(trace.labels, trace.values))


# ── B3 ────────────────────────────────────────────────────────────────────────

def test_b3_pizza_mora_numa_funcao_chamada_pelos_paineis_compartilhados():
    funcoes = _funcoes("views/portfolio_b3.py")
    assert _chama(funcoes["_render_analises_da_carteira"], "_render_distribuicao_setorial"), (
        "a pizza tem de sair de _render_analises_da_carteira, que as duas rotas chamam")
    assert _chama(funcoes["_rota_revisao"], "_render_analises_da_carteira")
    assert _chama(funcoes["render"], "_render_analises_da_carteira")
    assert _donas_da_chave("views/portfolio_b3.py", "pb3_pie") == {
        "_render_distribuicao_setorial"}, "cópia da pizza fora da função única"


def test_b3_pizza_soma_setores_e_mostra_capital_nao_alocado(monkeypatch):
    import views.portfolio_b3 as pb3

    st = _StFalso()
    monkeypatch.setattr(pb3, "st", st)
    monkeypatch.setattr(pb3, "_sec_hdr", lambda *a, **k: None)
    pb3._render_distribuicao_setorial([
        {"tk": "WEGE3", "setor": "Bens Industriais", "peso": 0.25},
        {"tk": "RAPT4", "setor": "Bens Industriais", "peso": 0.15},
        {"tk": "SBSP3", "setor": None, "peso": 0.4},
    ])
    [(chave, fig)] = st.graficos
    assert chave == "pb3_pie"
    fatias = _fatias(fig)
    assert fatias.keys() == {"Bens Industriais", "Não classificado", "Não alocado"}
    assert abs(fatias["Bens Industriais"] - 0.40) < 1e-9
    assert abs(fatias["Não alocado"] - 0.20) < 1e-9


def test_b3_pizza_cheia_nao_inventa_sobra(monkeypatch):
    import views.portfolio_b3 as pb3

    st = _StFalso()
    monkeypatch.setattr(pb3, "st", st)
    monkeypatch.setattr(pb3, "_sec_hdr", lambda *a, **k: None)
    pb3._render_distribuicao_setorial([
        {"tk": tk, "setor": setor, "peso": 0.25}
        for tk, setor in (("WEGE3", "Bens Industriais"), ("CXSE3", "Financeiro"),
                          ("BRAP3", "Materiais Básicos"), ("SBSP3", "Utilidade Pública"))
    ])
    [(_, fig)] = st.graficos
    assert "Não alocado" not in _fatias(fig)
    pb3._render_distribuicao_setorial([])
    assert len(st.graficos) == 1, "carteira vazia não desenha pizza"


# ── EUA ───────────────────────────────────────────────────────────────────────

def test_eua_revisao_desenha_os_graficos_antes_de_sair():
    funcoes = _funcoes("views/empresas_americanas.py")
    assert _donas_da_chave("views/empresas_americanas.py", "us_create_pie") == {
        "_render_graficos_alocacao_us"}
    assert _donas_da_chave("views/empresas_americanas.py", "us_create_sector") == {
        "_render_graficos_alocacao_us"}
    donas = [f for f in funcoes.values()
             if _chama(f, "render_portfolio_review") and _chama(f, "_render_graficos_alocacao_us")]
    assert donas, "a revisão dos EUA não desenha os gráficos de alocação"
    for funcao in donas:
        revisao = min(_chama(funcao, "render_portfolio_review"))
        graficos = _chama(funcao, "_render_graficos_alocacao_us")
        assert any(linha > revisao for linha in graficos)
        # A carteira normal também passa pela mesma função.
        assert len(graficos) >= 2


def test_eua_graficos_da_revisao_parcial(monkeypatch):
    import views.empresas_americanas as eua

    st = _StFalso()
    monkeypatch.setattr(eua, "st", st)
    itens = pd.DataFrame([
        {"symbol": "AAPL", "sector_group": "Tecnologia", "weight": 0.3, "ticker": "AAPL"},
        {"symbol": "MSFT", "sector_group": "Tecnologia", "weight": 0.3, "ticker": "MSFT"},
        {"symbol": "JNJ", "sector_group": None, "weight": 0.2, "ticker": "JNJ"},
    ])
    eua._render_graficos_alocacao_us(itens)
    graficos = dict(st.graficos)
    assert graficos.keys() == {"us_create_pie", "us_create_sector"}
    por_ativo = _fatias(graficos["us_create_pie"])
    assert abs(por_ativo["Não alocado"] - 0.2) < 1e-9
    barras = graficos["us_create_sector"].data[0]
    por_setor = dict(zip(barras.y, barras.x))
    assert abs(por_setor["Tecnologia"] - 0.6) < 1e-9
    assert "Não classificado" in por_setor and "Não alocado" in por_setor


def test_eua_graficos_ignoram_composicao_vazia(monkeypatch):
    import views.empresas_americanas as eua

    st = _StFalso()
    monkeypatch.setattr(eua, "st", st)
    eua._render_graficos_alocacao_us(pd.DataFrame())
    eua._render_graficos_alocacao_us(pd.DataFrame([{"symbol": "AAPL"}]))
    assert st.graficos == []
