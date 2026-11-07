"""Controle CNPJ: leitura de OFX, plano de contas, Simples e relatórios.

Dados sintéticos (AGENTS.md): nenhum extrato real entra nos testes.
"""
from __future__ import annotations

from datetime import date

import pytest

from core.pj import relatorios as rel
from core.pj.extratos import ler_extrato
from core.pj.ofx import ler_data, ler_ofx, ler_valor
from core.pj.plano_contas import CATEGORIAS, POR_CODIGO, sugerir_categoria
from core.pj.simples import aliquota_efetiva, estimar_das, rbt12_de

OFX_SGML = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
ENCODING:USASCII
CHARSET:1252

<OFX>
<SIGNONMSGSRSV1><SONRS><FI><ORG>Banco Teste<FID>999</FI></SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<CURDEF>BRL
<BANKACCTFROM><BANKID>999<ACCTID>12345-6<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261103120000[-3:BRT]<TRNAMT>5000.00<FITID>A1<NAME>CLIENTE ALFA LTDA<MEMO>Pix recebido</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261105<TRNAMT>-312,40<FITID>A2<NAME>SIMPLES NACIONAL<MEMO>DAS</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261105<TRNAMT>-312,40<FITID>A3<NAME>FORNECEDOR BETA</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>xx<TRNAMT>-1</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>9375.20<DTASOF>20261106</LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""

OFX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS>
<BANKACCTFROM><BANKID>403</BANKID><ACCTID>777</ACCTID></BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20261102</DTPOSTED><TRNAMT>-49.90</TRNAMT>
<FITID>X9</FITID><NAME>Assinatura Ferramenta</NAME></STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>1000.00</BALAMT><DTASOF>20261102</DTASOF></LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>""".encode()


# ── OFX ─────────────────────────────────────────────────────────────────────

def test_ofx_sgml_le_transacoes_saldo_e_conta():
    e = ler_ofx(OFX_SGML)
    assert e.banco == "Banco Teste" and e.conta == "12345-6"
    assert [m["valor"] for m in e.movimentos] == [5000.0, -312.40, -312.40]
    assert e.movimentos[0]["data"] == date(2026, 11, 3)
    assert e.saldo_final == {"data": date(2026, 11, 6), "valor": 9375.20}
    assert any("ignoradas" in a for a in e.avisos)


def test_dois_pagamentos_iguais_no_mesmo_dia_continuam_dois():
    e = ler_ofx(OFX_SGML)
    assert e.movimentos[1]["chave"] != e.movimentos[2]["chave"]


def test_ofx_xml_tambem_e_lido():
    e = ler_extrato(OFX_XML, "extrato.ofx")
    assert e.formato == "OFX" and e.conta == "777"
    assert e.movimentos[0]["valor"] == -49.90


def test_arquivo_desconhecido_nao_inventa_movimento():
    e = ler_extrato(b"data;valor\n01/11;10", "x.csv")
    assert e.movimentos == [] and "não reconhecido" in e.avisos[0]


@pytest.mark.parametrize(("texto", "valor"), [
    ("-1.234,56", -1234.56), ("-1234.56", -1234.56), ("1234,5", 1234.5), ("1,234.56", 1234.56)])
def test_valor_com_virgula_ou_ponto(texto, valor):
    assert ler_valor(texto) == valor


def test_data_com_fuso():
    assert ler_data("20261231235959[-3:BRT]") == date(2026, 12, 31)


# ── plano de contas ─────────────────────────────────────────────────────────

def test_codigos_unicos():
    assert len(POR_CODIGO) == len(CATEGORIAS)


def test_regras_padrao_respeitam_o_sentido():
    assert sugerir_categoria({"descricao": "SIMPLES NACIONAL", "valor": -300}) == "das"
    assert sugerir_categoria({"descricao": "Rendimento da conta", "valor": 2.5}) == "receitas_financeiras"
    # "juros" recebido não é despesa financeira
    assert sugerir_categoria({"descricao": "juros", "valor": 10}) is None
    # crédito recebido sugere receita (a prévia mostra; o usuário troca se for aporte)
    assert sugerir_categoria({"descricao": "Pix recebido", "valor": 10}) == "receita_servicos"
    assert sugerir_categoria({"descricao": "Pix recebido", "valor": -10}) is None
    assert sugerir_categoria({"descricao": "Deposito", "valor": 10}) is None


def test_regra_aprendida_vence_a_padrao():
    mov = {"descricao": "Tarifa", "contraparte": "Cliente Alfa", "valor": -5}
    assert sugerir_categoria(mov, {"cliente alfa": "custo_servicos"}) == "custo_servicos"


# ── Simples ─────────────────────────────────────────────────────────────────

def test_aliquota_efetiva_anexo_iii_segunda_faixa():
    faixa, nominal, efetiva = aliquota_efetiva("III", 300_000)
    assert (faixa, nominal) == (2, 0.112)
    assert efetiva == pytest.approx(0.0808)


def test_acima_do_teto_nao_calcula():
    assert aliquota_efetiva("I", 5_000_000) == (None, None, None)


def test_fator_r_leva_anexo_v_para_o_iii():
    receitas = [25_000.0] * 12  # RBT12 = 300 mil
    alto = estimar_das("V", receitas, 30_000.0, folhas_12m_anteriores=[7_500.0] * 12)  # 30%
    baixo = estimar_das("V", receitas, 30_000.0, folhas_12m_anteriores=[5_000.0] * 12)  # 20%
    assert alto.anexo_aplicado == "III" and baixo.anexo_aplicado == "V"
    assert alto.das_estimado == pytest.approx(30_000 * 0.0808, abs=0.01)


def test_rbt12_proporcional_no_inicio():
    assert rbt12_de([10_000.0, 20_000.0]) == (180_000.0, True)
    assert rbt12_de([1.0] * 14) == (12.0, False)


# ── relatórios ──────────────────────────────────────────────────────────────

def _l(dia, valor, cat, status="pago", mes=11):
    return {"data": date(2026, mes, dia), "valor": valor, "categoria": cat, "status": status}


LANCS = [
    _l(3, 10_000.0, "receita_servicos"),
    _l(5, -600.0, "das"),
    _l(6, -2_000.0, "pro_labore"),
    _l(7, -300.0, "software"),
    _l(8, 12.0, "receitas_financeiras"),
    _l(9, -5_000.0, "distribuicao_lucros"),
    _l(10, -150.0, None),
    _l(20, -999.0, "aluguel", status="previsto"),
]


def test_dre_separa_fora_do_resultado_e_sem_categoria():
    d = rel.dre(LANCS, [(2026, 11)])
    assert d["receita_liquida"] == 9_400.0
    assert d["resultado_operacional"] == 7_100.0
    assert d["resultado"] == 7_112.0
    assert d["sem_categoria_n"] == 1 and d["sem_categoria_total"] == -150.0
    assert d["grupos"]["fora_do_resultado"] == -5_000.0


def test_serie_mensal_traz_folha_e_das_pagos():
    s = rel.serie_mensal(LANCS, date(2026, 11, 30), meses=2)
    assert [(x["ano"], x["mes"]) for x in s] == [(2026, 10), (2026, 11)]
    assert s[1]["folha"] == 2_000.0 and s[1]["das_pago"] == 600.0
    assert s[1]["receita"] == 10_000.0


def test_saldo_inicial_deduzido_do_primeiro_extrato():
    movs = [{"data": date(2026, 11, 3), "valor": 5000.0},
            {"data": date(2026, 11, 5), "valor": -312.4}]
    ini, data = rel.saldo_inicial_pelo_extrato(9375.2, movs, date(2026, 11, 6))
    assert ini == 4687.6 and data == date(2026, 11, 2)
    assert rel.saldo_ate(ini, data, [dict(m, status="pago") for m in movs], date(2026, 11, 6)) == 9375.2


def test_fator_r_no_inicio_compara_folha_e_receita_do_mesmo_periodo():
    """Achado do Codex no PR #680: seis meses com R$ 10 mil de receita e R$ 3 mil
    de folha são Fator R de 30%, não 15% (receita anualizada, folha não)."""
    est = estimar_das("V", [10_000.0] * 6, 10_000.0, folhas_12m_anteriores=[3_000.0] * 6)
    assert est.rbt12_proporcional and est.rbt12 == 120_000.0
    assert est.fator_r == 0.30
    assert est.anexo_aplicado == "III"


def test_fator_r_logo_abaixo_de_28_por_cento_nao_muda_de_anexo():
    """Achado do Codex no PR #680: 27,999% arredondado virava 28% e levava ao III."""
    est = estimar_das("V", [100_000.0] * 12, 100_000.0, folhas_12m_anteriores=[27_999.0] * 12)
    assert est.anexo_aplicado == "V"
    assert est.fator_r == pytest.approx(0.27999)


def test_mes_sem_receita_de_empresa_antiga_conta_como_zero_na_rbt12():
    """Achado do Codex no PR #680: seis meses zerados seguidos de seis com R$ 20 mil
    numa empresa que já existia dão RBT12 de 120 mil, não 240 mil."""
    lancs = [_l(1, -10.0, "tarifas", mes=1) | {"data": date(2025, 1, 1)}]
    lancs += [{"data": date(2026, m, 5), "valor": 20_000.0, "categoria": "receita_servicos",
               "status": "pago"} for m in range(5, 11)]
    serie = rel.serie_mensal(lancs, date(2026, 11, 1), meses=13)[:-1]
    hist = rel.meses_desde_o_inicio(serie, lancs)
    assert len(hist) == 12
    est = estimar_das("III", [s["receita"] for s in hist], 20_000.0)
    assert est.rbt12 == 120_000.0 and not est.rbt12_proporcional
    assert est.das_estimado == pytest.approx(1_200.0)


def test_inicio_de_atividade_dentro_da_janela_continua_proporcional():
    lancs = [{"data": date(2026, m, 5), "valor": 10_000.0, "categoria": "receita_servicos",
              "status": "pago"} for m in range(8, 11)]
    serie = rel.serie_mensal(lancs, date(2026, 11, 1), meses=13)[:-1]
    hist = rel.meses_desde_o_inicio(serie, lancs)
    assert [(s["ano"], s["mes"]) for s in hist] == [(2026, 8), (2026, 9), (2026, 10)]


def test_saldo_antes_da_abertura_e_calculado_para_tras():
    """Achado do Codex no PR #680: abertura de 1.000 em 02/11 e crédito de 200 em
    15/10 importado depois dão 800 em 14/10, não 1.000."""
    lancs = [{"data": date(2026, 10, 15), "valor": 200.0, "status": "pago"},
             {"data": date(2026, 11, 5), "valor": -50.0, "status": "pago"}]
    ancora = date(2026, 11, 2)
    assert rel.saldo_ate(1000.0, ancora, lancs, date(2026, 10, 14)) == 800.0
    assert rel.saldo_ate(1000.0, ancora, lancs, date(2026, 10, 15)) == 1000.0
    assert rel.saldo_ate(1000.0, ancora, lancs, ancora) == 1000.0
    assert rel.saldo_ate(1000.0, ancora, lancs, date(2026, 11, 5)) == 950.0


def test_ofx_com_duas_contas_e_recusado():
    """Achado do Codex no PR #680: duas contas no mesmo arquivo misturavam
    movimentos contra o saldo da primeira."""
    duas = OFX_SGML.replace(
        b"</STMTRS>",
        b"</STMTRS><STMTRS><BANKACCTFROM><BANKID>1<ACCTID>999-9</BANKACCTFROM>"
        b"<BANKTRANLIST><STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261105<TRNAMT>-1<FITID>A1"
        b"</STMTTRN></BANKTRANLIST></STMTRS>", 1)
    e = ler_ofx(duas)
    assert e.movimentos == [] and "2 contas" in e.avisos[0]


def test_previa_confere_extrato_antigo_com_a_mesma_conta_do_saldo_gravado():
    """Achado do Codex no PR #680: abertura 1.000 em 02/11; extrato antigo com
    crédito de 200 em 15/10 e saldo de 800 em 14/10 fecha na prévia."""
    existentes = [{"id": "p1", "data": date(2026, 11, 20), "valor": -100.0, "status": "previsto"}]
    novos = [{"data": date(2026, 10, 15), "valor": 200.0}]
    hip = rel.lancamentos_apos_importacao(existentes, novos, {})
    assert rel.saldo_ate(1000.0, date(2026, 11, 2), hip, date(2026, 10, 14)) == 800.0


def test_previa_trata_previsto_conciliado_como_pago_na_data_do_banco():
    existentes = [{"id": "p1", "data": date(2026, 11, 20), "valor": -100.0, "status": "previsto"}]
    novos = [{"data": date(2026, 11, 18), "valor": -100.0}]
    hip = rel.lancamentos_apos_importacao(existentes, novos, {0: "p1"})
    assert hip == [{"id": "p1", "data": date(2026, 11, 18), "valor": -100.0, "status": "pago"}]
    assert rel.saldo_ate(1000.0, date(2026, 11, 2), hip, date(2026, 11, 18)) == 900.0


# ── Lançamentos: filtro por categoria e busca por descrição ─────────────────

def _lancs_filtro():
    from datetime import date
    return [
        {"id": "1", "data": date(2026, 11, 6), "descricao": "Pagamento recebido - Andressa França",
         "valor": 300.0, "categoria": "receita_servicos"},
        {"id": "2", "data": date(2026, 11, 4), "descricao": "Transf Pix enviada - Luana Bezerra",
         "valor": -70.0, "categoria": "receita_servicos"},
        {"id": "3", "data": date(2026, 11, 3), "descricao": "Pagamento da fatura - Cora SCFI",
         "valor": -64.99, "categoria": "outras_despesas"},
        {"id": "4", "data": date(2026, 10, 3), "descricao": "Pix sem nome",
         "valor": -10.0, "categoria": None},
    ]


def test_filtro_sem_criterio_devolve_tudo():
    from core.pj.relatorios import filtrar_lancamentos
    assert len(filtrar_lancamentos(_lancs_filtro())) == 4
    assert len(filtrar_lancamentos(_lancs_filtro(), [], "   ")) == 4


def test_busca_ignora_acento_caixa_e_ordem_das_palavras():
    from core.pj.relatorios import filtrar_lancamentos
    assert [x["id"] for x in filtrar_lancamentos(_lancs_filtro(), busca="FRANCA")] == ["1"]
    assert [x["id"] for x in filtrar_lancamentos(_lancs_filtro(), busca="luana pix")] == ["2"]
    assert filtrar_lancamentos(_lancs_filtro(), busca="luana fatura") == []


def test_filtro_de_categoria_aceita_sem_categoria_e_combina_com_a_busca():
    from core.pj.relatorios import filtrar_lancamentos

    def ids(r):
        return [x["id"] for x in r]

    assert ids(filtrar_lancamentos(_lancs_filtro(), [None])) == ["4"]
    assert ids(filtrar_lancamentos(_lancs_filtro(), ["receita_servicos", None])) == ["1", "2", "4"]
    assert ids(filtrar_lancamentos(_lancs_filtro(), ["receita_servicos"], "pagamento")) == ["1"]


# ── Devolução a cliente: dedução da receita, nunca receita negativa ─────────

def test_receita_aprendida_numa_saida_vira_devolucao():
    from core.pj.plano_contas import sugerir_categoria
    regras = {"luana bezerra": "receita_servicos", "loja x": "receita_vendas"}
    saida = {"valor": -70.0, "descricao": "Transf Pix enviada - Luana Bezerra",
             "contraparte": "Luana Bezerra"}
    assert sugerir_categoria(saida, regras) == "devolucoes"
    assert sugerir_categoria({**saida, "contraparte": "Loja X"}, regras) == "devolucoes"
    # a mesma pessoa pagando continua sendo receita
    assert sugerir_categoria({**saida, "valor": 300.0}, regras) == "receita_servicos"


def test_devolucao_aprendida_numa_entrada_cai_na_regra_padrao():
    from core.pj.plano_contas import sugerir_categoria
    regras = {"luana bezerra": "devolucoes"}
    entrada = {"valor": 300.0, "descricao": "Pix recebido - Luana Bezerra",
               "contraparte": "Luana Bezerra"}
    assert sugerir_categoria(entrada, regras) == "receita_servicos"
    assert sugerir_categoria({**entrada, "valor": -30.0}, regras) == "devolucoes"


def test_sentido_so_mexe_em_receita_e_devolucao():
    from core.pj.plano_contas import categoria_no_sentido
    assert categoria_no_sentido("aluguel", "saida") == "aluguel"
    assert categoria_no_sentido("aluguel", "entrada") == "aluguel"
    assert categoria_no_sentido("transferencia", "saida") == "transferencia"
    assert categoria_no_sentido("receitas_financeiras", "saida") == "receitas_financeiras"
    assert categoria_no_sentido("inexistente", "saida") is None
    assert categoria_no_sentido(None, "entrada") is None


def _lancs_devolucao(cat_devolucao: str):
    from datetime import date
    return [
        {"data": date(2026, 11, 3), "valor": 1000.0, "categoria": "receita_servicos",
         "status": "pago"},
        {"data": date(2026, 11, 4), "valor": -70.0, "categoria": cat_devolucao,
         "status": "pago"},
    ]


def test_devolucao_entra_em_deducoes_na_dre():
    from core.pj.relatorios import dre
    d = dre(_lancs_devolucao("devolucoes"), [(2026, 11)])
    assert d["grupos"]["receita_bruta"] == 1000.0
    assert d["grupos"]["deducoes"] == -70.0
    assert d["receita_liquida"] == 930.0


def test_base_do_simples_desconta_devolucao_igual_a_receita_negativa():
    """Equivalência dos dois caminhos: a devolução em Deduções dá a mesma base
    do DAS que o lançamento antigo, gravado como receita negativa."""
    from datetime import date

    from core.pj.relatorios import serie_mensal
    from core.pj.simples import estimar_das
    novo = serie_mensal(_lancs_devolucao("devolucoes"), date(2026, 11, 1), meses=1)[0]
    antigo = serie_mensal(_lancs_devolucao("receita_servicos"), date(2026, 11, 1), meses=1)[0]
    assert novo["receita_simples"] == antigo["receita_simples"] == 930.0
    assert novo["receita"] == 1000.0  # a receita bruta da DRE não muda
    assert (estimar_das("III", [930.0] * 12, novo["receita_simples"]).das_estimado
            == estimar_das("III", [930.0] * 12, antigo["receita_simples"]).das_estimado)


# ── Balanço patrimonial gerencial ───────────────────────────────────────────

def _lancs_balanco():
    from datetime import date
    def pago(dia, valor, cat):
        return {"data": date(2026, 10, dia), "valor": valor, "categoria": cat, "status": "pago"}
    return [
        pago(1, 5000.0, "aporte_socio"),
        pago(2, 3000.0, "receita_servicos"),
        pago(3, -400.0, "aluguel"),
        pago(4, 10000.0, "emprestimo"),
        pago(5, -1500.0, "emprestimo"),
        pago(6, -2000.0, "aplicacao"),
        pago(7, 500.0, "aplicacao"),
        pago(8, -1000.0, "distribuicao_lucros"),
        pago(9, -300.0, "transferencia"),
        pago(10, -50.0, None),
        {"data": date(2026, 12, 5), "valor": 1200.0, "categoria": "receita_servicos",
         "status": "previsto"},
        {"data": date(2026, 12, 10), "valor": -700.0, "categoria": "aluguel",
         "status": "previsto"},
        # parcela prevista de empréstimo: a dívida já está em Empréstimos
        {"data": date(2026, 12, 15), "valor": -1500.0, "categoria": "emprestimo",
         "status": "previsto"},
    ]


def _linha(b, lado, nome):
    return dict(b[lado])[nome]


def test_balanco_fecha_e_poe_cada_categoria_no_seu_lado():
    from datetime import date

    from core.pj.relatorios import balanco
    b = balanco(_lancs_balanco(), 1000.0, date(2026, 9, 30), date(2026, 11, 7))
    assert b["fecha"]
    # caixa = 1000 + 5000 + 3000 − 400 + 10000 − 1500 − 2000 + 500 − 1000 − 300 − 50
    assert _linha(b, "ativo", "Caixa e bancos") == 14250.0
    assert _linha(b, "ativo", "Aplicações financeiras") == 1500.0
    assert _linha(b, "ativo", "Outras contas da empresa (transferências)") == 300.0
    assert _linha(b, "ativo", "Contas a receber") == 1200.0
    assert _linha(b, "passivo", "Empréstimos e financiamentos") == 8500.0
    assert _linha(b, "passivo", "Contas a pagar") == 700.0  # sem a parcela do empréstimo
    pl = dict(b["patrimonio_liquido"])
    assert pl["Saldo anterior aos lançamentos registrados"] == 1000.0
    assert pl["Aportes de sócios"] == 5000.0
    assert pl["Distribuição de lucros"] == -1000.0
    assert pl["Resultado acumulado (DRE, caixa)"] == 2600.0
    assert pl["Contas em aberto (a receber − a pagar)"] == 500.0
    assert pl["Movimentos sem categoria"] == -50.0
    assert b["total_ativo"] == 17250.0
    assert b["total_passivo"] == 9200.0
    assert b["total_pl"] == 8050.0


def test_balanco_em_data_passada_nao_tem_contas_em_aberto_e_fecha():
    from datetime import date

    from core.pj.relatorios import balanco
    b = balanco(_lancs_balanco(), 1000.0, date(2026, 9, 30), date(2026, 10, 4),
                contas_em_aberto=False)
    assert b["fecha"]
    assert _linha(b, "ativo", "Caixa e bancos") == 18600.0
    assert _linha(b, "ativo", "Contas a receber") == 0.0
    assert _linha(b, "passivo", "Contas a pagar") == 0.0


def test_balanco_antes_da_abertura_e_sem_saldo():
    from datetime import date

    from core.pj.relatorios import balanco, saldo_ate
    assert balanco(_lancs_balanco(), None, None, date(2026, 11, 7)) is None
    # extrato antigo: a data do balanço é anterior à âncora do saldo de abertura
    b = balanco(_lancs_balanco(), 1000.0, date(2026, 10, 31), date(2026, 10, 5),
                contas_em_aberto=False)
    assert b["fecha"]
    assert _linha(b, "ativo", "Caixa e bancos") == saldo_ate(
        1000.0, date(2026, 10, 31), _lancs_balanco(), date(2026, 10, 5))


# ── Estorno de fornecedor: abate a despesa, nunca vira receita ──────────────

def test_estorno_de_quem_nao_tem_regra_nao_vira_receita():
    from core.pj.plano_contas import sugerir_categoria
    estorno = {"valor": 200.0, "descricao": "Pix recebido - Estorno compra Loja Y",
               "contraparte": "Loja Y"}
    assert sugerir_categoria(estorno, {}) is None
    for desc in ("TED recebida - Reembolso Kalunga", "Pix recebido - devolução fornecedor",
                 "Crédito recebido ressarcimento", "Cashback recebido"):
        assert sugerir_categoria({**estorno, "descricao": desc}, {}) is None, desc
    # crédito comum continua sugerindo receita
    assert sugerir_categoria({**estorno, "descricao": "Pix recebido - Andressa"},
                             {}) == "receita_servicos"


def test_estorno_usa_a_regra_da_saida_que_ele_desfaz():
    from core.pj.plano_contas import sugerir_categoria
    assert sugerir_categoria({"valor": 12.9, "descricao": "Estorno de tarifa Pix",
                              "contraparte": "Cora"}, {}) == "tarifas"
    assert sugerir_categoria({"valor": 3.5, "descricao": "Estorno IOF",
                              "contraparte": "Cora"}, {}) == "despesas_financeiras"


def test_estorno_de_fornecedor_conhecido_segue_a_regra_dele():
    from core.pj.plano_contas import sugerir_categoria
    regras = {"imobiliaria central": "aluguel"}
    assert sugerir_categoria({"valor": 200.0, "descricao": "Pix recebido - estorno",
                              "contraparte": "Imobiliária Central"}, regras) == "aluguel"


def test_estorno_abate_a_despesa_e_nao_mexe_na_base_do_das():
    from datetime import date

    from core.pj.relatorios import dre, serie_mensal

    def pago(dia, valor, cat):
        return {"data": date(2026, 11, dia), "valor": valor, "categoria": cat,
                "status": "pago"}
    sem = [pago(1, 1000.0, "receita_servicos"), pago(2, -800.0, "aluguel")]
    com = [*sem, pago(3, 200.0, "aluguel")]
    assert dre(com, [(2026, 11)])["grupos"]["despesas_operacionais"] == -600.0
    assert dre(com, [(2026, 11)])["resultado"] == dre(sem, [(2026, 11)])["resultado"] + 200.0
    base = [serie_mensal(x, date(2026, 11, 1), meses=1)[0]["receita_simples"] for x in (sem, com)]
    assert base == [1000.0, 1000.0]


def test_balanco_conta_o_historico_anterior_a_ancora_do_saldo():
    """Achado do Codex no PR #686: extrato de outubro importado depois do de
    novembro deixa a âncora em 31/10, e o empréstimo tomado em outubro sumia
    do passivo (só a parcela de novembro aparecia, como dívida negativa)."""
    from datetime import date

    from core.pj.relatorios import balanco

    def pago(d, v, cat):
        return {"data": d, "valor": v, "categoria": cat, "status": "pago"}
    lancs = [pago(date(2026, 10, 5), 10000.0, "emprestimo"),
             pago(date(2026, 10, 9), -2000.0, "aplicacao"),
             pago(date(2026, 11, 5), -1000.0, "emprestimo")]
    b = balanco(lancs, 8000.0, date(2026, 10, 31), date(2026, 11, 7))
    assert b["fecha"]
    assert _linha(b, "ativo", "Caixa e bancos") == 7000.0
    assert _linha(b, "ativo", "Aplicações financeiras") == 2000.0
    assert _linha(b, "passivo", "Empréstimos e financiamentos") == 9000.0
    assert _linha(b, "patrimonio_liquido", "Saldo anterior aos lançamentos registrados") == 0.0


# ── Categorias criadas pelo usuário ─────────────────────────────────────────

def _plano_proprio():
    from core.pj.plano_contas import Categoria, PlanoDeContas
    return PlanoDeContas((Categoria("u_comissoes", "Comissões de vendedores", "custos"),
                          Categoria("u_consultoria", "Consultoria", "receita_bruta"),
                          Categoria("u_caucao", "Caução de aluguel", "fora_do_resultado")))


def test_plano_junta_fixas_e_proprias_na_ordem_da_dre():
    from core.pj.plano_contas import CATEGORIAS
    plano = _plano_proprio()
    assert len(plano.todas) == len(CATEGORIAS) + 3
    assert plano.rotulo("u_comissoes") == "Comissões de vendedores"
    assert plano.rotulo("inexistente") == "Sem categoria"
    custos = [c.codigo for c in plano.do_grupo("custos")]
    assert custos[-1] == "u_comissoes"  # depois das fixas do mesmo grupo
    nomes = plano.nomes()
    assert nomes.index("Consultoria") < nomes.index("Comissões de vendedores")


def test_codigo_da_propria_nao_colide():
    from core.pj.plano_contas import codigo_para_propria
    assert codigo_para_propria("Comissões de Vendedores", set()) == "u_comissoes_de_vendedores"
    assert codigo_para_propria("Comissões", {"u_comissoes"}) == "u_comissoes_2"
    assert codigo_para_propria("!!!", set()) == "u_categoria"


def test_regra_aprendida_com_categoria_propria_e_sugerida():
    from core.pj.plano_contas import sugerir_categoria
    plano = _plano_proprio()
    mov = {"valor": -300.0, "descricao": "Pix enviado - Vendedor X", "contraparte": "Vendedor X"}
    regras = {"vendedor x": "u_comissoes"}
    assert sugerir_categoria(mov, regras, plano) == "u_comissoes"
    # sem o plano do dono, a regra não vale (categoria desconhecida)
    assert sugerir_categoria(mov, regras) is None
    # receita própria numa saída também vira devolução
    assert sugerir_categoria({**mov, "contraparte": "Cliente Y"},
                             {"cliente y": "u_consultoria"}, plano) == "devolucoes"


def test_dre_e_das_contam_a_categoria_propria_pelo_grupo():
    from datetime import date

    from core.pj.relatorios import dre, serie_mensal

    def pago(valor, cat, grupo=None):
        return {"data": date(2026, 11, 3), "valor": valor, "categoria": cat,
                "status": "pago", "grupo_categoria": grupo}
    lancs = [pago(1000.0, "receita_servicos"), pago(500.0, "u_consultoria", "receita_bruta"),
             pago(-200.0, "u_comissoes", "custos"), pago(-50.0, "u_x", "grupo_falso")]
    d = dre(lancs, [(2026, 11)])
    assert d["grupos"]["receita_bruta"] == 1500.0
    assert d["grupos"]["custos"] == -200.0
    assert d["categorias"]["u_consultoria"] == 500.0
    assert d["sem_categoria_n"] == 1  # grupo inválido não inventa linha
    assert serie_mensal(lancs, date(2026, 11, 1), meses=1)[0]["receita_simples"] == 1500.0


def test_balanco_fecha_com_categoria_propria_fora_do_resultado():
    from datetime import date

    from core.pj.relatorios import balanco

    def pago(valor, cat, grupo=None):
        return {"data": date(2026, 11, 3), "valor": valor, "categoria": cat,
                "status": "pago", "grupo_categoria": grupo}
    lancs = [pago(1000.0, "receita_servicos"),
             pago(-300.0, "u_caucao", "fora_do_resultado")]
    b = balanco(lancs, 0.0, date(2026, 10, 31), date(2026, 11, 7))
    assert b["fecha"]
    assert dict(b["patrimonio_liquido"])["Outras movimentações fora do resultado"] == -300.0
    assert dict(b["patrimonio_liquido"])["Resultado acumulado (DRE, caixa)"] == 1000.0
