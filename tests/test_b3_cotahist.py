"""Leitura do COTAHIST separada do filtro, e o preco unitario.

O defeito que estes testes existem para impedir esta descrito em
`data_pipeline/market/b3_precos.py`: `PREULT` é o preco de `FATCOT` unidades, e
`FATCOT` nao é sempre 1. Gravar o fechamento cru como preco unitario erra por
1000x sem levantar excecao.
"""
import io
import zipfile

import pytest

from data_pipeline.market import b3_cotahist, b3_precos
from data_pipeline.market.fii_b3_history import parse_cotahist


def _campo(linha, inicio, fim, valor):
    linha[inicio:fim] = list(str(valor).ljust(fim - inicio)[:fim - inicio])


def _linha(*, bdi="02", ticker="PETR4", data="20260130", tpmerc="010",
           fechamento="0000000010500", fator="0000001", especificacao="ON"):
    linha = list(" " * 245)
    _campo(linha, 0, 2, "01")
    _campo(linha, 2, 10, data)
    _campo(linha, 10, 12, bdi)
    _campo(linha, 12, 24, ticker)
    _campo(linha, 24, 27, tpmerc)
    _campo(linha, 27, 39, "PETROBRAS")
    _campo(linha, 39, 49, especificacao)
    _campo(linha, 56, 69, "0000000010000")
    _campo(linha, 69, 82, "0000000011000")
    _campo(linha, 82, 95, "0000000009000")
    _campo(linha, 95, 108, "0000000010000")
    _campo(linha, 108, 121, fechamento)
    _campo(linha, 147, 152, "00010")
    _campo(linha, 152, 170, "000000000000001000")
    _campo(linha, 170, 188, "000000000010500000")
    _campo(linha, 210, 217, fator)
    _campo(linha, 230, 242, "BRPETRACNPR6")
    return "".join(linha)


def _zip(*linhas):
    conteudo = io.BytesIO()
    with zipfile.ZipFile(conteudo, "w") as compactado:
        compactado.writestr("COTAHIST_A2026.TXT", "\n".join(linhas))
    return conteudo.getvalue()


def test_filtro_padrao_traz_lote_padrao_e_deixa_fii_de_fora():
    conteudo = _zip(_linha(bdi="02", ticker="PETR4"),
                    _linha(bdi="12", ticker="HGLG11"))
    linhas = b3_cotahist.ler_linhas(conteudo)
    assert [linha["ticker"] for linha in linhas] == ["PETR4"]


def test_bdi_vazio_significa_todos_e_nao_nenhum():
    """A distincao aparece na chamada; recusar tudo em silencio seria o caro."""
    conteudo = _zip(_linha(bdi="02", ticker="PETR4"),
                    _linha(bdi="12", ticker="HGLG11"),
                    _linha(bdi="96", ticker="PETR4F"))
    linhas = b3_cotahist.ler_linhas(conteudo, bdi_codes=())
    assert {linha["ticker"] for linha in linhas} == {"PETR4", "HGLG11", "PETR4F"}


def test_mercado_a_termo_nao_entra_como_preco_do_papel():
    """O fechamento de uma opcao nao é o fechamento da acao."""
    conteudo = _zip(_linha(tpmerc="010", ticker="PETR4"),
                    _linha(tpmerc="030", ticker="PETR4"))
    linhas = b3_cotahist.ler_linhas(conteudo)
    assert len(linhas) == 1


def test_parser_de_fii_devolve_as_mesmas_chaves_de_antes_da_separacao():
    """`market.fii_b3_security_history` nao tem bdi nem fator_cotacao."""
    conteudo = _zip(_linha(bdi="12", ticker="HGLG11"))
    linhas = parse_cotahist(conteudo)
    assert len(linhas) == 1
    assert "bdi" not in linhas[0] and "fator_cotacao" not in linhas[0]
    assert linhas[0]["ticker"] == "HGLG11"
    assert linhas[0]["close"] == 105.0


def test_preco_unitario_desfaz_o_lote_de_mil():
    """1.074 linhas de 2013 tem FATCOT=1000. Sem isso o preco erra 1000x."""
    conteudo = _zip(_linha(fator="0001000", fechamento="0000000010500"))
    [linha] = b3_precos.preparar_linhas(b3_cotahist.ler_linhas(conteudo))
    assert linha["close"] == 105.0
    assert linha["close_unitario"] == 0.105


def test_fator_um_deixa_o_preco_intacto():
    conteudo = _zip(_linha(fator="0000001", fechamento="0000000010500"))
    [linha] = b3_precos.preparar_linhas(b3_cotahist.ler_linhas(conteudo))
    assert linha["close"] == linha["close_unitario"] == 105.0


def test_papel_sem_negocio_no_dia_nao_vira_preco_zero():
    conteudo = _zip(_linha(fechamento="0000000000000"))
    assert b3_precos.preparar_linhas(b3_cotahist.ler_linhas(conteudo)) == []


def test_gravacao_remota_e_recusada():
    """~1 GB nao pode cair no Supabase por engano."""
    class _Url:
        host = "db.abcdefgh.supabase.co"

    class _Engine:
        url = _Url()

    with pytest.raises(b3_precos.DestinoRemotoRecusado):
        b3_precos.exigir_local(_Engine())


def test_armazem_local_e_aceito():
    class _Url:
        host = "localhost"

    class _Engine:
        url = _Url()

    b3_precos.exigir_local(_Engine())  # nao levanta
