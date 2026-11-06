"""Controle CNPJ: as cinco abas e a subaba de upload rodam de verdade (AppTest).

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
                          "_tab_impostos": (lancs, empresa)}[aba]))


@pytest.mark.parametrize("aba", ["_tab_visao_geral", "_tab_lancamentos", "_tab_previstos",
                                 "_tab_dre", "_tab_impostos"])
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
