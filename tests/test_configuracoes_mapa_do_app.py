"""🗺️ Mapa do App: o fluxograma de todas as telas, aba de Configurações.

Pedido do usuário em 29/10/2026, para todas as contas. Os testes guardam as
quatro coisas que podem quebrar sem erro nenhum na tela:

- a aba existe para quem não administra (o mapa só descreve o app);
- a página chega inteira ao iframe -- com os dados no lugar do marcador e no
  tema escolhido no app, não no do sistema operacional;
- o ``mapa.json`` versionado é o que as áreas montam (mapa que envelhece
  descreve um app que não existe mais);
- o ramo de Configurações do mapa lista as mesmas abas que a tela abre.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from design import mapa_app
from scripts import montar_mapa_app
from views import configuracoes

RAIZ = Path(__file__).resolve().parents[1]
_DADOS = re.compile(r'<script type="application/json" id="dados">(.*?)</script>', re.S)


def _mapa_versionado() -> dict:
    return json.loads((mapa_app.PASTA / "mapa.json").read_text(encoding="utf-8"))


def _rotulos_das_abas() -> list[str]:
    """Os rótulos que ``configuracoes.render`` passa ao primeiro ``st.tabs``.

    Por AST, como em ``test_app_rotas_alcancaveis``: contar na fonte crua
    contaria também o docstring que lista as abas.
    """
    arvore = ast.parse((RAIZ / "views/configuracoes.py").read_text(encoding="utf-8"))
    render = next(n for n in arvore.body
                  if isinstance(n, ast.FunctionDef) and n.name == "render")
    chamada = next(n for n in ast.walk(render)
                   if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "tabs")
    return [e.value for e in chamada.args[0].elts]


def _sem_emoji(rotulo: str) -> str:
    return rotulo.split(" ", 1)[1]


@pytest.fixture
def iframes(monkeypatch):
    """Roda ``_render_mapa_app`` e devolve o que foi entregue ao iframe."""
    import streamlit.components.v1 as components

    entregues: list[dict] = []
    monkeypatch.setattr(
        components, "html",
        lambda html, **kw: entregues.append({"html": html, **kw}))
    return entregues


# -- a aba -------------------------------------------------------------------

def test_aba_mapa_do_app_fica_ao_lado_da_documentacao():
    rotulos = _rotulos_das_abas()
    assert rotulos.count("🗺️ Mapa do App") == 1
    assert rotulos.index("🗺️ Mapa do App") + 1 == rotulos.index("📚 Documentação")


def test_aba_nao_depende_de_ser_administrador(monkeypatch, iframes):
    """Quem não administra recebe a mesma página de quem administra.

    ``require_admin`` levantando prova que a aba não passa por ele; a
    comparação prova que não há outro caminho que bifurque a página.
    """
    import design.theme_selector as selector

    def _recusa():
        raise PermissionError("Acesso exclusivo do administrador.")

    monkeypatch.setattr("core.user_context.require_admin", _recusa)
    monkeypatch.setattr(selector, "tema_pintado", lambda: "dark")
    for quem in ("outra-pessoa", "admin-1"):
        monkeypatch.setattr("core.user_context.principal", lambda q=quem: {"id": q})
        monkeypatch.setattr("core.config.settings.ADMIN_USER_ID", "admin-1",
                            raising=False)
        configuracoes._render_mapa_app()
    assert len(iframes) == 2
    assert iframes[0] == iframes[1]


def test_iframe_tem_altura_fixa_e_rolagem(monkeypatch, iframes):
    import design.theme_selector as selector

    monkeypatch.setattr(selector, "tema_pintado", lambda: "light")
    configuracoes._render_mapa_app()
    (entregue,) = iframes
    assert entregue["scrolling"] is True
    assert entregue["height"] == configuracoes._ALTURA_MAPA_APP >= 600


def test_trocar_o_tema_nao_muda_quantos_elementos_a_aba_desenha(monkeypatch, iframes):
    """Elemento que aparece só em algumas execuções desloca o ``st.tabs`` e
    devolve a seleção para a primeira aba (ver ``app.py``). Os dois temas
    precisam entregar a mesma coisa, mudando só a cor."""
    import design.theme_selector as selector

    for tema in ("dark", "light"):
        monkeypatch.setattr(selector, "tema_pintado", lambda t=tema: t)
        configuracoes._render_mapa_app()
    escuro, claro = iframes
    assert set(escuro) == set(claro)
    assert escuro["height"] == claro["height"]
    assert 'data-theme="dark"' in escuro["html"]
    assert 'data-theme="light"' in claro["html"]


# -- o tema --------------------------------------------------------------------

def test_tema_pintado_le_a_cor_que_o_topo_do_app_pintou(monkeypatch):
    """Não resolve de novo: o iframe precisa da cor da página em volta."""
    import streamlit as st

    import design.theme_selector as selector

    monkeypatch.setattr(selector, "tema_para_pintar",
                        lambda: pytest.fail("resolveu o tema uma segunda vez"))
    monkeypatch.setitem(st.session_state, selector._PINTADO, "light")
    assert selector.tema_pintado() == "light"


def test_tema_pintado_sem_marca_resolve_pelo_mesmo_caminho(monkeypatch):
    import streamlit as st

    import design.theme_selector as selector

    if selector._PINTADO in st.session_state:
        monkeypatch.delitem(st.session_state, selector._PINTADO)
    monkeypatch.setattr(selector, "tema_para_pintar", lambda: "light")
    assert selector.tema_pintado() == "light"


def test_tema_desconhecido_nao_vira_pagina():
    with pytest.raises(ValueError):
        mapa_app.pagina_html("sepia")


# -- a página ------------------------------------------------------------------

@pytest.mark.parametrize("tema", mapa_app.TEMAS)
def test_pagina_e_um_documento_completo(tema):
    html = mapa_app.pagina_html(tema)
    assert html.startswith("<!doctype html>")
    assert f'<html lang="pt-BR" data-theme="{tema}">' in html
    assert '<meta charset="utf-8">' in html
    assert 'name="viewport"' in html
    assert "[hidden]{display:none!important}" in html
    assert html.rstrip().endswith("</html>")


def test_pagina_traz_os_dados_no_lugar_do_marcador():
    html = mapa_app.pagina_html("dark")
    assert mapa_app.MARCADOR not in html
    (bloco,) = _DADOS.findall(html)
    assert "<" not in bloco, "texto do mapa pode fechar o <script> dos dados"
    assert json.loads(bloco) == _mapa_versionado()


def test_pagina_inclui_o_proprio_mapa_no_ramo_de_configuracoes():
    (bloco,) = _DADOS.findall(mapa_app.pagina_html("light"))
    raiz = json.loads(bloco)
    config = next(a for a in raiz["filhos"] if a["nome"] == "Configurações")
    no = next(f for f in config["filhos"] if f["nome"] == "Mapa do App")
    assert no["so_admin"] is False
    assert no["explicacao"].strip()


# -- o mapa.json -------------------------------------------------------------

def test_mapa_json_e_o_que_as_areas_montam():
    """Editou uma área e não rodou ``scripts/montar_mapa_app.py``: falha aqui."""
    versionado = _mapa_versionado()
    montado, problemas = montar_mapa_app.montar(data_mapa=versionado["data_mapa"])
    assert problemas == []
    assert montado == versionado, "rode: python scripts/montar_mapa_app.py"


def test_mapa_json_nao_leva_a_referencia_de_codigo_para_a_tela():
    assert '"fonte"' not in (mapa_app.PASTA / "mapa.json").read_text(encoding="utf-8")


def test_areas_do_mapa_seguem_o_menu_lateral():
    """Mesma ordem e mesmos nomes do menu, lidos do ``_ROTAS`` de ``app.py``."""
    arvore = ast.parse((RAIZ / "app.py").read_text(encoding="utf-8"))
    rotas = next(n.value for n in ast.walk(arvore)
                 if isinstance(n, ast.AnnAssign)
                 and getattr(n.target, "id", "") == "_ROTAS")
    menu = [_sem_emoji(c.value) for c in rotas.keys]
    areas = [a["nome"] for a in _mapa_versionado()["filhos"] if not a["bastidores"]]
    assert areas == menu


def test_ramo_de_configuracoes_lista_as_abas_da_tela():
    """O mapa mostra as abas que a tela abre -- nem uma a mais, nem uma a menos."""
    config = next(a for a in _mapa_versionado()["filhos"] if a["nome"] == "Configurações")
    assert [f["nome"] for f in config["filhos"]] == [_sem_emoji(r) for r in _rotulos_das_abas()]
