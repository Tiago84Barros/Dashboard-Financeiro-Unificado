"""Tela da estratégia (Configurações → Geral): fluxo com repositório em memória."""
import ast
from pathlib import Path

import pytest

from core import llm_estrategia as ent
from core.estrategia import politica as pol
from core.estrategia import repositorio as repo
from views import configuracoes_estrategia as tela

RAIZ = Path(__file__).resolve().parents[1]

MINIMOS = {
    "objective": "renda_passiva", "time_horizon": "longo",
    "risk_profile": "moderado", "liquidity_need": "baixa",
    "predominant_strategy": "dividendos",
    "asset_class_targets": {"renda_fixa": 40, "acoes_br": 20, "fiis": 30,
                            "exterior": 10},
}


def _registro(status="IN_PROGRESS", politica=None, entrevista=None, versao=1):
    return repo.Registro(
        id=f"r{versao}", version=versao, status_gravado=status,
        schema_version=pol.SCHEMA_VERSION, politica=politica or {},
        entrevista=entrevista or [], completion_pct=0, completed_at=None,
        created_at=None, updated_at=None)


class _RepoFalso:
    def __init__(self, monkeypatch, estado):
        self.estado = estado
        self.salvos = []
        self.concluidos = []
        self.iniciados = 0
        monkeypatch.setattr(repo, "carregar", lambda **_k: self.estado)
        monkeypatch.setattr(repo, "salvar_rascunho", self._salvar)
        monkeypatch.setattr(repo, "concluir", self._concluir)
        monkeypatch.setattr(repo, "iniciar", self._iniciar)

    def _salvar(self, rid, politica, entrevista, **_k):
        self.salvos.append((rid, politica, entrevista))
        self.estado = repo.Estado(vigente=self.estado.vigente,
                                  rascunho=_registro(politica=politica,
                                                     entrevista=entrevista))
        return self.estado.rascunho

    def _concluir(self, rid, **_k):
        self.concluidos.append(rid)
        return True, []

    def _iniciar(self, **_k):
        self.iniciados += 1
        self.estado = repo.Estado(rascunho=_registro())
        return self.estado.rascunho


def _app():
    from views.configuracoes_estrategia import render
    render()


def _rodar():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_function(_app).run(timeout=30)


@pytest.fixture(autouse=True)
def _sem_llm_real(monkeypatch):
    monkeypatch.setattr(ent, "llm_disponivel", lambda: True)
    monkeypatch.setattr(ent, "provedores_disponiveis", lambda: ["openai"])
    monkeypatch.setattr(tela, "_contexto", lambda: "")


def test_nao_iniciada_mostra_zero_e_botao_de_iniciar(monkeypatch):
    falso = _RepoFalso(monkeypatch, repo.Estado())
    app = _rodar()
    assert not app.exception
    assert "Não iniciada" in app.markdown[0].value
    botao = next(b for b in app.button if "Iniciar" in b.label)
    botao.click().run(timeout=30)
    assert falso.iniciados == 1
    assert not app.exception


def test_tabela_ausente_pede_a_migration(monkeypatch):
    _RepoFalso(monkeypatch, repo.Estado(tabela_ausente=True))
    app = _rodar()
    assert "migration 076" in app.warning[0].value


def test_entrevista_grava_resposta_pergunta_e_valor(monkeypatch):
    falso = _RepoFalso(monkeypatch, repo.Estado(rascunho=_registro()))
    monkeypatch.setattr(ent, "proxima_etapa", lambda p, h, r, **_k: ent.Etapa(
        politica=pol.aplicar(p, {"objective": "renda_passiva"},
                             fonte="entrevista")[0],
        aplicados=("objective",), pergunta="Em quanto tempo?"))
    app = _rodar()
    assert not app.exception
    assert "uma de cada vez" in app.chat_message[0].markdown[0].value
    concluir = next(b for b in app.button if "Concluir" in b.label)
    assert concluir.disabled

    app.chat_input[0].set_value("Quero viver de renda").run(timeout=30)
    assert not app.exception
    _, politica, conversa = falso.salvos[-1]
    assert pol.valor(politica, "objective") == "renda_passiva"
    assert [m["role"] for m in conversa] == ["assistant", "user", "assistant"]
    assert conversa[-1]["content"] == "Em quanto tempo?"
    assert any("Registrado: Objetivo principal" in s.value for s in app.success)


def test_rascunho_completo_habilita_concluir(monkeypatch):
    completa, _ = pol.aplicar({}, MINIMOS, fonte="manual")
    falso = _RepoFalso(monkeypatch, repo.Estado(
        rascunho=_registro(politica=completa)))
    app = _rodar()
    assert "100% concluída" in app.get("progress")[0].proto.text
    concluir = next(b for b in app.button if "Concluir" in b.label)
    assert not concluir.disabled
    concluir.click().run(timeout=30)
    assert falso.concluidos == ["r1"]


def test_vigente_mostra_respostas_e_editar(monkeypatch):
    completa, _ = pol.aplicar({}, MINIMOS, fonte="entrevista")
    falso = _RepoFalso(monkeypatch, repo.Estado(
        vigente=_registro("COMPLETED", completa)))
    app = _rodar()
    assert "Concluída" in app.markdown[0].value
    assert len(app.dataframe) == 1
    next(b for b in app.button if "Editar" in b.label).click().run(timeout=30)
    assert falso.iniciados == 1


def test_diferencas_so_regrava_o_que_mudou():
    base, _ = pol.aplicar({}, {"objective": "renda_passiva",
                               "monthly_contribution": 1000},
                          fonte="entrevista")
    mudancas, remocoes = tela.diferencas(base, {
        "objective": "renda_passiva",       # igual: mantém a procedência
        "monthly_contribution": None,       # esvaziado: remove
        "risk_profile": "moderado",         # novo
        "time_horizon": None,               # vazio e já vazio: nada
    })
    assert mudancas == {"risk_profile": "moderado"}
    assert remocoes == ["monthly_contribution"]


def test_bloco_e_o_primeiro_da_aba_geral_e_o_nao_admin_tambem_ve():
    geral = ast.parse((RAIZ / "views" / "configuracoes_geral.py")
                      .read_text(encoding="utf-8"))
    render = next(n for n in geral.body
                  if isinstance(n, ast.FunctionDef) and n.name == "render")
    primeira = render.body[0].value.func.id
    assert primeira == "render_estrategia_bloco"
    fonte = (RAIZ / "views" / "configuracoes.py").read_text(encoding="utf-8")
    assert fonte.count("render_estrategia_bloco") == 2  # import + chamada


def test_formulario_renderiza_todos_os_tipos_e_salva_so_a_mudanca(monkeypatch):
    base, _ = pol.aplicar({}, {**MINIMOS, "monthly_contribution": 1500,
                               "makes_contributions": True,
                               "strategy_priorities": ["dividendos"],
                               "asset_class_limits": {"exterior": 20}},
                          fonte="entrevista")
    falso = _RepoFalso(monkeypatch, repo.Estado(rascunho=_registro(politica=base)))
    app = _rodar()
    app.radio[0].set_value(tela._MODO_FORM).run(timeout=30)
    assert not app.exception
    salvar = next(b for b in app.button if "Salvar" in b.label)
    salvar.click().run(timeout=30)
    assert not app.exception
    assert any("Nenhuma resposta mudou" in i.value for i in app.info)
    assert falso.salvos == []

    risco = next(s for s in app.selectbox if s.label.startswith("Perfil de risco"))
    risco.set_value("arrojado")
    next(b for b in app.button if "Salvar" in b.label).click().run(timeout=30)
    assert not app.exception
    _, politica, _ = falso.salvos[-1]
    assert politica["risk_profile"]["source"] == "manual"
    assert politica["objective"]["source"] == "entrevista"  # intocado
