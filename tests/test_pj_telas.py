"""Controle CNPJ: as seis abas e a subaba de upload rodam de verdade (AppTest).

O banco é trocado por lançamentos sintéticos: o que se testa é a tela montar
cada aba sem exceção e mostrar os números que os relatórios calculam.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


def _app(aba: str):
    from datetime import date, timedelta

    import views.controle_cnpj as tela

    hoje = date.today()
    ini = hoje.replace(day=1)
    lancs = [
        {"id": "1", "data": ini, "descricao": "Cliente Alfa", "valor": 10000.0,
         "categoria": "receita_servicos", "status": "pago", "origem": "extrato",
         "contraparte": "CLIENTE ALFA", "chave": "k1", "parcela": 1, "total_parcelas": 1},
        {"id": "2", "data": ini, "descricao": "Pix sem nome", "valor": -150.0,
         "categoria": None, "status": "pago", "origem": "extrato",
         "contraparte": "X", "chave": "k2", "parcela": 1, "total_parcelas": 1},
        {"id": "3", "data": hoje + timedelta(days=10), "descricao": "Aluguel", "valor": -2000.0,
         "categoria": "aluguel", "status": "previsto", "origem": "manual",
         "contraparte": None, "chave": None, "parcela": 1, "total_parcelas": 12},
    ]
    empresa = {"cnpj": "12345678000190", "razao_social": "Empresa Teste", "anexo_simples": "III",
               "saldo_inicial": 1000.0, "data_saldo_inicial": ini - timedelta(days=1)}
    tela.repo.obter_empresa = lambda: empresa
    tela.repo.listar_lancamentos = lambda: lancs
    tela.repo.listar_saldos_extrato = lambda: [{"data": ini, "saldo": 10850.0, "arquivo": "x.ofx"}]
    getattr(tela, aba)(*({"_tab_visao_geral": (lancs, empresa), "_tab_lancamentos": (lancs,),
                          "_tab_previstos": (lancs, empresa), "_tab_dre": (lancs,),
                          "_tab_balanco": (lancs, empresa),
                          "_tab_impostos": (lancs, empresa)}[aba]))


@pytest.mark.parametrize("aba", ["_tab_visao_geral", "_tab_lancamentos", "_tab_previstos",
                                 "_tab_dre", "_tab_balanco", "_tab_impostos"])
def test_cada_aba_monta_sem_excecao(aba):
    at = AppTest.from_function(_app, args=(aba,)).run(timeout=30)
    assert not at.exception, at.exception


def test_visao_geral_confere_o_saldo_com_o_banco():
    at = AppTest.from_function(_app, args=("_tab_visao_geral",)).run(timeout=30)
    # 1000 de abertura + 10000 − 150 = 10850, igual ao extrato
    assert any("fecha com o do banco" in s.value for s in at.success)


def test_dre_avisa_o_que_ficou_sem_categoria():
    at = AppTest.from_function(_app, args=("_tab_dre",)).run(timeout=30)
    assert any("sem categoria" in w.value for w in at.warning)


def _app_upload():
    import views.cnpj_upload as up

    up.repo.preparar_importacao = lambda movs: {"ok": True, "ja_importadas": set(),
                                                "regras": {}, "vinculos": {}}
    up.repo.obter_empresa = lambda: None
    up.render_upload_extrato_cnpj()


def test_upload_sem_arquivo_orienta():
    at = AppTest.from_function(_app_upload).run(timeout=30)
    assert not at.exception
    assert any("prévia" in c.value for c in at.caption)


def test_lancamento_ja_classificado_pode_ter_a_categoria_corrigida():
    """Achado do Codex no PR #680: só os sem categoria tinham editor, e um
    aporte aceito como receita ficava preso na DRE e no DAS."""
    at = AppTest.from_function(_app, args=("_tab_lancamentos",)).run(timeout=30)
    assert any("Corrigir categoria" in e.label for e in at.expander)
    rotulos = at.selectbox(key="cnpj_corrigir_sel").options
    assert any("Cliente Alfa" in r for r in rotulos)


def _app_conferencia(saldo_banco: float):
    from datetime import date
    from types import SimpleNamespace

    import views.cnpj_upload as up

    # Troca só o acesso ao banco e devolve o original no fim: AppTest roda no
    # mesmo processo, e um módulo trocado vaza para os testes seguintes.
    originais = {n: getattr(up.repo, n)
                 for n in ("obter_empresa", "listar_lancamentos", "saldo_calculado")}
    up.repo.obter_empresa = lambda: {"saldo_inicial": 1000.0}
    up.repo.listar_lancamentos = lambda: []
    up.repo.saldo_calculado = lambda empresa, lancs, data: 1771.44
    try:
        extrato = SimpleNamespace(saldo_final={"valor": saldo_banco, "data": date(2026, 11, 7)})
        up._conferencia_de_saldo(extrato, [], {}, set())
    finally:
        for n, f in originais.items():
            setattr(up.repo, n, f)


def test_conferencia_de_saldo_em_cards_css():
    """Os três números saem no card CSS do app, não em st.metric."""
    at = AppTest.from_function(_app_conferencia, args=(1771.44,)).run(timeout=30)
    assert not at.exception
    assert not at.metric
    cards = "".join(m.value for m in at.markdown if "app-kpi-card" in m.value)
    for trecho in ("Saldo do banco", "Saldo do app após importar", "Diferença",
                   "1.771,44", "07/11/2026"):
        assert trecho in cards
    assert any("fecha com o do banco" in s.value for s in at.success)


def test_conferencia_com_diferenca_pinta_o_card_de_vermelho():
    at = AppTest.from_function(_app_conferencia, args=(1800.0,)).run(timeout=30)
    assert not at.exception
    card_dif = [m.value for m in at.markdown if "Diferença" in m.value][0]
    assert "danger" in card_dif
    assert at.warning


def _app_lancamentos_dois_meses():
    from datetime import date

    import views.controle_cnpj as tela

    hoje = date.today()
    ini = hoje.replace(day=1)
    anterior = (ini.replace(day=1) - __import__("datetime").timedelta(days=1)).replace(day=5)
    base = {"status": "pago", "origem": "extrato", "contraparte": None, "parcela": 1,
            "total_parcelas": 1}
    lancs = [
        {**base, "id": "1", "data": ini, "descricao": "Pagamento recebido - Andressa França",
         "valor": 300.0, "categoria": "receita_servicos", "chave": "k1"},
        {**base, "id": "2", "data": ini, "descricao": "Transf Pix enviada - Luana Bezerra",
         "valor": -70.0, "categoria": "receita_servicos", "chave": "k2"},
        {**base, "id": "3", "data": anterior, "descricao": "Pagamento recebido - Luana Bezerra",
         "valor": 500.0, "categoria": "receita_servicos", "chave": "k3"},
    ]
    tela._tab_lancamentos(lancs)


def _tabela_html(at) -> str:
    return "".join(m.value for m in at.markdown if "<table" in m.value)


def test_lancamentos_abre_no_mes_atual_e_tem_todos_os_meses():
    at = AppTest.from_function(_app_lancamentos_dois_meses).run(timeout=30)
    assert not at.exception
    sel = at.selectbox(key="cnpj_lanc_mes")
    assert sel.options[0] == "Todos os meses"
    assert "Andressa" in _tabela_html(at) and "R$ 500" not in _tabela_html(at)
    sel.select_index(0).run()
    assert "Andressa" in _tabela_html(at) and "500,00" in _tabela_html(at)


def test_busca_e_categoria_filtram_a_lista_e_a_correcao():
    at = AppTest.from_function(_app_lancamentos_dois_meses).run(timeout=30)
    at.selectbox(key="cnpj_lanc_mes").select_index(0).run()
    at.text_input(key="cnpj_lanc_busca").input("luana").run()
    tabela = _tabela_html(at)
    assert "Luana" in tabela and "Andressa" not in tabela
    assert any("2 de 3 lançamento(s)" in c.value for c in at.caption)
    # a correção de categoria só oferece o que passou no filtro
    assert len(at.selectbox(key="cnpj_corrigir_sel").options) == 2
    at.multiselect(key="cnpj_lanc_filtro_cat").select("Sem categoria").run()
    assert not _tabela_html(at)
    assert not at.exception


def test_balanco_fecha_e_mostra_os_tres_totais():
    at = AppTest.from_function(_app, args=("_tab_balanco",)).run(timeout=30)
    assert not at.exception
    cards = "".join(m.value for m in at.markdown if "app-kpi-card" in m.value)
    for trecho in ("Ativo", "Passivo", "Patrimônio líquido"):
        assert trecho in cards
    # caixa 10.850 + aluguel previsto de 2.000 a pagar
    assert "10.850,00" in cards and "2.000,00" in cards
    assert "8.850,00" in cards  # PL = 10.850 − 2.000
    assert any("gerencial" in c.value for c in at.caption)


def test_balanco_sem_saldo_de_abertura_nao_inventa_numero():
    def app():
        import views.controle_cnpj as tela
        tela._tab_balanco([], {"saldo_inicial": None})

    at = AppTest.from_function(app).run(timeout=30)
    assert not at.exception
    assert not [m for m in at.markdown if "app-kpi-card" in m.value]
