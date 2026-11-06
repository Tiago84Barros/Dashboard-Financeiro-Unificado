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
