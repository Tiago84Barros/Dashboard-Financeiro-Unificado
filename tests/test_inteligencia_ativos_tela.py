"""Aba Inteligência dos Ativos: onboarding quando bloqueada, análise quando não."""
import ast
from html import escape
from pathlib import Path

from core.estrategia import politica as pol
from core.estrategia import portao
from core.inteligencia_ativos import modelos as m
from core.estrategia import repositorio as repo
from views import inteligencia_ativos as tela

RAIZ = Path(__file__).resolve().parents[1]

CARTEIRA_COMPLETA = {"total_mercado": 1000.0, "posicoes": [
    {"ticker": "TAEE11", "nome": "Taesa", "classe": "Ações BR",
     "setor": "Utilidades", "moeda": "BRL", "pct_carteira": 40.0,
     "total_investido": 380.0, "valor_mercado": 400.0},
    {"ticker": "HGLG11", "nome": "CSHG Logística", "classe": "FII",
     "setor": "Logística", "moeda": "BRL", "pct_carteira": 60.0,
     "total_investido": 550.0, "valor_mercado": 600.0},
]}


def _bloqueada(status=pol.NOT_STARTED, pct=0.0, faltantes=pol.OBRIGATORIOS,
               motivo=portao.CONFIGURACAO_NECESSARIA):
    return portao.Liberacao(False, status, pct, faltantes=tuple(faltantes),
                            motivo=motivo)


def _liberada():
    politica = pol.aplicar({}, {
        "objective": "renda_passiva", "time_horizon": "longo",
        "risk_profile": "moderado", "liquidity_need": "baixa",
        "predominant_strategy": "dividendos", "single_asset_limit_pct": 50,
        "asset_class_targets": {"renda_fixa": 40, "acoes_br": 20,
                                "fiis": 30, "exterior": 10}},
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


def test_liberada_mostra_premissa_resumo_e_os_13_cartoes():
    app = _rodar(_liberada(), CARTEIRA_COMPLETA)
    assert not app.exception
    assert "já está disponível" in app.success[0].value
    assert "versão 3" in app.success[0].value
    htmls = [md.value for md in app.markdown]
    assert any("Premissa de toda análise" in h for h in htmls)
    assert app.dataframe[0].value["Ativo"].tolist() == ["HGLG11", "TAEE11"]
    assert app.selectbox(key="ia_ativo").value == "HGLG11"

    fluxo = next(h for h in htmls if "01 · Ativo" in h)
    titulos = ["Ativo", "Papel na carteira", "Peso atual",
               "Peso desejado / faixa desejada", "Fundamentos", "Valuation",
               "Comparação com pares", "Cenário", "Notícias", "Relatórios",
               "Próximos eventos", "Impacto na carteira", "Ação a considerar"]
    posicoes = [fluxo.index(f"{n:02d} · {t}") for n, t in
                enumerate(titulos, start=1)]
    assert posicoes == sorted(posicoes)
    assert "Papel principal: renda imobiliária." in fluxo
    assert "Nenhum alvo individual é presumido" in fluxo
    assert fluxo.count("em preparação</span>") == 6  # fundamentos é real
    assert "Dado · fornecido pelo sistema" in fluxo
    assert "P/VP" in fluxo and "Dado não disponível." in fluxo
    questoes = next(h for h in htmls if "flex:1 1 220px" in h)
    for pergunta in m.PERGUNTAS.values():
        assert escape(pergunta) in questoes
    assert "ATIVO EM ANÁLISE: HGLG11" in app.code[0].value

    app.selectbox(key="ia_ativo").set_value("TAEE11").run(timeout=30)
    assert any("ATIVO EM ANÁLISE: TAEE11" in c.value for c in app.code)


def test_liberada_sem_posicoes_nao_quebra():
    app = _rodar(_liberada(), {"posicoes": []})
    assert not app.exception
    assert "Nenhum ativo" in app.info[0].value


def test_cartoes_da_analise_so_usam_tokens_de_tema():
    from core.inteligencia_ativos import analise, contexto
    ctx = contexto.montar(_liberada().politica, CARTEIRA_COMPLETA)
    a = analise.analisar_carteira(ctx)[0]
    for html in (tela.cartao_premissa(ctx), tela.fluxo_html(a),
                 tela.cartao_questoes(a)):
        assert "#" not in html.replace("&#", "")


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


def test_cartao_de_calculos_aparece_com_alertas_tabela_e_concentracao():
    from core.inteligencia_ativos import contexto
    app = _rodar(_liberada(), CARTEIRA_COMPLETA)
    assert not app.exception
    ctx = contexto.montar(_liberada().politica, CARTEIRA_COMPLETA)
    html = next(md.value for md in app.markdown
                if "Cálculos da carteira" in md.value)
    assert "feitos pelo sistema, não pela IA" in html
    for trecho in ("Alertas objetivos", "Alocação atual vs alvo",
                   "Concentração", "Overweight", "Underweight", "HHI"):
        assert trecho in html
    for alerta in ctx.calculos.alertas:
        assert escape(alerta.mensagem) in html
    assert "#" not in tela.cartao_calculos(ctx.calculos).replace("&#", "")
