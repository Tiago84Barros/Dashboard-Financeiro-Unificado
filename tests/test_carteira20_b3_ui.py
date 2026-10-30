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


def _rodar(monkeypatch, regs, hoje="2026-10-25", desempenho=None, lacunas=None,
           medicao=None, estado=None):
    monkeypatch.chdir(RAIZ)
    monkeypatch.setattr(vc, "carregar", lambda: regs)
    monkeypatch.setattr(vc, "carregar_desempenho", lambda: desempenho)
    monkeypatch.setattr(vc, "carregar_medicao", lambda: medicao)
    # o logo real faz HEAD na CDN; aqui basta o marcador
    monkeypatch.setattr(vc, "company_logo_html",
                        lambda tk, url, **kw: f'<span class="logo-teste">{tk}</span>')
    monkeypatch.setattr(vc, "aviso_lacuna", lambda msg, *, codigo, **kw:
                        lacunas.append(codigo) if lacunas is not None else None)
    app = AppTest.from_string(_SCRIPT)
    app.session_state["_hoje"] = hoje
    for chave, valor in (estado or {}).items():
        app.session_state[chave] = valor
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
        "schema": 2, "pregoes": 4, "dados_ate": dados_ate, "datas": datas,
        "revisao": {"seq": atual["registro"]["seq"], "pregao_execucao": "2026-10-26",
                    "hash": hash_ or atual["registro"]["hash"],
                    "corpo_sha256": vc.cd.impressao_corpo(atual["corpo"])},
        "curvas": {"carteira20": [994.01, 1003.0, 1010.0, 1021.5, 1030.2],
                   "carteira20_fracoes": [994.01, 1004.0, 1011.0, 1022.5, 1031.4],
                   "pesos_iguais": [994.01, 999.0, 1004.0, 1009.0, 1012.0],
                   "bova11": [994.01, 996.0, 1001.0, 1003.0, 1005.0],
                   "cdi": [1000.0, 1000.4, 1000.8, 1001.2, 1001.6]},
        "caixa": {"carteira20": 115.24, "bova11": 193.62}, "cotas_bova11": 4,
        "ativos": [{"id": c["id"], "ticker": c["ticker"],
                    "retorno": 0.042 if i == 0 else -0.013, "peso_atual": 0.05,
                    "quantidade": 0 if i == 0 else 3 if i == 1 else 1, "preco_compra": 13.62}
                   for i, c in enumerate(carteira)],
        "avisos": ["CDI sem a taxa de 29/10/2026; as curvas param em 29/10/2026"]}


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
    assert mc.NAV_ITEMS_B3[5] == ("📒", "Carteira 20")
    assert ("📒", "Carteira 20") not in mc.NAV_ITEMS
    b3 = (RAIZ / "views" / "empresas_b3.py").read_text(encoding="utf-8")
    assert "carteira20_b3.render(show_header=False, cadastro=df_set)" in b3
    assert "previsao_lucros_b3" not in b3


def test_trilho_b3_desenha_seis_abas():
    app = AppTest.from_string("""
from design.market_companies import NAV_ITEMS_B3, render_market_tabs
render_market_tabs(state_key='t_active', key_prefix='t', itens=NAV_ITEMS_B3)
""").run(timeout=60)
    assert not app.exception
    rotulos = [b.label for b in app.button]
    assert rotulos[-1] == "📒 Carteira 20"
    assert len(rotulos) == 6
    assert not any("Previsão de Lucros" in r for r in rotulos)


def test_grafico_e_cards_com_desempenho_publicado(monkeypatch, registros_publicados):
    lacunas = []
    app = _rodar(monkeypatch, registros_publicados, hoje="2026-11-02",
                 desempenho=_desempenho(registros_publicados), lacunas=lacunas)
    assert not app.exception
    txt = _texto(app)
    assert "Período curto demais para concluir" in txt
    assert "4 pregão(ões) desde o fechamento de 26/10/2026" in txt
    assert "CDI sem a taxa de 29/10/2026" in txt
    assert "R\\$ 1.000" in txt and "R\\$ 20 mil" in txt  # sem virar LaTeX
    assert lacunas == []
    grafico = app.get("plotly_chart")
    assert len(grafico) == 1
    fig = __import__("json").loads(grafico[0].proto.spec)
    nomes = [t["name"] for t in fig["data"]]
    assert nomes == ["Carteira 20", "Carteira 20 em frações", "Pesos iguais do universo",
                     "BOVA11", "CDI"]
    assert fig["data"][0]["y"][0] == pytest.approx(-0.00599)  # só o custo de compra
    assert fig["data"][0]["y"][-1] == pytest.approx(0.0302)
    cards = _cards_html(app)
    assert "+4,2%" in cards and "c20-pos" in cards and "−" not in cards
    assert "-1,3%" in cards and "c20-neg" in cards
    assert "Peso em 30/10" in cards and "1º pregão" not in cards
    assert "0 ações" in cards and "3 ações" in cards and "1 ação<" in cards
    assert "R$ 13,62" in cards
    assert "ações inteiras pelo fracionário" in txt and "mede a escolha das ações" in txt
    ajudas = " ".join(m.value for m in app.markdown)
    assert "115,24 de troco em caixa" in ajudas and "4 cotas inteiras e" in ajudas
    assert "193,62 de troco em caixa" in ajudas


def test_desempenho_de_antes_dos_lotes_ainda_abre(monkeypatch, registros_publicados):
    """Artefato schema 1: sem quantidade, sem caixa e sem a curva em frações."""
    antigo = _desempenho(registros_publicados)
    antigo["schema"] = 1
    del antigo["caixa"], antigo["cotas_bova11"], antigo["curvas"]["carteira20_fracoes"]
    for a in antigo["ativos"]:
        del a["quantidade"], a["preco_compra"]
    app = _rodar(monkeypatch, registros_publicados, hoje="2026-11-02", desempenho=antigo)
    assert not app.exception
    assert "Ações compradas" not in _cards_html(app)
    fig = __import__("json").loads(app.get("plotly_chart")[0].proto.spec)
    # a curva "carteira20" do schema 1 é a em frações, e o rótulo e a legenda dizem isso
    assert [t["name"] for t in fig["data"]] == [
        "Carteira 20 em frações", "Pesos iguais do universo", "BOVA11", "CDI"]
    txt = _texto(app)
    assert "Publicado antes da compra em ações inteiras" in txt
    assert "ações inteiras pelo fracionário" not in txt
    assert "peso atual é quanto a posição comprada a 5%" in txt


def test_desempenho_de_outra_revisao_e_ignorado(monkeypatch, registros_publicados):
    lacunas = []
    app = _rodar(monkeypatch, registros_publicados, hoje="2026-11-02", lacunas=lacunas,
                 desempenho=_desempenho(registros_publicados, hash_="0" * 64))
    assert not app.exception
    assert not app.get("plotly_chart")
    assert "ainda não foi publicado" in _texto(app)
    assert lacunas == ["tela.b3.carteira20.desempenho_ausente"]


def test_desempenho_de_corpo_substituido_por_correcao_e_ignorado(registros_publicados):
    desempenho = _desempenho(registros_publicados)
    assert vc.desempenho_da_vigente(registros_publicados, desempenho) is desempenho
    # a correção troca o corpo vigente sem mudar o hash da revisão original
    atual = c20.vigente(registros_publicados)
    novo = copy.deepcopy(atual["corpo"])
    novo["pregao_execucao"] = "2026-10-27"
    correcao = {"tipo": "correcao", "seq": len(registros_publicados),
                "corpo": {"referencia": {"seq": atual["registro"]["seq"]}, "substitui": novo}}
    regs = [*registros_publicados, correcao]
    assert c20.vigente(regs)["registro"]["hash"] == atual["registro"]["hash"]
    assert vc.desempenho_da_vigente(regs, desempenho) is None


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


def _medicao(regs, meses=("2026-11-30",), fator=1.004, protocolo=None) -> dict:
    """Artefato no formato de scripts/carteira20_medicao.py."""
    def carteiras(base):
        return {"carteira20": {"liquido": base, "caixa": 42.5, "ir_pago": 0.0, "custos": 6.1},
                "pesos_iguais": {"liquido": base - 20}, "bova11": {"liquido": base - 10},
                "cdi": {"liquido": base - 30}}

    return {
        "schema": 1,
        "protocolo": {"seq": 0, "hash": protocolo or regs[0]["hash"]},
        "revisoes": [{"seq": r["registro"]["seq"], "hash": r["registro"]["hash"],
                      "corpo_sha256": vc.cd.impressao_corpo(r["corpo"])}
                     for r in c20.revisoes(regs)],
        "meses": [{"fechamento": f, "ipca_fator": fator if i == 0 else None,
                   "aportes": {"aportado": 1000.0 + 500 * i,
                               "aportado_real": 1000.0 if i == 0 else None,
                               **carteiras(1050.0 + 500 * i)},
                   "teorica": {"aportado": 1000.0, "aportado_real": 1000.0,
                               **carteiras(1040.0)}}
                  for i, f in enumerate(meses)],
        "posicoes_aportes": [{"id": "X", "ticker": "PETR4", "quantidade": 3.0, "valor": 160.5}],
        "avisos": ["fechamento de 30/12/2026 ainda sem o pregão de PETR4 na fonte"],
    }


def _tabela_medicao(app):
    return next(d.value for d in app.dataframe if "Fechamento" in d.value.columns)


def _da_medicao(lacunas):
    return [c for c in lacunas if ".medicao" in c]


def test_medicao_publicada_vira_tabela_por_fechamento(monkeypatch, registros_publicados):
    lacunas = []
    app = _rodar(monkeypatch, registros_publicados, hoje="2026-12-02", lacunas=lacunas,
                 medicao=_medicao(registros_publicados))
    assert not app.exception
    assert _da_medicao(lacunas) == []
    tabela = _tabela_medicao(app)
    assert list(tabela.columns) == ["Fechamento", "Aportado", "Carteira 20", "Pesos iguais",
                                    "BOVA11", "CDI"]
    assert tabela.iloc[0].tolist() == ["30/11/2026", "R$ 1.000,00", "R$ 1.050,00",
                                       "R$ 1.030,00", "R$ 1.040,00", "R$ 1.020,00"]
    txt = _texto(app)
    assert "a medição ainda não foi publicada" not in txt
    assert "R\\$ 42,50 em caixa" in txt and "R\\$ 500 entram" in txt
    assert "Fechamento de 30/12/2026 ainda sem o pregão de PETR4" in txt


def test_medicao_em_reais_do_inicio_divide_pelo_ipca_e_nao_inventa_mes(registros_publicados):
    med = _medicao(registros_publicados, meses=("2026-11-30", "2026-12-30"))
    t = vc.tabela_medicao(med, "aportes", real=True)
    assert t.iloc[0]["Carteira 20"] == vc._reais(1050.0 / 1.004)
    assert t.iloc[0]["Aportado"] == "R$ 1.000,00"
    # dezembro sem IPCA divulgado: nada de nominal no lugar do real
    assert t.iloc[1]["Carteira 20"] == "—" and t.iloc[1]["Aportado"] == "—"
    nominal = vc.tabela_medicao(med, "aportes", real=False)
    assert nominal.iloc[1]["Aportado"] == "R$ 1.500,00"


def test_medicao_de_outro_registro_e_ignorada(monkeypatch, registros_publicados):
    lacunas = []
    app = _rodar(monkeypatch, registros_publicados, hoje="2026-12-05", lacunas=lacunas,
                 medicao=_medicao(registros_publicados, protocolo="0" * 64))
    assert not app.exception
    assert "a medição ainda não foi publicada" in _texto(app)
    assert _da_medicao(lacunas) == ["tela.b3.carteira20.medicao_pendente"]


def test_medicao_atrasada_nao_finge_estar_em_dia(monkeypatch, registros_publicados):
    lacunas = []
    app = _rodar(monkeypatch, registros_publicados, hoje="2027-01-08", lacunas=lacunas,
                 medicao=_medicao(registros_publicados))
    assert not app.exception
    assert "O fechamento de 30/12/2026 ainda não foi medido" in _texto(app)
    assert _da_medicao(lacunas) == ["tela.b3.carteira20.medicao_atrasada"]


def test_curva_teorica_na_tela(monkeypatch, registros_publicados):
    app = _rodar(monkeypatch, registros_publicados, hoje="2026-12-02",
                 medicao=_medicao(registros_publicados),
                 estado={"c20_medicao_curva": "Teórica"})
    assert not app.exception
    assert _tabela_medicao(app).iloc[0]["Carteira 20"] == "R$ 1.040,00"
    assert "Sem aporte; os proventos voltam para a própria ação" in _texto(app)


def test_aviso_com_dois_cifroes_nao_vira_formula(monkeypatch, registros_publicados):
    des = _desempenho(registros_publicados)
    des["avisos"] = ["fora da compra, uma ação passa do dobro do alvo: CLSC4 (1 ação custa "
                     "R$ 169,87, o alvo é R$ 50,00)"]
    app = _rodar(monkeypatch, registros_publicados, hoje="2026-11-02", desempenho=des)
    assert not app.exception
    aviso = next(c.value for c in app.caption if "CLSC4" in c.value)
    assert "R\\$ 169,87" in aviso and "R\\$ 50,00" in aviso
