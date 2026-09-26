"""Aba Inteligência dos Ativos: onboarding quando bloqueada, análise quando não."""
import ast
from pathlib import Path

from core import inteligencia_ativos as servico
from core.estrategia import politica as pol
from core.estrategia import portao
from core.estrategia import repositorio as repo
from views import inteligencia_ativos as tela

RAIZ = Path(__file__).resolve().parents[1]

CARTEIRA = {"posicoes": [{"ticker": "HGLG11", "nome": "CSHG Logística"}]}


def _bloqueada(status=pol.NOT_STARTED, pct=0.0, faltantes=pol.OBRIGATORIOS,
               motivo=portao.CONFIGURACAO_NECESSARIA):
    return portao.Liberacao(False, status, pct, faltantes=tuple(faltantes),
                            motivo=motivo)


def _liberada():
    politica = pol.aplicar({}, {"objective": "renda_passiva"},
                           fonte="manual")[0]
    reg = repo.Registro(
        id="r1", version=3, status_gravado="COMPLETED",
        schema_version=pol.SCHEMA_VERSION, politica=politica, entrevista=[],
        completion_pct=100, completed_at=None, created_at=None,
        updated_at=None)
    return portao.Liberacao(True, pol.COMPLETED, 100.0, politica=reg)


def _rodar(liberacao, carteira=None):
    from streamlit.testing.v1 import AppTest

    def _app(liberacao, carteira):
        from views.inteligencia_ativos import render
        render(liberacao, carteira)
    return AppTest.from_function(_app, args=(liberacao, carteira)).run(timeout=30)


def test_rotulo_mostra_cadeado_so_quando_bloqueada():
    assert tela.rotulo_aba(_bloqueada()) == "🔒  Inteligência dos Ativos"
    assert tela.rotulo_aba(_liberada()) == "🧠  Inteligência dos Ativos"


def test_nao_iniciada_orienta_e_leva_para_a_estrategia():
    app = _rodar(_bloqueada())
    assert not app.exception
    html = app.markdown[0].value
    assert "Configure sua estratégia para liberar esta análise" in html
    assert "Configurações</strong> → <strong>Geral</strong> → " in html
    assert html.count("○") == len(pol.OBRIGATORIOS) and "✓" not in html
    assert "0% concluída" in app.get("progress")[0].proto.text
    botao = app.button[0]
    assert botao.label == "Configurar minha estratégia"

    botao.click().run(timeout=30)
    assert app.session_state[tela.NAVEGACAO_KEY] == tela.ROTA_CONFIGURACOES
    assert app.session_state[tela.VEIO_DA_ANALISE] is True


def test_em_andamento_mostra_progresso_e_o_que_falta():
    app = _rodar(_bloqueada(pol.IN_PROGRESS, 50.0,
                            ["risk_profile", "predominant_strategy",
                             "asset_class_targets"]))
    html = app.markdown[0].value
    assert html.count("✓") == 3 and html.count("○") == 3
    assert "✓ Objetivo principal" in html
    assert "50% concluída" in app.get("progress")[0].proto.text
    assert app.button[0].label == "Continuar configuração"


def test_revisao_tem_texto_e_botao_proprios():
    app = _rodar(_bloqueada(pol.NEEDS_REVIEW, 100.0, (),
                            portao.REVISAO_NECESSARIA))
    assert "Revise sua estratégia" in app.markdown[0].value
    assert app.button[0].label == "Revisar minha estratégia"


def test_cartao_so_usa_tokens_de_tema():
    html = tela.cartao_onboarding(_bloqueada())
    assert "#" not in html.replace("&#", "")  # sem cor literal no inline


def test_linguagem_de_onboarding_nao_de_erro():
    for lib in (_bloqueada(), _bloqueada(pol.NEEDS_REVIEW, 100.0, (),
                                         portao.REVISAO_NECESSARIA)):
        texto = tela.cartao_onboarding(lib).lower()
        for proibida in ("acesso negado", "erro", "sem permissão"):
            assert proibida not in texto


def test_liberada_analisa_pelo_servico(monkeypatch):
    chamadas = []

    def _falso(ticker, **kw):
        chamadas.append((ticker, kw["carteira"]))
        return {"analysis_available": True, "analysis": None,
                "policy_context": "Objetivo principal: Renda passiva"}
    monkeypatch.setattr(servico, "analisar_ativo", _falso)

    app = _rodar(_liberada(), CARTEIRA)
    assert not app.exception
    assert "já está disponível" in app.success[0].value
    assert "versão 3" in app.success[0].value
    app.button[0].click().run(timeout=30)
    assert chamadas == [("HGLG11", CARTEIRA)]
    assert "Renda passiva" in app.code[0].value


def test_aba_esta_em_investimentos_e_constantes_batem_com_o_app():
    fonte = (RAIZ / "views" / "investimentos.py").read_text(encoding="utf-8")
    assert "_ia.rotulo_aba(_liberacao)" in fonte
    assert "_ia.render(_liberacao, carteira)" in fonte

    app = ast.parse((RAIZ / "app.py").read_text(encoding="utf-8"))
    literais = {n.value for n in ast.walk(app)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert tela.NAVEGACAO_KEY in literais
    assert tela.ROTA_CONFIGURACOES in literais

    from views import configuracoes_estrategia as cfg
    assert cfg._VEIO_DA_ANALISE == tela.VEIO_DA_ANALISE
