"""Rótulos da página de detalhe do Fundamentus (lacuna tela.carteira.valuation_sem_media).

A Análise do Portfólio mostrava "Margem líquida: 0 ativo(s)" e "Dív. bruta/Patrim.:
0 ativo(s)" com o Fundamentus no ar: a página de detalhe escreve "Marg. Líquida"
e "Liquidez Corr", e o raspador procurava os cabeçalhos da página de resultado
("Mrg. Líq.", "Liq. Corr."). A Dív. Bruta/Patrim. nem existe no detalhe; só as
parcelas. E o FII era lido de `fii_detalhes.php`, que passou a responder 200 com
um erro fatal de PHP. Os rótulos abaixo são os da página real, lida em 03/11/2026.
"""
import types

import pytest

import core.fundamentus as fundamentus


def _linha(*pares):
    celulas = "".join(
        f'<td class="label"><span class="help tips">?</span><span class="txt">{r}</span></td>'
        f'<td class="data"><span class="txt">{v}</span></td>'
        for r, v in pares)
    return f"<tr>{celulas}</tr>"


_ACAO = ("<html><body><table>"
         + _linha(("Papel", "WEGE3"), ("Cotação", "40,10"))
         + _linha(("P/L", "25,30"), ("LPA", "1,58"))
         + _linha(("Marg. Bruta", "33,1%"), ("PSR", "4,10"))
         + _linha(("Marg. EBIT", "20,4%"), ("Marg. Líquida", "16,2%"))
         + _linha(("Div. Yield", "1,9%"), ("ROE", "31,4%"))
         + _linha(("Liquidez Corr", "1,85"), ("Cres. Rec (5a)", "10,2%"))
         + _linha(("Dív. Bruta", "4.000.000"), ("Dív. Líquida", "-1.000.000"))
         + _linha(("Patrim. Líq", "20.000.000"), ("Giro Ativos", "0,90"))
         + "</table></body></html>").encode("utf-8")

_FII = ("<html><body><table>"
        + _linha(("FII", "HGLG11"), ("Cotação", "149,50"))
        + _linha(("Div. Yield", "8,9%"), ("P/VP", "0,90"))
        + _linha(("Qtd imóveis", "37"), ("Vacância Média", "2,4%"))
        + _linha(("Patrim Líquido", "7.546.020.000"), ("VP/Cota", "165,48"))
        + "</table></body></html>").encode("utf-8")

_ERRO_PHP = b"<b>Fatal error</b>: Uncaught TypeError: mysqli_query()"


@pytest.fixture
def paginas(monkeypatch):
    pedidos = []

    def get(url, **_kw):
        pedidos.append(url)
        if "fii_detalhes.php" in url:
            return types.SimpleNamespace(status_code=200, content=_ERRO_PHP)
        corpo = _FII if url.endswith("HGLG11") else _ACAO
        return types.SimpleNamespace(status_code=200, content=corpo)

    monkeypatch.setattr(fundamentus.requests, "get", get)
    fundamentus._scrape_stock.clear()
    fundamentus._scrape_fii.clear()
    return pedidos


def test_acao_le_margens_e_liquidez_pelos_rotulos_do_detalhe(paginas):
    dados = fundamentus._scrape_stock("WEGE3")

    assert dados["marg_liq"] == pytest.approx(16.2)
    assert dados["marg_bruta"] == pytest.approx(33.1)
    assert dados["marg_ebit"] == pytest.approx(20.4)
    assert dados["liq_corr"] == pytest.approx(1.85)
    # Sem a razão na página, ela sai das parcelas que a página traz.
    assert dados["div_brut_patrim"] == pytest.approx(0.2)


def test_div_bruta_patrim_sem_patrimonio_positivo_nao_vira_zero():
    assert fundamentus._razao(4.0, 0.0) is None
    assert fundamentus._razao(4.0, -2.0) is None
    assert fundamentus._razao(None, 2.0) is None
    assert fundamentus._razao(0.0, 2.0) == 0.0


def test_fii_e_lido_da_pagina_que_responde(paginas):
    dados = fundamentus._scrape_fii("HGLG11")

    assert not any("fii_detalhes.php" in u for u in paginas)
    assert dados["vacancia_media"] == pytest.approx(2.4)
    assert dados["dy"] == pytest.approx(8.9)
    assert dados["pvp"] == pytest.approx(0.90)
    assert dados["qtd_imoveis"] == pytest.approx(37)
    # O Fundamentus não publica vacância financeira: o campo fica vazio, não
    # emprestado da média.
    assert dados["vacancia_financ"] is None
