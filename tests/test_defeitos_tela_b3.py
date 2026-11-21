"""Defeitos de tela da Criação de Portfólio B3 vistos em 20/11/2026.

* ``_fv`` escrevia "R$ 801.0K": ponto decimal e sufixo em inglês.
* O cartão do log em Configurações -> Restrições é HTML cru; o ``**BBSE3**``
  saía com os asteriscos à mostra.
"""
from views.configuracoes_restricoes import mensagem_html
from views.empresas_b3 import _fv


def test_fv_usa_padrao_brasileiro():
    assert _fv(801_000) == "R$ 801,0 mil"
    assert _fv(2_500_000) == "R$ 2,50 mi"
    assert _fv(3_200_000_000) == "R$ 3,20 bi"
    assert _fv(-1_500_000) == "R$ -1,50 mi"
    assert "K" not in _fv(801_000) and "M" not in _fv(2_500_000)


def test_mensagem_html_troca_negrito_e_escapa():
    assert mensagem_html("Saiu **BBSE3** da carteira") == (
        "Saiu <strong>BBSE3</strong> da carteira")
    # HTML vindo do log é escapado antes de virar negrito.
    assert mensagem_html("**<b>x</b>**") == "<strong>&lt;b&gt;x&lt;/b&gt;</strong>"
    assert mensagem_html("sem marca") == "sem marca"
