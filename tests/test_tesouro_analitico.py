"""
tests/test_tesouro_analitico.py

Helpers puros do importador do Extrato Analítico e do parser da curva oficial
do Tesouro Direto. Sem banco e sem rede.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from core.tesouro_curva import indexador_do_titulo, indice_implicito
from core.tesouro_mtm import (
    SEM_BASE,
    LoteTesouro,
    comparar_carregar_vs_vender,
    parse_taxa_contratada,
)
from data_pipeline.importers.investments.tesouro_analitico import (
    parse_cabecalho,
    parse_lotes,
)
from data_pipeline.importers.investments.tesouro_direto import tesouro_security_key
from data_pipeline.jobs.update_tesouro_curva import parse_csv
from design.tesouro_mtm_cards import (
    card_conjuntura_html,
    card_titulo_html,
    card_veredito_html,
)

# ─────────────────────────────────────────────────────────────────────────────
# Cabeçalho
# ─────────────────────────────────────────────────────────────────────────────

CABECALHO = [
    ("EXTRATO ANALÍTICO - Tesouro Selic 2031", None),
    (None, None),
    ("AGENTE DE CUSTÓDIA: XP INVESTIMENTOS CCTVM S/A.", None),
    ("VENCIMENTO: 01/03/2031", None),
    (None, None),
    ("Extrato Analítico gerado em 07/09/2026 11:41:07. Período 09/2026.", None),
]


def test_parse_cabecalho_le_titulo_vencimento_e_custodiante():
    cab = parse_cabecalho(CABECALHO)
    assert cab["titulo"] == "Tesouro Selic 2031"
    assert cab["vencimento"] == date(2031, 3, 1)
    assert cab["gerado_em"] == date(2026, 9, 7)
    assert "XP INVESTIMENTOS" in cab["custodiante"]


def test_chave_do_titulo_bate_com_a_da_curva():
    """Extrato e curva precisam concordar na chave, senão o join silencia.

    Um `security_key` divergente não levanta erro: devolve zero linhas de
    mercado e a tela cai para o valor congelado do extrato como se fosse o
    comportamento normal.
    """
    cab = parse_cabecalho(CABECALHO)
    chave_extrato = tesouro_security_key(cab["titulo"], cab["vencimento"])
    chave_curva = tesouro_security_key("Tesouro Selic", date(2031, 3, 1))
    assert chave_extrato == chave_curva == "TSELIC2031"


# ─────────────────────────────────────────────────────────────────────────────
# Lotes
# ─────────────────────────────────────────────────────────────────────────────

def _linha(data, qtd, preco, investido, taxa="SELIC + 0,103%", bruto=592.53):
    return (data, qtd, preco, investido, taxa, 12.34, 17.48, bruto,
            424, 17.5, 15.42, 0.0, 0.85, 0.0, 576.26)


def test_parse_lotes_le_uma_linha_por_aplicacao():
    lotes, ignoradas = parse_lotes([
        _linha("09/07/2025", 0.03, 16_812.23, 504.36),
        _linha("11/08/2025", 0.02, 17_000.00, 340.00),
    ])
    assert ignoradas == 0
    assert [lote["data_aplicacao"] for lote in lotes] == [date(2025, 7, 9), date(2025, 8, 11)]
    assert lotes[0]["taxa_contratada"] == "SELIC + 0,103%"


def test_linha_total_nao_vira_lote():
    """Somar a linha 'Total' dobraria a posição sem nenhum sinal de erro."""
    lotes, _ = parse_lotes([
        _linha("09/07/2025", 0.03, 16_812.23, 504.36),
        ("Total", 0.03, None, 504.36, None, None, None, 592.53,
         None, None, 15.42, 0.0, 0.85, 0.0, 576.26),
    ])
    assert len(lotes) == 1


def test_quantidade_e_recomposta_pelo_valor_investido():
    """O arquivo publica quantidade com 2 casas; o PU, inteiro.

    Usar a quantidade arredondada erraria o valor de mercado do lote no
    terceiro dígito — pouco por linha e visível na soma da carteira.
    """
    lotes, _ = parse_lotes([_linha("09/07/2025", 0.03, 16_812.23, 504.36)])
    assert lotes[0]["quantidade"] == pytest.approx(504.36 / 16_812.23, rel=1e-12)
    assert lotes[0]["quantidade"] != 0.03


@pytest.mark.parametrize("qtd,investido", [(0, 504.36), (0.03, 0), (-1, 504.36), (None, None)])
def test_linha_sem_os_tres_campos_essenciais_e_ignorada(qtd, investido):
    lotes, ignoradas = parse_lotes([_linha("09/07/2025", qtd, 16_812.23, investido)])
    assert lotes == []
    assert ignoradas == 1


def test_linha_sem_data_valida_nao_conta_como_ignorada():
    """Rodapé e cabeçalho não são erro de dado — não podem inflar o contador."""
    lotes, ignoradas = parse_lotes([
        ("Data da aplicação", "Quantidade", None, None, None, None, None,
         None, None, None, None, None, None, None, None),
    ])
    assert lotes == []
    assert ignoradas == 0


# ─────────────────────────────────────────────────────────────────────────────
# Curva oficial
# ─────────────────────────────────────────────────────────────────────────────

CSV_TESOURO = (
    "Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;Taxa Venda Manha;"
    "PU Compra Manha;PU Venda Manha;PU Base Manha\n"
    "Tesouro Selic;01/03/2031;04/09/2026;0,07;0,08;19759,78;19740,7;19740,7\n"
    "Tesouro Prefixado;01/01/2031;04/09/2026;14,43;14,33;562,55;563,3;563,3\n"
    "Tesouro Selic;01/03/2031;03/09/2026;0,06;0,07;19750,11;19731,2;19731,2\n"
)


def test_parse_csv_normaliza_linhas_da_curva():
    regs = parse_csv(CSV_TESOURO)
    assert len(regs) == 3
    selic = [r for r in regs if r["security_key"] == "TSELIC2031"]
    assert len(selic) == 2
    assert selic[0]["base_date"] == date(2026, 9, 4)
    assert selic[0]["sell_rate"] == pytest.approx(0.08)
    assert selic[0]["sell_pu"] == pytest.approx(19_740.70)


def test_ponta_de_compra_e_de_venda_nao_se_confundem():
    """Quem resgata recebe o PU de venda, que é o menor. Inverter as pontas
    embute o spread com o sinal trocado no resultado do investidor."""
    regs = parse_csv(CSV_TESOURO)
    selic = [r for r in regs if r["security_key"] == "TSELIC2031"][0]
    assert selic["buy_pu"] > selic["sell_pu"]
    assert selic["buy_rate"] < selic["sell_rate"]
    assert selic["sell_pu"] == selic["base_pu"]


def test_janela_curta_vale_so_para_titulo_que_nao_esta_na_carteira():
    """A série longa é só de quem o usuário tem; do resto basta o preço de hoje.

    Guardar a história dos 60 títulos ofertados para um usuário que tem um
    multiplicaria a tabela por sessenta no Supabase, que opera perto do teto.
    """
    csv_com_historico = CSV_TESOURO + (
        "Tesouro Prefixado;01/01/2031;03/09/2026;14,40;14,30;562,10;562,9;562,9\n"
    )
    regs = parse_csv(
        csv_com_historico,
        desde=date(2026, 9, 1),
        desde_outros=date(2026, 9, 4),
        chaves_longas=frozenset({"TSELIC2031"}),
    )
    chaves = {(r["security_key"], r["base_date"]) for r in regs}
    assert ("TSELIC2031", date(2026, 9, 3)) in chaves   # série longa preservada
    assert ("TPRE2031", date(2026, 9, 4)) in chaves     # preço de hoje mantido
    assert ("TPRE2031", date(2026, 9, 3)) not in chaves  # história descartada


def test_corte_longo_descarta_tudo_que_e_anterior():
    assert parse_csv(CSV_TESOURO, desde=date(2026, 9, 5)) == []


# ─────────────────────────────────────────────────────────────────────────────
# Índice implícito
# ─────────────────────────────────────────────────────────────────────────────

def _cardapio() -> pd.DataFrame:
    linhas = [
        ("TPRE2029", "Tesouro Prefixado", date(2029, 1, 1), 14.10, 14.00),
        ("TPRE2031", "Tesouro Prefixado", date(2031, 1, 1), 14.43, 14.33),
        ("TIPCA2029", "Tesouro IPCA+", date(2029, 5, 15), 7.60, 7.50),
        ("TIPCA2032", "Tesouro IPCA+ com Juros Semestrais", date(2032, 8, 15), 7.20, 7.10),
        ("TSELIC2031", "Tesouro Selic", date(2031, 3, 1), 0.07, 0.08),
    ]
    df = pd.DataFrame(linhas, columns=["security_key", "title_name", "maturity_date",
                                       "buy_rate", "sell_rate"])
    df["buy_rate_dec"] = df["buy_rate"] / 100.0
    df["sell_rate_dec"] = df["sell_rate"] / 100.0
    return df


def test_prefixado_nao_recebe_indice():
    """A taxa do prefixado já é cheia; somar índice contaria o ganho duas vezes."""
    assert indice_implicito(_cardapio(), "PRE", date(2031, 1, 1)) == 0.0


def test_selic_projetada_vem_do_prefixado_de_vencimento_mais_proximo():
    idx = indice_implicito(_cardapio(), "SELIC", date(2031, 3, 1))
    assert idx == pytest.approx((0.1443 + 0.1433) / 2)  # TPRE2031, não TPRE2029


def test_inflacao_implicita_e_o_breakeven_pre_sobre_real():
    idx = indice_implicito(_cardapio(), "IPCA", date(2029, 5, 15))
    pre = (0.1410 + 0.1400) / 2
    real = (0.0760 + 0.0750) / 2
    assert idx == pytest.approx((1 + pre) / (1 + real) - 1)


def test_titulo_com_cupom_nao_entra_no_breakeven():
    """Taxa de papel com juros semestrais não é comparável à de zero-cupom.

    Se o IPCA+ 2032 (com cupom) entrasse, um vencimento perto dele devolveria
    um breakeven calculado com grandezas diferentes — e ninguém veria.
    """
    idx = indice_implicito(_cardapio(), "IPCA", date(2032, 8, 15))
    real = (0.0760 + 0.0750) / 2  # o zero-cupom de 2029, único elegível
    pre = (0.1443 + 0.1433) / 2
    assert idx == pytest.approx((1 + pre) / (1 + real) - 1)


def test_indexador_sem_par_no_cardapio_nao_vira_zero():
    """IGP-M não tem par para inferir índice; chutar aqui decidiria a venda."""
    assert indice_implicito(_cardapio(), "IGPM", date(2031, 1, 1)) is None
    assert indice_implicito(pd.DataFrame(), "SELIC", date(2031, 1, 1)) is None


# ─────────────────────────────────────────────────────────────────────────────
# Indexador pelo nome — é ele que monta o cardápio de alternativas
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nome,esperado", [
    ("Tesouro Selic 2031", "SELIC"),
    ("Tesouro IPCA+ 2029", "IPCA"),
    ("Tesouro IPCA+ com Juros Semestrais 2032", "IPCA"),
    ("Tesouro Prefixado 2031", "PRE"),
    ("Tesouro Prefixado com Juros Semestrais 2033", "PRE"),
    ("Tesouro IGPM+ com Juros Semestrais 2031", "IGPM"),
])
def test_indexador_sai_do_nome_publicado(nome, esperado):
    assert indexador_do_titulo(nome) == esperado


@pytest.mark.parametrize("nome", ["Tesouro Educa+ 2035", "Tesouro Renda+ Aposentadoria Extra 2045"])
def test_educa_e_renda_sao_ipca_e_nao_caem_no_prefixado(nome):
    """Nome comercial diferente, indexador igual.

    Se caíssem no `PRE` do fim da lista, apareceriam como alternativa de
    prefixado — e o motor compararia cupom real com taxa nominal cheia.
    """
    assert indexador_do_titulo(nome) == "IPCA"


# ─────────────────────────────────────────────────────────────────────────────
# Cards: só formatação, mas formatação que já mentiu neste projeto
# ─────────────────────────────────────────────────────────────────────────────

class _TituloFake:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _titulo_fake(**over):
    lote = LoteTesouro(
        data_aplicacao=date(2025, 7, 9), quantidade=0.03, preco_aplicacao=16_812.23,
        valor_investido=504.36, taxa_contratada=parse_taxa_contratada("SELIC + 0,103%"),
    )
    base = dict(
        security_key="TSELIC2031", titulo="Tesouro Selic 2031",
        vencimento=date(2031, 3, 1), custodiante="XP", report_date=date(2026, 9, 7),
        lotes=[lote], avaliacoes=[],
        taxa_mercado_venda=0.0008, taxa_mercado_compra=0.0007,
        pu_mercado=19_740.70, data_curva=date(2026, 9, 4),
        quantidade=0.03, valor_investido=504.36, valor_bruto=592.53,
        valor_liquido=576.26, ir=15.42, iof=0.0,
        ganho_mtm_reais=-1.20, mtm_pct=-0.00202,
        fonte_preco="curva", aproximado=False, indexador="SELIC", taxa_indice=0.1438,
    )
    base.update(over)
    return _TituloFake(**base)


def test_card_do_titulo_diz_de_onde_veio_o_preco():
    """Valor da data do extrato não pode aparecer com cara de preço de hoje."""
    da_curva = card_titulo_html(_titulo_fake())
    do_extrato = card_titulo_html(_titulo_fake(fonte_preco="extrato", data_curva=None))
    assert "04/09/2026" in da_curva
    assert "curva não cobre" in do_extrato
    assert "04/09/2026" not in do_extrato


def test_card_avisa_quando_a_marcacao_e_aproximada():
    assert "aproximação" in card_titulo_html(_titulo_fake(aproximado=True))
    assert "aproximação" not in card_titulo_html(_titulo_fake())


def test_card_de_veredito_sem_alternativa_nao_imprime_grade_de_numeros():
    """Sem alternativa não há conta; imprimir zeros pareceria empate medido."""
    titulo = _titulo_fake()
    comp = comparar_carregar_vs_vender(
        [], vencimento=titulo.vencimento, data_avaliacao=date(2026, 9, 4),
        taxa_mercado_resgate=0.0008, taxa_alternativa=None,
    )
    html = card_veredito_html(titulo, comp)
    assert SEM_BASE in html
    assert "CARREGAR ATÉ O FIM" not in html


def test_card_de_conjuntura_nunca_apresenta_dado_anual_como_de_hoje():
    html = card_conjuntura_html(
        data_curva=date(2026, 9, 4),
        pre_curto={"title_name": "Tesouro Prefixado", "maturity_date": date(2027, 1, 1),
                   "sell_rate_dec": 0.1361},
        pre_longo={"title_name": "Tesouro Prefixado", "maturity_date": date(2032, 1, 1),
                   "sell_rate_dec": 0.1443},
        inflacao_implicita=0.065,
        macro_ano={"ano": 2025, "selic": 14.75},
    )
    assert "04/09/2026" in html
    assert "não é" in html and "2025" in html


def test_conjuntura_sem_dois_vertices_nao_inventa_inclinacao():
    html = card_conjuntura_html(data_curva=None, pre_curto=None, pre_longo=None,
                                inflacao_implicita=None)
    assert "Sem dois vértices" in html
