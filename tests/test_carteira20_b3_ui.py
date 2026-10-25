import copy
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import core.carteira20 as c20
import views.carteira20_b3 as vc

RAIZ = Path(__file__).parents[1]

_SCRIPT = """
import time
from datetime import date
import streamlit as st
st.session_state.setdefault("_app4_user",
    {"id": "11111111-1111-1111-1111-111111111111", "expires_at": time.time() + 600})
from views import carteira20_b3
carteira20_b3.render(show_header=False, hoje=date.fromisoformat(st.session_state["_hoje"]))
"""


@pytest.fixture
def registros_publicados():
    regs = c20.ler(c20.REGISTRO)
    if not c20.revisoes(regs):
        pytest.skip("registro sem revisão publicada")
    return regs


def _rodar(monkeypatch, regs, hoje="2026-10-25", desempenho=None, lacunas=None):
    monkeypatch.chdir(RAIZ)
    monkeypatch.setattr(vc, "carregar", lambda: regs)
    monkeypatch.setattr(vc, "carregar_desempenho", lambda: desempenho)
    # o logo real faz HEAD na CDN; aqui basta o marcador
    monkeypatch.setattr(vc, "company_logo_html",
                        lambda tk, url, **kw: f'<span class="logo-teste">{tk}</span>')
    monkeypatch.setattr(vc, "aviso_lacuna", lambda msg, *, codigo, **kw:
                        lacunas.append(codigo) if lacunas is not None else None)
    app = AppTest.from_string(_SCRIPT)
    app.session_state["_hoje"] = hoje
    return app.run(timeout=60)


def _texto(app) -> str:
    partes = [m.value for m in app.markdown] + [c.value for c in app.caption]
    return "\n".join(str(p) for p in partes)


def _cards_html(app) -> str:
    return next(str(m.value) for m in app.markdown if 'class="c20-grid"' in str(m.value))


def _desempenho(regs, dados_ate="2026-10-30", hash_=None) -> dict:
    """Artefato no formato do publicador: 4 pregões depois da execução de 26/10."""
    atual = c20.vigente(regs)
    carteira = atual["corpo"]["carteira"]
    datas = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", dados_ate]
    return {
        "schema": 1, "pregoes": 4, "dados_ate": dados_ate, "datas": datas,
        "revisao": {"seq": atual["registro"]["seq"], "pregao_execucao": "2026-10-26",
                    "hash": hash_ or atual["registro"]["hash"]},
        "curvas": {"carteira20": [994.01, 1003.0, 1010.0, 1021.5, 1030.2],
                   "pesos_iguais": [994.01, 999.0, 1004.0, 1009.0, 1012.0],
                   "bova11": [994.01, 996.0, 1001.0, 1003.0, 1005.0],
                   "cdi": [1000.0, 1000.4, 1000.8, 1001.2, 1001.6]},
        "ativos": [{"id": c["id"], "ticker": c["ticker"],
                    "retorno": 0.042 if i == 0 else -0.013, "peso_atual": 0.05}
                   for i, c in enumerate(carteira)],
        "avisos": ["CDI publicado só até 29/10/2026; as curvas param na mesma data"]}


def test_mostra_carteira_vigente_e_cadeia_integra(monkeypatch, registros_publicados):
    app = _rodar(monkeypatch, registros_publicados)
    assert not app.exception
    txt = _texto(app)
    assert "Cadeia íntegra" in txt
    assert "o primeiro fecha em 30/11/2026" in txt
    cards = _cards_html(app)
    corpo = c20.vigente(registros_publicados)["corpo"]
    tickers = [c["ticker"] for c in corpo["carteira"]]
    assert cards.count('class="b3-card"') == c20.N_ACOES
    posicoes = [cards.index(f'b3-card-ticker">{tk}<') for tk in tickers]
    assert posicoes == sorted(posicoes)  # na ordem do registro
    assert "#1<" in cards and f"#{c20.N_ACOES}<" in cards
    assert "5,0%" in cards and "1º pregão" in cards
    # antes da execução: nada de gráfico, e o aviso de período curto já aparece
    assert not app.get("plotly_chart")
    assert "Período curto demais para concluir" in txt
    assert "começa no primeiro pregão seguinte" in txt
    historico = app.dataframe[-1].value
    assert list(historico["Hash"]) == [r["hash"] for r in registros_publicados]


def test_cadeia_adulterada_aparece_quebrada(monkeypatch, registros_publicados):
    regs = copy.deepcopy(registros_publicados)
    regs[1]["corpo"]["carteira"][0]["ticker"] = "XXXX3"
    app = _rodar(monkeypatch, regs)
    assert not app.exception
    assert "Cadeia QUEBRADA" in _texto(app)


def test_data_adulterada_nao_derruba_o_historico(monkeypatch, registros_publicados):
    regs = copy.deepcopy(registros_publicados)
    regs[1]["registrado_em"] = "ontem à noite"
    regs[1]["corpo"]["data_formacao"] = "30/09"
    app = _rodar(monkeypatch, regs)
    assert not app.exception
    assert "Cadeia QUEBRADA" in _texto(app)
    historico = app.dataframe[-1].value
    assert "inválido (ontem à noite)" in list(historico["Registrado em"])
    assert len(historico) == len(regs)


def test_mes_fechado_sem_medicao_nao_finge_estar_em_dia(monkeypatch, registros_publicados):
    app = _rodar(monkeypatch, registros_publicados, hoje="2026-12-05")
    assert not app.exception
    txt = _texto(app)
    assert "a medição ainda não foi publicada" in txt
    assert "Nenhum mês fechado" not in txt


def test_registro_ausente_mostra_estado_vazio(monkeypatch):
    app = _rodar(monkeypatch, None)
    assert not app.exception
    assert "ainda não tem registro publicado" in _texto(app)


def test_proxima_revisao_e_primeiro_fechamento(registros_publicados):
    prox = vc.proxima_revisao(registros_publicados, date(2026, 10, 25))
    assert prox["data_formacao"] == "2027-04-30" and not prox["atrasada"]
    assert vc.primeiro_fechamento(registros_publicados) == date(2026, 11, 30)


def test_revisao_formada_segue_visivel_ate_o_prazo_e_depois_atrasada(registros_publicados):
    # formação 30/04/2027 já passou, mas o registro vale até a abertura de 03/05
    prox = vc.proxima_revisao(registros_publicados, date(2027, 5, 1))
    assert prox["data_formacao"] == "2027-04-30" and not prox["atrasada"]
    # passado o prazo sem registro, não pula para outubro: fica marcada
    prox = vc.proxima_revisao(registros_publicados, date(2027, 5, 10))
    assert prox["data_formacao"] == "2027-04-30" and prox["atrasada"]


def test_card_mostra_revisao_atrasada(monkeypatch, registros_publicados):
    app = _rodar(monkeypatch, registros_publicados, hoje="2027-05-10")
    assert not app.exception
    assert "ATRASADA: o prazo era a abertura de 03/05/2027" in _texto(app)


def test_aba_despachada_na_vitrine_b3():
    import design.market_companies as mc
    assert mc.NAV_ITEMS_B3[6] == ("📒", "Carteira 20")
    assert ("📒", "Carteira 20") not in mc.NAV_ITEMS
    b3 = (RAIZ / "views" / "empresas_b3.py").read_text(encoding="utf-8")
    assert "carteira20_b3.render(show_header=False, cadastro=df_set)" in b3


def test_grafico_e_cards_com_desempenho_publicado(monkeypatch, registros_publicados):
    lacunas = []
    app = _rodar(monkeypatch, registros_publicados, hoje="2026-11-02",
                 desempenho=_desempenho(registros_publicados), lacunas=lacunas)
    assert not app.exception
    txt = _texto(app)
    assert "Período curto demais para concluir" in txt
    assert "4 pregão(ões) desde o fechamento de 26/10/2026" in txt
    assert "CDI publicado só até 29/10/2026" in txt
    assert "R\\$ 1.000" in txt and "R\\$ 20 mil" in txt  # sem virar LaTeX
    assert lacunas == []
    grafico = app.get("plotly_chart")
    assert len(grafico) == 1
    fig = __import__("json").loads(grafico[0].proto.spec)
    nomes = [t["name"] for t in fig["data"]]
    assert nomes == ["Carteira 20", "Pesos iguais do universo", "BOVA11", "CDI"]
    assert fig["data"][0]["y"][0] == pytest.approx(-0.00599)  # só o custo de compra
    assert fig["data"][0]["y"][-1] == pytest.approx(0.0302)
    cards = _cards_html(app)
    assert "+4,2%" in cards and "c20-pos" in cards and "−" not in cards
    assert "-1,3%" in cards and "c20-neg" in cards
    assert "Peso em 30/10" in cards and "1º pregão" not in cards


def test_desempenho_de_outra_revisao_e_ignorado(monkeypatch, registros_publicados):
    lacunas = []
    app = _rodar(monkeypatch, registros_publicados, hoje="2026-11-02", lacunas=lacunas,
                 desempenho=_desempenho(registros_publicados, hash_="0" * 64))
    assert not app.exception
    assert not app.get("plotly_chart")
    assert "ainda não foi publicado" in _texto(app)
    assert lacunas == ["tela.b3.carteira20.desempenho_ausente"]


def test_desempenho_parado_nao_finge_estar_em_dia(monkeypatch, registros_publicados):
    lacunas = []
    app = _rodar(monkeypatch, registros_publicados, hoje="2026-11-20", lacunas=lacunas,
                 desempenho=_desempenho(registros_publicados))
    assert not app.exception
    assert "Dados até 30/10/2026" in _texto(app)
    assert lacunas == ["tela.b3.carteira20.desempenho_parado"]
    assert len(app.get("plotly_chart")) == 1


def test_cards_levam_nome_e_setor_do_cadastro(registros_publicados):
    import pandas as pd
    corpo = c20.vigente(registros_publicados)["corpo"]
    tk = corpo["carteira"][0]["ticker"]
    cadastro = vc.cadastro_por_ticker(pd.DataFrame([
        {"ticker": f"{tk}.SA", "nome_empresa": "Empresa <Teste>", "SETOR": "Energia",
         "SUBSETOR": "Elétricas", "SEGMENTO": "x"}]))
    html = vc.cards_carteira(corpo, cadastro, None)
    assert "Empresa &lt;Teste&gt;" in html
    assert "Energia · Elétricas" in html
    assert "Setor não cadastrado" in html  # as outras 19 não vieram no cadastro


def test_titulos_de_secao_usam_o_token_de_texto():
    tema = (RAIZ / "design" / "tema.py").read_text(encoding="utf-8")
    bloco = tema[tema.index(".app-section-title {"):]
    assert "color: var(--app-text)" in bloco[:bloco.index("}")]
