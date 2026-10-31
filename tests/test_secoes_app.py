"""Seções do menu que cada conta escolhe ver (Configurações → Geral, 31/10/2026).

O que este arquivo tranca:

1. **O catálogo não envelhece calado.** ``core.secoes_app.SECOES`` é conferido
   contra ``app.py::_ROTAS`` lido por AST: tela nova no menu sem interruptor
   quebra aqui (``memoria: verificador-e-escritor-listas-diferentes``).
2. **A porta de volta não fecha.** Dashboard Geral e Configurações não saem por
   nenhum caminho -- nem pela tela, nem por uma gravação adulterada ou antiga.
3. **Esconder não apaga.** A gravação mexe só na chave da preferência; o resto
   do ``extra_settings`` (chats, tema, categorias) continua de pé.
4. **O Dashboard Geral acompanha o menu** -- sem deixar buraco na numeração nem
   ler o dado de uma seção que não aparece.
"""
from __future__ import annotations

import ast
import contextlib
import inspect
import re
import runpy
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from sqlalchemy import text

import views.dashboard_geral as dashboard
from core import secoes_app
from core import user_preferences as preferences
from tests import test_multiuser as fixtures
from tests.app_bootstrap_stubs import instalar_stubs_de_bootstrap
from tests.test_multiuser import ADMIN, OTHER, accounts, login_as

postgres = fixtures.postgres
user_state = fixtures.user_state

RAIZ = Path(__file__).resolve().parents[1]


def _rotas_do_app() -> list[tuple[str, str]]:
    arvore = ast.parse((RAIZ / "app.py").read_text(encoding="utf-8"))
    rotas = next(n.value for n in ast.walk(arvore)
                 if isinstance(n, ast.AnnAssign)
                 and getattr(n.target, "id", "") == "_ROTAS")
    return [(k.value, v.value) for k, v in zip(rotas.keys, rotas.values)]


# -- catálogo -----------------------------------------------------------------

def test_catalogo_e_o_menu_do_app_na_mesma_ordem():
    assert [(s.rotulo, s.modulo) for s in secoes_app.SECOES] == _rotas_do_app()


def test_so_dashboard_geral_e_configuracoes_sao_fixas():
    assert secoes_app.FIXAS == ("dashboard_geral", "configuracoes")
    assert secoes_app.OPCIONAIS == (
        "controle_financeiro", "investimentos", "empresas_b3",
        "empresas_americanas", "fiis", "portfolio_global",
    )


def test_cada_secao_explica_o_que_e():
    for secao in secoes_app.SECOES:
        assert secao.descricao.strip(), secao.modulo
        assert secao.nome in secao.rotulo, secao.modulo


# -- regras puras -------------------------------------------------------------

def test_normalizar_descarta_fixa_desconhecida_e_repetida():
    pedidas = ["fiis", "configuracoes", "dashboard_geral", "tela_que_saiu",
               "fiis", "", None, "controle_financeiro"]
    # Na ordem do menu, não na ordem pedida.
    assert secoes_app.normalizar(pedidas) == ("controle_financeiro", "fiis")
    assert secoes_app.normalizar(None) == ()


def test_filtrar_menu_tira_as_ocultas_e_nunca_as_fixas():
    rotas = dict(_rotas_do_app())
    menu = list(rotas)
    filtrado = secoes_app.filtrar_menu(
        menu, rotas, {"empresas_b3", "fiis", "configuracoes", "dashboard_geral"})
    assert filtrado == [
        "📊 Dashboard Geral", "💰 Controle Financeiro", "📈 Investimentos",
        "🌎 Empresas Americanas", "🌐 Portfólio Global", "⚙️ Configurações",
    ]
    # Esconder tudo o que dá deixa exatamente as duas portas.
    tudo = secoes_app.filtrar_menu(menu, rotas, secoes_app.TODAS)
    assert tudo == ["📊 Dashboard Geral", "⚙️ Configurações"]


def test_visiveis_inclui_sempre_as_fixas():
    assert secoes_app.visiveis(secoes_app.TODAS) == frozenset(secoes_app.FIXAS)
    assert secoes_app.visiveis(()) == secoes_app.TODAS


# -- sessão -------------------------------------------------------------------

@pytest.fixture
def sessao(monkeypatch):
    estado: dict = {}
    conta = {"id": ADMIN}
    monkeypatch.setattr(secoes_app, "_uid", lambda: conta["id"])
    leituras: list[str] = []

    def ler():
        leituras.append(conta["id"])
        return ["fiis", "configuracoes"]

    monkeypatch.setattr(preferences, "load_secoes_ocultas", ler)
    return SimpleNamespace(estado=estado, conta=conta, leituras=leituras)


def test_le_uma_vez_por_sessao_e_por_conta(sessao):
    assert secoes_app.ocultas_da_sessao(sessao.estado) == {"fiis"}
    assert secoes_app.ocultas_da_sessao(sessao.estado) == {"fiis"}
    assert sessao.leituras == [ADMIN]
    # Outra conta no mesmo navegador não herda a escolha da anterior.
    sessao.conta["id"] = OTHER
    secoes_app.ocultas_da_sessao(sessao.estado)
    assert sessao.leituras == [ADMIN, OTHER]


def test_sem_conta_nao_vai_ao_banco(sessao):
    sessao.conta["id"] = ""
    assert secoes_app.ocultas_da_sessao(sessao.estado) == frozenset()
    assert sessao.leituras == []


def test_falha_de_leitura_mostra_tudo_avisa_e_espera_para_tentar(sessao, monkeypatch):
    def quebra():
        sessao.leituras.append("falhou")
        raise RuntimeError("pool ocupado")

    monkeypatch.setattr(preferences, "load_secoes_ocultas", quebra)
    assert secoes_app.ocultas_da_sessao(sessao.estado) == frozenset()
    assert secoes_app.leitura_falhou(sessao.estado)
    # O menu é redesenhado a cada clique: a segunda chamada não insiste.
    secoes_app.ocultas_da_sessao(sessao.estado)
    assert sessao.leituras == ["falhou"]
    # Passada a espera, tenta de novo.
    monkeypatch.setattr(secoes_app, "_ESPERA_APOS_FALHA_S", 0.0)
    secoes_app.ocultas_da_sessao(sessao.estado)
    assert sessao.leituras == ["falhou", "falhou"]
    # "Ler de novo" de Configurações também tenta na hora.
    monkeypatch.setattr(secoes_app, "_ESPERA_APOS_FALHA_S", 3600.0)
    secoes_app.esquecer_leitura(sessao.estado)
    secoes_app.ocultas_da_sessao(sessao.estado)
    assert sessao.leituras == ["falhou", "falhou", "falhou"]


def test_gravar_poe_a_escolha_na_sessao_so_depois_do_banco(sessao, monkeypatch):
    gravado: list[list[str]] = []
    monkeypatch.setattr(preferences, "save_secoes_ocultas", gravado.append)
    ok, msg = secoes_app.gravar(["portfolio_global", "configuracoes", "empresas_b3"],
                                sessao.estado)
    assert ok
    assert gravado == [["empresas_b3", "portfolio_global"]]
    assert "Empresas B3, Portfólio Global" in msg and "Nada foi apagado" in msg
    assert secoes_app.ocultas_da_sessao(sessao.estado) == {"empresas_b3", "portfolio_global"}
    assert sessao.leituras == []

    def quebra(_lista):
        raise RuntimeError("SECRET host db.internal")

    monkeypatch.setattr(preferences, "save_secoes_ocultas", quebra)
    monkeypatch.setattr("core.lacunas.registrar_excecao", lambda *a, **k: None)
    ok, msg = secoes_app.gravar([], sessao.estado)
    assert not ok and "db.internal" not in msg
    # O banco recusou: a sessão continua com o que o banco tem.
    assert secoes_app.ocultas_da_sessao(sessao.estado) == {"empresas_b3", "portfolio_global"}


def test_devolver_tudo_avisa_que_o_menu_esta_completo(sessao, monkeypatch):
    monkeypatch.setattr(preferences, "save_secoes_ocultas", lambda _l: None)
    ok, msg = secoes_app.gravar((), sessao.estado)
    assert ok and msg == "Todas as seções estão no menu."


# -- banco --------------------------------------------------------------------

def test_gravar_mexe_so_na_chave_e_le_de_volta(postgres, user_state):
    login_as(user_state, ADMIN)
    # Conta sem linha em user_settings: a preferência cria a linha.
    assert preferences.load_secoes_ocultas() == []
    conversa = {"synthetic": {"messages": ["x" * 5_000]}}
    preferences.save_theme("light")
    with postgres.begin() as conn:
        extra = accounts.locked_preferences(conn, ADMIN)
        extra["app4_private_chats_v2"] = conversa
        accounts.write_preferences(conn, ADMIN, extra)

    preferences.save_secoes_ocultas(["fiis", "empresas_b3"])
    assert preferences.load_secoes_ocultas() == ["fiis", "empresas_b3"]
    preferences.save_secoes_ocultas([])
    assert preferences.load_secoes_ocultas() == []

    with postgres.connect() as conn:
        extra = conn.execute(text(
            "SELECT extra_settings FROM user_settings WHERE user_id=:uid"
        ), {"uid": ADMIN}).scalar()
    assert extra["app4_private_chats_v2"] == conversa
    assert extra["app4_theme"] == "light"
    assert extra[preferences.SECOES_OCULTAS_FIELD] == []


def test_valor_estranho_na_chave_vira_nada_oculto(postgres, user_state):
    login_as(user_state, ADMIN)
    preferences.save_secoes_ocultas(["fiis"])
    with postgres.begin() as conn:
        conn.execute(text(
            "UPDATE user_settings SET extra_settings = jsonb_set(extra_settings, "
            "ARRAY[:campo], '\"fiis\"'::jsonb) WHERE user_id = :uid"
        ), {"uid": ADMIN, "campo": preferences.SECOES_OCULTAS_FIELD})
    assert preferences.load_secoes_ocultas() == []


def test_leitura_pede_so_a_chave(monkeypatch, user_state):
    """O ``extra_settings`` da conta grande tem centenas de KB de conversa; a
    preferência do menu não pode trazê-lo a cada sessão."""
    login_as(user_state, ADMIN)
    consultas: list[str] = []

    class _Conexao:
        def execute(self, sql, _params):
            consultas.append(str(sql))
            return SimpleNamespace(scalar=lambda: '["fiis"]')

    monkeypatch.setattr(preferences, "_engine", lambda: SimpleNamespace(
        connect=lambda: contextlib.nullcontext(_Conexao())))
    # Driver que devolve jsonb como texto também é entendido.
    assert preferences.load_secoes_ocultas() == ["fiis"]
    assert not any(re.search(r"select\s+extra_settings\s+from", c, re.I) for c in consultas)


# -- app.py -------------------------------------------------------------------

class _StreamlitDoMenu(ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.sidebar = nullcontext()
        self.opcoes: list[str] = []

    def set_page_config(self, **_k):
        pass

    def markdown(self, *_a, **_k):
        pass

    def radio(self, _label, options, **_k):
        self.opcoes = list(options)
        return options[0]

    def warning(self, *_a, **_k):
        pass


def test_menu_lateral_obedece_a_escolha_da_conta(monkeypatch):
    monkeypatch.delenv("APP_TEST_MODE", raising=False)
    fake_st = _StreamlitDoMenu()
    instalar_stubs_de_bootstrap(
        monkeypatch, fake_st,
        core_secoes_app=SimpleNamespace(
            filtrar_menu=secoes_app.filtrar_menu,
            ocultas_da_sessao=lambda: frozenset({"fiis", "investimentos",
                                                 "configuracoes"}),
        ),
    )
    monkeypatch.setattr("importlib.import_module",
                        lambda _n: SimpleNamespace(render=lambda: None))
    runpy.run_path(str(RAIZ / "app.py"), run_name="menu_personalizado_test")
    assert fake_st.opcoes == [
        "📊 Dashboard Geral", "💰 Controle Financeiro", "🏢 Empresas B3",
        "🌎 Empresas Americanas", "🌐 Portfólio Global", "⚙️ Configurações",
    ]


# -- Dashboard Geral ----------------------------------------------------------

def _capture_markdown(monkeypatch) -> list[str]:
    rendered: list[str] = []
    monkeypatch.setattr(dashboard.st, "markdown",
                        lambda body, **_k: rendered.append(body))
    return rendered


def _colunas(monkeypatch) -> list[int]:
    linhas: list[int] = []

    def columns(spec, **_k):
        n = spec if isinstance(spec, int) else len(spec)
        linhas.append(n)
        return [contextlib.nullcontext()] * n

    monkeypatch.setattr(dashboard.st, "columns", columns)
    return linhas


def test_kpis_so_das_secoes_no_menu_e_grade_sem_buraco(monkeypatch):
    rendered = _capture_markdown(monkeypatch)
    argumentos = ({"total": 0, "investido": 1_000.0}, 10_000, 5_000, 2_000,
                  {"total_mercado": 1_000.0, "num_ativos": 3,
                   "rentabilidade_total_pct": 4.0})

    dashboard._render_kpi_grid(*argumentos, areas=frozenset({"controle_financeiro"}))
    html = "".join(rendered)
    assert "Saldo líquido do mês" in html and "Taxa de poupança" in html
    for fora in ("Patrimônio investido", "Rentabilidade da carteira", "Proventos recebidos"):
        assert fora not in html, fora
    assert "--dg-kpi-cols:2" in html

    rendered.clear()
    dashboard._render_kpi_grid(*argumentos, areas=frozenset({"investimentos"}))
    html = "".join(rendered)
    assert "Saldo líquido do mês" not in html and "--dg-kpi-cols:3" in html

    rendered.clear()
    dashboard._render_kpi_grid(*argumentos, areas=frozenset({"empresas_b3"}))
    assert rendered == []

    rendered.clear()
    dashboard._render_kpi_grid(*argumentos)
    assert "--dg-kpi-cols:5" in "".join(rendered)


def test_grade_de_kpis_le_o_numero_de_colunas():
    css = re.sub(r"/\*.*?\*/", "", dashboard._DASHBOARD_STYLES, flags=re.S)
    regra = css[css.index(".dg-kpi-grid {"):]
    regra = regra[:regra.index("}")]
    assert "repeat(var(--dg-kpi-cols, 5)" in regra


def test_resumo_por_area_numera_o_que_sobrou(monkeypatch):
    rendered = _capture_markdown(monkeypatch)
    linhas = _colunas(monkeypatch)
    faixas: list[str] = []
    monkeypatch.setattr(dashboard, "_faixa_destaque", lambda *a, **k: faixas.append(a[1]))
    argumentos = (1.0, 1.0, 0.0, {"investido": 0.0}, [], 0.0, {}, {}, {}, [], False)

    dashboard._secao_resumo_modulos(
        *argumentos, areas=frozenset({"investimentos", "fiis", "empresas_americanas"}))
    cartoes = [b for b in rendered if "border-left:4px" in b]
    titulos = [re.search(r"font-weight:850[^>]*>([^<]+)<", c).group(1) for c in cartoes]
    assert titulos == ["Investimentos", "Empresas Americanas", "Seleção de FIIs"]
    numeros = [re.search(r"flex-shrink:0;\">(\d+)<", c).group(1) for c in cartoes]
    assert numeros == ["1", "2", "3"]
    assert linhas == [2, 2] and faixas == ["Resumo por área"]

    rendered.clear()
    faixas.clear()
    dashboard._secao_resumo_modulos(*argumentos, areas=frozenset({"portfolio_global"}))
    assert rendered == [] and faixas == []


def test_modelos_de_secao_oculta_nem_sao_lidos(monkeypatch):
    monkeypatch.setattr(dashboard.settings, "MOCK_MODE", False)

    def proibido(*_a, **_k):
        raise AssertionError("seção fora do menu não pode ser lida")

    monkeypatch.setattr(dashboard, "load_active_b3_portfolio_model", proibido)
    monkeypatch.setattr(dashboard, "_fiis_carteira_modelo", proibido)
    monkeypatch.setattr(dashboard, "_carteira_modelo_us", lambda: {"items": [1]})
    assert dashboard._load_decision_models(frozenset({"empresas_americanas"})) == (
        {}, {"items": [1]}, [], False)


def test_destaque_da_carteira_recomendada_nomeia_so_o_que_vem_abaixo(monkeypatch):
    rendered = _capture_markdown(monkeypatch)
    dashboard._destaque_carteira_recomendada(("fiis",))
    html = "".join(rendered)
    assert "Composição sugerida por Seleção de FIIs." in html
    assert "Empresas B3" not in html

    rendered.clear()
    dashboard._destaque_carteira_recomendada()
    assert ("Composição sugerida por Empresas B3, Empresas Americanas e Seleção "
            "de FIIs.") in "".join(rendered)


def test_cabecalho_descreve_o_que_a_pagina_reune():
    todas = secoes_app.TODAS
    assert dashboard._subtitulo_do_cabecalho(todas).startswith(
        "Caixa, patrimônio e decisões de investimento")
    assert dashboard._subtitulo_do_cabecalho(
        frozenset({"controle_financeiro", "fiis"})
    ) == ("Leitura executiva do que você mantém no menu: o caixa do mês e as "
          "carteiras recomendadas.")
    assert "Configurações → Geral" in dashboard._subtitulo_do_cabecalho(
        frozenset(secoes_app.FIXAS))


def test_render_le_o_menu_e_cada_bloco_obedece_a_sua_secao():
    corpo = inspect.getsource(dashboard.render)
    assert "visiveis_da_sessao()" in corpo
    assert "_load_decision_models(areas)" in corpo
    assert "areas=areas" in corpo
    # Caixa: a evolução financeira inteira fica sob a guarda do Controle
    # Financeiro, e a leitura do mês também.
    guarda = corpo.index("if tem_caixa:\n        _faixa_destaque(")
    assert guarda < corpo.index('key="dg_history_plot"') < corpo.index("if tem_carteira:")
    assert "if tem_carteira:\n        _secao_raio_x_portfolio(" in corpo
    # Sem o caixa, o chip de período não pode cair no último mês com lançamento
    # ao lado de proventos do mês corrente.
    assert "mes_corrente = cur is not None or not tem_caixa" in corpo
    assert "_rotulo_do_periodo(mes_corrente, d, hoje)" in corpo
    # Painel sem seção nenhuma avisa onde se desfaz, em vez de ficar em branco,
    # e decide isso ANTES de ler qualquer dado.
    assert corpo.index("_render_painel_sem_secoes(hoje)") < corpo.index("get_visao_geral()")


def test_painel_sem_secoes_nao_consulta_o_resumo_agregado(monkeypatch):
    """Revisão do PR #630: o resumo agregado junta caixa e carteira numa leitura
    só. Quem escondeu todas as seções com resumo não pode ficar sem o aviso
    porque uma dessas leituras falhou."""
    rendered = _capture_markdown(monkeypatch)
    monkeypatch.setattr(dashboard, "visiveis_da_sessao",
                        lambda: frozenset(secoes_app.FIXAS) | {"portfolio_global"})

    def proibido(*_a, **_k):
        raise AssertionError("o painel sem seções não lê dado nenhum")

    for nome in ("get_visao_geral", "get_cashflow_mensal", "get_historico_anual",
                 "get_carteira", "get_evolucao_patrimonial",
                 "get_gastos_categoria_anual", "_load_decision_models"):
        monkeypatch.setattr(dashboard, nome, proibido)
    dashboard.render()
    html = "".join(rendered)
    assert "Painel sem seções" in html and "Seu dinheiro, em perspectiva." in html
    assert 'class="dg-kpi-grid"' not in html


def test_aviso_de_painel_vazio_aponta_onde_se_desfaz(monkeypatch):
    rendered = _capture_markdown(monkeypatch)
    dashboard._aviso_painel_sem_secoes()
    html = "".join(rendered)
    assert "Configurações → Geral → Seções do aplicativo" in html
    assert "Nada foi apagado" in html


# -- Configurações → Geral ----------------------------------------------------

def test_bloco_de_secoes_vem_logo_depois_do_tema_e_os_numeros_seguem():
    arvore = ast.parse((RAIZ / "views/configuracoes_geral.py").read_text(encoding="utf-8"))
    render = next(n for n in arvore.body
                  if isinstance(n, ast.FunctionDef) and n.name == "render")
    chamadas = [n.value.func.id for n in render.body
                if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Name)]
    assert chamadas == ["_render_tema", "_render_secoes", "_render_categorias",
                        "_render_orcamento", "_render_trocar_usuario",
                        "_render_memoria_llm"]
    cabecalhos = [n.args[1].value for n in ast.walk(arvore)
                  if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_cabecalho"]
    indices = [n.args[0].value for n in ast.walk(arvore)
               if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_cabecalho"]
    assert dict(zip(indices, cabecalhos))["02"] == "🧭 Seções do aplicativo"
    # Um número por bloco, sem buraco nem repetição: dois PRs que acrescentam
    # bloco ao mesmo tempo (Seções e Orçamento, 31/10/2026) colidiam no "03".
    assert sorted(indices) == [f"{n:02d}" for n in range(1, len(chamadas) + 1)]


# -- leitura que falha com Configurações aberta (revisão do PR #630) ----------

def _tela_de_secoes():
    """Script do AppTest: o bloco de seções com um banco de mentira na sessão.

    ``_falhar`` liga e desliga a leitura; ``_banco`` é o que está gravado.
    """
    import time

    import streamlit as st

    from core import user_preferences as prefs

    banco = st.session_state.setdefault("_banco", {"ocultas": ["fiis", "empresas_b3"]})

    def ler():
        if st.session_state.get("_falhar"):
            raise RuntimeError("pool ocupado")
        return list(banco["ocultas"])

    def gravar(lista):
        banco["ocultas"] = list(lista)

    prefs.load_secoes_ocultas = ler
    prefs.save_secoes_ocultas = gravar
    from core import secoes_app

    # Sem espera entre tentativas: cada execução tenta ler de novo, que é o
    # que acontece de verdade quando os 60 s passam com a tela aberta.
    secoes_app._ESPERA_APOS_FALHA_S = st.session_state.get("_espera", 60.0)
    st.session_state["_app4_user"] = {"id": "u1", "expires_at": time.time() + 600}
    from views import configuracoes_geral as cg

    cg._render_secoes()


def _interruptores(at) -> dict[str, bool]:
    return {t.label: t.value for t in at.toggle}


def _salvar(at):
    return next(b for b in at.button if "Salvar" in b.label)


def test_leitura_que_volta_realinha_os_interruptores_e_nao_desfaz_a_escolha(monkeypatch):
    from streamlit.testing.v1 import AppTest

    # O script mexe na espera do módulo; o monkeypatch a devolve no fim.
    monkeypatch.setattr(secoes_app, "_ESPERA_APOS_FALHA_S", 60.0)

    at = AppTest.from_function(_tela_de_secoes, default_timeout=30)
    at.session_state["_falhar"] = True
    at.run()
    assert not at.exception
    # Leitura falhou: tudo ligado, aviso na tela e Salvar bloqueado -- gravar
    # agora apagaria FIIs e B3 da escolha guardada.
    assert all(_interruptores(at).values())
    assert any("Não foi possível ler" in w.value for w in at.warning)
    assert _salvar(at).disabled
    at.toggle(key="cfg_geral_secao_portfolio_global").set_value(False).run()
    assert _salvar(at).disabled, "salvar durante a falha sobrescreveria a escolha"

    # A leitura volta SOZINHA, passada a espera, na mesma execução em que os
    # interruptores são desenhados -- o caminho que a revisão apontou. (O botão
    # "Ler de novo" interrompe a execução com st.rerun antes dos interruptores,
    # e por isso o Streamlit já descartava o estado deles.)
    at.session_state["_falhar"] = False
    at.session_state["_espera"] = 0.0
    at.run()
    assert not at.exception
    estado = _interruptores(at)
    assert estado["🏬 Seleção de FIIs"] is False
    assert estado["🏢 Empresas B3"] is False
    assert estado["🌐 Portfólio Global"] is True
    assert not at.warning
    # Nada pendente: o que está na tela é o que está gravado.
    assert _salvar(at).disabled

    # E uma mudança de verdade grava sem devolver as outras ao menu.
    at.toggle(key="cfg_geral_secao_investimentos").set_value(False).run()
    _salvar(at).click().run()
    assert at.session_state["_banco"]["ocultas"] == ["investimentos", "empresas_b3", "fiis"]
