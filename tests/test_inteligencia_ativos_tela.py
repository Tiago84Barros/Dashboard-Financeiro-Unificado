"""Aba Inteligência dos Ativos: onboarding quando bloqueada, análise quando não."""
import ast
from html import escape
from pathlib import Path

import pytest

from core.estrategia import politica as pol
from core.estrategia import portao
from core.estrategia import repositorio as repo
from core.inteligencia_ativos import modelos as m
from views import inteligencia_ativos as tela

RAIZ = Path(__file__).resolve().parents[1]

# A aba mostra os blocos da estratégia e do cenário: repositórios em memória.
pytestmark = pytest.mark.usefixtures("estrategia_falsa", "cenario_falso")

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


def test_nao_iniciada_orienta_e_configura_na_propria_aba(estrategia_falsa):
    app = _rodar(_bloqueada())
    assert not app.exception
    html = app.markdown[0].value
    assert "Configure sua estratégia para liberar esta análise" in html
    assert "<strong>aqui mesmo</strong>" in html
    assert "Configurações" not in html
    assert html.count("○") == len(pol.OBRIGATORIOS) and "✓" not in html
    assert "0% concluída" in app.get("progress")[0].proto.text
    botao = app.button[0]
    assert botao.label == "Configurar minha estratégia"
    # fechada, a aba não lê a estratégia de novo: só o cartão e o botão
    assert len(app.button) == 1

    botao.click().run(timeout=30)
    assert not app.exception
    # um clique só: abre o rascunho e já mostra a entrevista, na mesma aba
    assert estrategia_falsa.iniciados == 1
    assert app.session_state[tela.ESTRATEGIA_ABERTA] is True
    app.run(timeout=30)          # o AppTest não reexecuta sozinho no st.rerun
    assert not app.exception
    assert not any(b.key == "ia_abrir_estrategia" for b in app.button)
    assert any("Estratégia de Investimentos" in md.value for md in app.markdown)
    assert app.button(key="cfg_estrategia_concluir") is not None
    assert "Configuração da estratégia" in app.get("progress")[0].proto.text


def test_em_andamento_abre_o_rascunho_sem_criar_outro(estrategia_falsa):
    from core.estrategia import repositorio as repo
    estrategia_falsa.estado = repo.Estado(rascunho=repo.Registro(
        id="r9", version=1, status_gravado="IN_PROGRESS",
        schema_version=pol.SCHEMA_VERSION, politica={}, entrevista=[],
        completion_pct=50, completed_at=None, created_at=None,
        updated_at=None))
    app = _rodar(_bloqueada(pol.IN_PROGRESS, 50.0, ["risk_profile"]))
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert estrategia_falsa.iniciados == 0
    app.run(timeout=30)
    assert app.button(key="cfg_estrategia_concluir") is not None


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
    assert "já está disponível" in app.caption[0].value
    assert "versão 3" in app.caption[0].value
    htmls = [md.value for md in app.markdown]
    # página resumida: um grupo por classe e uma caixa por ativo
    assert any("Ações" in h and "📈" in h for h in htmls)
    assert any("Fundos imobiliários" in h for h in htmls)
    rotulos = [e.label for e in app.expander]
    # o cenário, lido dos dados, abre a página; depois um ativo por caixa
    assert rotulos[:3] == ["🌎 Cenário econômico atual (lido dos dados)",
                           "TAEE11 · 40,0% → 20,0% · Vender",   # ativo a ativo
                           "HGLG11 · 60,0% → 30,0% · Vender"]
    assert "📊 Visão geral da carteira" in rotulos
    assert any("Premissa de toda análise" in h for h in htmls)
    assert app.dataframe[0].value["Ativo"].tolist() == ["HGLG11", "TAEE11"]
    # a análise detalhada só abre no botão
    assert not any("01 · Ativo" in h for h in htmls)
    assert "ia_ativo" not in [s.key for s in app.selectbox]
    app.toggle(key="ia_detalhe").set_value(True).run(timeout=30)
    assert not app.exception
    htmls = [md.value for md in app.markdown]
    assert app.selectbox(key="ia_ativo").value == "HGLG11"
    # no fim da página: a estratégia vigente
    titulos = [h for h in htmls if h in ("#### Minha estratégia",
                                         "#### Meu cenário")]
    assert titulos == ["#### Minha estratégia"]
    assert app.expander[-1].label == "✏️ Ver ou alterar minha estratégia"

    # O fluxo sai em duas partes, cortado depois da etapa 10 para o resumo
    # por IA dos relatórios; sem trechos no acervo, nada entra entre elas.
    i = next(n for n, h in enumerate(htmls) if "01 · Ativo" in h)
    assert "11 · Próximos eventos" not in htmls[i]
    fluxo = htmls[i] + htmls[i + 1]
    titulos =["Ativo", "Papel na carteira", "Peso atual",
               "Peso desejado / faixa desejada", "Fundamentos", "Valuation",
               "Comparação com pares", "Cenário", "Notícias", "Relatórios",
               "Próximos eventos", "Impacto na carteira", "Ação a considerar"]
    posicoes = [fluxo.index(f"{n:02d} · {t}") for n, t in
                enumerate(titulos, start=1)]
    assert posicoes == sorted(posicoes)
    assert "Papel principal: renda imobiliária." in fluxo
    assert "alvo da classe dividido pelo risco" in fluxo
    # nenhuma seção segue em preparação: o cenário virou premissa cadastrável
    assert "em preparação</span>" not in fluxo
    assert "séries macro do banco não puderam ser lidas" in fluxo
    assert "Dado · fornecido pelo sistema" in fluxo
    assert "P/VP" in fluxo and "Dado não disponível." in fluxo
    questoes = next(h for h in htmls if "flex:1 1 220px" in h)
    for pergunta in m.PERGUNTAS.values():
        assert escape(pergunta) in questoes
    # Portfolio Fit: pré-leitura por regras sempre visível; a LLM só roda no
    # botão, e o contexto estruturado (JSON) que ela recebe não vai à tela.
    assert any("Portfolio Fit pelas regras" in h for h in htmls)
    assert not app.json
    assert not any(e.label == "Contexto estruturado que a LLM recebe"
                   for e in app.expander)

    app.selectbox(key="ia_ativo").set_value("TAEE11").run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="ia_ativo").value == "TAEE11"
    assert any("TAEE11" in md.value for md in app.markdown)


def test_liberada_sem_posicoes_nao_quebra():
    app = _rodar(_liberada(), {"posicoes": []})
    assert not app.exception
    assert "Nenhum ativo" in app.info[0].value
    # sem ativos, a estratégia continua alterável
    assert app.expander[-1].label == "✏️ Ver ou alterar minha estratégia"


def test_alterar_estrategia_no_fim_da_aba_liberada(estrategia_falsa):
    from core.estrategia import repositorio as repo
    vigente = _liberada().politica
    estrategia_falsa.estado = repo.Estado(vigente=vigente)
    app = _rodar(_liberada(), CARTEIRA_COMPLETA)
    assert not app.exception
    editar = app.button(key="cfg_estrategia_editar")
    editar.click().run(timeout=30)
    assert not app.exception
    assert estrategia_falsa.iniciados == 1
    app.run(timeout=30)
    assert app.button(key="cfg_estrategia_concluir") is not None


def test_cartoes_da_analise_so_usam_tokens_de_tema():
    from core.inteligencia_ativos import analise, contexto
    ctx = contexto.montar(_liberada().politica, CARTEIRA_COMPLETA)
    a = analise.analisar_carteira(ctx)[0]
    for html in (tela.cartao_premissa(ctx), tela.fluxo_html(a),
                 tela.cartao_questoes(a)):
        assert "#" not in html.replace("&#", "")


def test_cenario_vem_dos_dados_e_nao_e_perguntado():
    """30/09/2026: o usuário pediu para não perguntar o cenário; ele é lido
    das séries do banco. Sem séries (teste), a caixa diz que faltaram."""
    app = _rodar(_liberada(), CARTEIRA_COMPLETA)
    assert not app.exception
    htmls = [md.value for md in app.markdown]
    assert not any("Meu cenário" in h for h in htmls)
    assert not any(e.label == "🌎 Ver ou alterar meu cenário"
                   for e in app.expander)
    assert not [b for b in app.button if "cenario" in (b.key or "")]
    assert any("Cenário econômico atual" in h
               and "não puderam ser lidas" in h for h in htmls)


def test_bloqueada_nao_mostra_o_cenario():
    app = _rodar(_bloqueada())
    assert not app.exception
    assert not any(e.label == "🌎 Ver ou alterar meu cenário"
                   for e in app.expander)


def test_aba_esta_em_investimentos_e_a_estrategia_so_nela():
    fonte = (RAIZ / "views" / "investimentos.py").read_text(encoding="utf-8")
    assert "_ia.render(_liberacao, carteira, proventos)" in fonte
    assert "_ia.rotulo_aba(_liberacao)" in fonte
    # a estratégia saiu de Configurações em 27/09/2026
    for arq in ("configuracoes.py", "configuracoes_geral.py"):
        cfg = (RAIZ / "views" / arq).read_text(encoding="utf-8")
        assert "configuracoes_estrategia" not in cfg
        assert "render_estrategia_bloco" not in cfg
        # o cenário saiu junto, no mesmo dia
        assert "configuracoes_cenario" not in cfg
        assert "render_cenario_bloco" not in cfg
    # renderizada em um lugar só por execução (as chaves dos widgets são fixas)
    arvore = ast.parse((RAIZ / "views" / "inteligencia_ativos.py")
                       .read_text(encoding="utf-8"))
    chamadas = [n for n in ast.walk(arvore) if isinstance(n, ast.Call)
                and ast.unparse(n.func) == "tela_estrategia.render"]
    assert len(chamadas) == 2   # onboarding (bloqueada) e fim (liberada)
    chamadas = [n for n in ast.walk(arvore) if isinstance(n, ast.Call)
                and ast.unparse(n.func) == "tela_cenario.render"]
    assert not chamadas   # 30/09/2026: o cenário é lido dos dados


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


def test_analise_da_carteira_nao_e_refeita_a_cada_clique(monkeypatch):
    """Abrir o detalhe ou trocar o ativo reaproveita a análise (CPU, 30/09)."""
    from core import inteligencia_ativos as servico
    original = servico.analisar_carteira
    chamadas = []

    def contando(**kw):
        chamadas.append(kw["carteira"])
        return original(**kw)

    monkeypatch.setattr(servico, "analisar_carteira", contando)
    app = _rodar(_liberada(), CARTEIRA_COMPLETA)
    app.toggle(key="ia_detalhe").set_value(True).run(timeout=30)
    app.selectbox(key="ia_ativo").set_value("TAEE11").run(timeout=30)
    assert not app.exception
    assert len(chamadas) == 1


def test_chave_da_analise_muda_com_carteira_e_politica():
    base = tela._chave_analise(_liberada(), CARTEIRA_COMPLETA)
    outra_carteira = {**CARTEIRA_COMPLETA, "total_mercado": 1001.0}
    assert tela._chave_analise(_liberada(), outra_carteira) != base
    lib = _liberada()
    from dataclasses import replace
    outra_politica = replace(lib, politica=replace(lib.politica, version=4))
    assert tela._chave_analise(outra_politica, CARTEIRA_COMPLETA) != base
    assert tela._chave_analise(_liberada(), dict(CARTEIRA_COMPLETA)) == base
