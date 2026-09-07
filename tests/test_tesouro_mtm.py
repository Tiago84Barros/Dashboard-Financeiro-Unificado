"""
tests/test_tesouro_mtm.py

Testes puros do motor de marcação a mercado do Tesouro Direto e dos parsers
do Extrato Analítico e da curva oficial. Sem banco e sem rede.

Os PUs oficiais usados na calibração estão embutidos como constantes: vieram do
CSV público do Tesouro Transparente e são a evidência de que a convenção de
dias úteis não foi adivinhada.
"""
from __future__ import annotations

import math
from datetime import date

import pytest

from core.tesouro_mtm import (
    AVALIAR_TROCA,
    MANTER,
    SEM_BASE,
    VENDA_DESVANTAJOSA,
    LoteTesouro,
    aliquota_iof,
    aliquota_ir,
    avaliar_lote,
    comparar_carregar_vs_vender,
    dias_uteis,
    feriados_nacionais,
    mtm_por_taxa,
    parse_taxa_contratada,
    pascoa,
    preco_unitario,
)

# ─────────────────────────────────────────────────────────────────────────────
# Calendário
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ano,esperado", [
    (2024, date(2024, 3, 31)),
    (2025, date(2025, 4, 20)),
    (2026, date(2026, 4, 5)),
    (2027, date(2027, 3, 28)),
])
def test_pascoa(ano, esperado):
    assert pascoa(ano) == esperado


def test_feriados_moveis_derivam_da_pascoa():
    f = feriados_nacionais(2026)
    assert date(2026, 4, 3) in f      # sexta-feira santa
    assert date(2026, 2, 17) in f     # carnaval
    assert date(2026, 6, 4) in f      # corpus christi


def test_consciencia_negra_so_a_partir_de_2024():
    """20/11 virou feriado nacional pela Lei 14.759/2023.

    Sem esse recorte, marcar preço antigo conta um dia útil a menos do que o
    mercado contou na época — foi exatamente o desvio observado nos PUs de
    2022 e 2023 durante a calibração.
    """
    assert date(2023, 11, 20) not in feriados_nacionais(2023)
    assert date(2024, 11, 20) in feriados_nacionais(2024)


def test_dias_uteis_inclui_data_base_e_exclui_vencimento():
    # 2026-09-04 é sexta; 2026-09-07 (Independência) cai numa segunda.
    assert dias_uteis(date(2026, 9, 4), date(2026, 9, 9)) == 2  # sexta e terça
    assert dias_uteis(date(2026, 9, 9), date(2026, 9, 9)) == 0
    assert dias_uteis(date(2026, 9, 10), date(2026, 9, 9)) == 0


# PUs de venda oficiais do Tesouro Prefixado publicados para a data-base
# 2026-09-04 (Tesouro Transparente). A taxa vem com duas casas, então a
# inversão de PU = 1000/(1+i)**(du/252) não devolve inteiro exato: o resíduo
# aceitável é o do próprio arredondamento.
PUS_OFICIAIS_2026_09_04 = [
    # (vencimento, taxa % a.a., PU de venda, du esperado)
    (date(2027, 1, 1), 13.61, 960.30, 80),
    (date(2028, 1, 1), 13.72, 844.61, 331),
    (date(2029, 1, 1), 14.06, 739.14, 579),
    (date(2031, 1, 1), 14.33, 563.30, 1080),
    (date(2032, 1, 1), 14.43, 490.42, 1332),
]


@pytest.mark.parametrize("vencimento,taxa,pu,du_esperado", PUS_OFICIAIS_2026_09_04)
def test_du_calibrado_contra_pu_oficial(vencimento, taxa, pu, du_esperado):
    base = date(2026, 9, 4)
    du = dias_uteis(base, vencimento)
    assert du == du_esperado

    du_implicito = math.log(1000.0 / pu) / math.log(1.0 + taxa / 100.0) * 252.0
    # Um erro de convenção vale 1 du inteiro; o arredondamento do centavo vale
    # centésimos. A tolerância separa os dois casos.
    assert abs(du_implicito - du) < 0.1


@pytest.mark.parametrize("vencimento,taxa,pu,du_esperado", PUS_OFICIAIS_2026_09_04)
def test_preco_unitario_reproduz_pu_oficial(vencimento, taxa, pu, du_esperado):
    calculado = preco_unitario(1000.0, taxa / 100.0, dias_uteis(date(2026, 9, 4), vencimento))
    assert calculado == pytest.approx(pu, abs=0.05)


# ─────────────────────────────────────────────────────────────────────────────
# Taxa contratada
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("texto,indexador,valor", [
    ("SELIC + 0,103%", "SELIC", 0.00103),
    ("SELIC - 0,520%", "SELIC", -0.00520),
    ("IPCA + 6,12%",   "IPCA",  0.0612),
    ("13,50%",         "PRE",   0.1350),
    ("IGPM + 4,00%",   "IGPM",  0.0400),
])
def test_parse_taxa_contratada(texto, indexador, valor):
    taxa = parse_taxa_contratada(texto)
    assert taxa is not None
    assert taxa.indexador == indexador
    assert taxa.valor == pytest.approx(valor)


@pytest.mark.parametrize("texto", [None, "", "   ", "n/d", "taxa não informada"])
def test_taxa_ilegivel_vira_none_e_nao_zero(texto):
    """Zero é uma taxa: viraria MtM calculado a partir de dado que não existe."""
    assert parse_taxa_contratada(texto) is None


# ─────────────────────────────────────────────────────────────────────────────
# Marcação
# ─────────────────────────────────────────────────────────────────────────────

def test_mtm_positivo_quando_taxa_de_mercado_cai():
    assert mtm_por_taxa(0.14, 0.12, 1260) > 0


def test_mtm_negativo_quando_taxa_de_mercado_sobe():
    assert mtm_por_taxa(0.12, 0.14, 1260) < 0


def test_mtm_zero_no_vencimento():
    """No vencimento o preço converge para o nominal, qualquer que seja a taxa."""
    assert mtm_por_taxa(0.12, 0.16, 0) == 0.0


def test_mtm_exige_as_duas_pontas():
    assert mtm_por_taxa(None, 0.14, 500) is None
    assert mtm_por_taxa(0.14, None, 500) is None


def test_mtm_independe_do_vna():
    """A identidade é razão de fatores: o VNA cancela.

    É o que permite marcar IPCA+ e Selic sem conhecer o VNA nem projetar
    inflação — só com as duas taxas.
    """
    taxa_c, taxa_m, du = 0.0612, 0.0740, 1500
    mtm = mtm_por_taxa(taxa_c, taxa_m, du)
    for vna in (1000.0, 4321.87, 19_740.70):
        pu_contratado = preco_unitario(vna, taxa_c, du)
        pu_mercado = preco_unitario(vna, taxa_m, du)
        assert pu_mercado / pu_contratado - 1.0 == pytest.approx(mtm, rel=1e-12)


@pytest.mark.parametrize("dias,aliq", [
    (1, 0.225), (180, 0.225), (181, 0.20), (360, 0.20),
    (361, 0.175), (720, 0.175), (721, 0.15), (3000, 0.15),
])
def test_aliquota_ir_regressiva(dias, aliq):
    assert aliquota_ir(dias) == pytest.approx(aliq)


@pytest.mark.parametrize("dias,fator", [(0, 1.0), (15, 0.5), (29, 1 / 30), (30, 0.0), (400, 0.0)])
def test_aliquota_iof_regressiva(dias, fator):
    assert aliquota_iof(dias) == pytest.approx(fator, abs=1e-9)


def _lote(**kw) -> LoteTesouro:
    base = dict(
        data_aplicacao=date(2025, 7, 9),
        quantidade=0.03,
        preco_aplicacao=16_812.23,
        valor_investido=504.36,
        taxa_contratada=parse_taxa_contratada("SELIC + 0,103%"),
        valor_bruto_extrato=592.53,
    )
    base.update(kw)
    return LoteTesouro(**base)


def test_avaliar_lote_usa_curva_quando_ha_pu():
    av = avaliar_lote(
        _lote(), nome_titulo="Tesouro Selic", vencimento=date(2031, 3, 1),
        data_avaliacao=date(2026, 9, 4), taxa_mercado_resgate=0.0008,
        pu_mercado=19_740.70,
    )
    assert av.fonte_preco == "curva"
    assert av.valor_bruto == pytest.approx(19_740.70 * 0.03)
    assert av.du_restante == dias_uteis(date(2026, 9, 4), date(2031, 3, 1))


def test_avaliar_lote_cai_para_o_extrato_e_diz_que_caiu():
    """Sem curva o número existe, mas congelado na data do arquivo.

    O campo `fonte_preco` é o que impede a tela de apresentar valor velho como
    se fosse marcação de hoje.
    """
    av = avaliar_lote(
        _lote(), nome_titulo="Tesouro Selic", vencimento=date(2031, 3, 1),
        data_avaliacao=date(2026, 9, 4), taxa_mercado_resgate=0.0008,
        pu_mercado=None,
    )
    assert av.fonte_preco == "extrato"
    assert av.valor_bruto == pytest.approx(592.53)


def test_avaliar_lote_sem_preco_nenhum_nao_inventa():
    av = avaliar_lote(
        _lote(valor_bruto_extrato=None), nome_titulo="Tesouro Selic",
        vencimento=date(2031, 3, 1), data_avaliacao=date(2026, 9, 4),
        taxa_mercado_resgate=0.0008, pu_mercado=None,
    )
    assert av.fonte_preco == "indisponivel"
    assert av.valor_bruto is None
    assert av.valor_liquido is None


def test_ir_incide_sobre_ganho_e_nunca_sobre_prejuizo():
    av = avaliar_lote(
        _lote(valor_investido=700.0), nome_titulo="Tesouro Prefixado",
        vencimento=date(2031, 1, 1), data_avaliacao=date(2026, 9, 4),
        taxa_mercado_resgate=0.1433, pu_mercado=None,
    )
    assert av.rendimento_bruto < 0
    assert av.ir == pytest.approx(0.0)
    assert av.iof == pytest.approx(0.0)


def test_titulo_com_cupom_marca_aproximado():
    av = avaliar_lote(
        _lote(), nome_titulo="Tesouro IPCA+ com Juros Semestrais",
        vencimento=date(2035, 5, 15), data_avaliacao=date(2026, 9, 4),
        taxa_mercado_resgate=0.0740, pu_mercado=1000.0,
    )
    assert av.aproximado is True


# ─────────────────────────────────────────────────────────────────────────────
# Veredito
# ─────────────────────────────────────────────────────────────────────────────

def _avaliacao_padrao():
    return avaliar_lote(
        _lote(), nome_titulo="Tesouro Prefixado", vencimento=date(2031, 1, 1),
        data_avaliacao=date(2026, 9, 4), taxa_mercado_resgate=0.1433,
        pu_mercado=563.30,
    )


def test_sem_alternativa_nao_ha_veredito():
    """Vender para recomprar o mesmo título perde por definição.

    O motor recusa dar veredito em vez de comparar o investimento contra ele
    mesmo e devolver um 'MANTER' que não foi medido.
    """
    c = comparar_carregar_vs_vender(
        [_avaliacao_padrao()], vencimento=date(2031, 1, 1),
        data_avaliacao=date(2026, 9, 4), taxa_mercado_resgate=0.1433,
        taxa_alternativa=None,
    )
    assert c.veredito == SEM_BASE
    assert "alternativa" in c.motivo.lower()


def test_alternativa_igual_a_taxa_de_mercado_nao_compensa_o_ir_antecipado():
    c = comparar_carregar_vs_vender(
        [_avaliacao_padrao()], vencimento=date(2031, 1, 1),
        data_avaliacao=date(2026, 9, 4), taxa_mercado_resgate=0.1433,
        taxa_alternativa=0.1433,
    )
    assert c.veredito == VENDA_DESVANTAJOSA
    assert c.vantagem_reais < 0


def test_alternativa_muito_melhor_abre_avaliacao_de_troca():
    c = comparar_carregar_vs_vender(
        [_avaliacao_padrao()], vencimento=date(2031, 1, 1),
        data_avaliacao=date(2026, 9, 4), taxa_mercado_resgate=0.1433,
        taxa_alternativa=0.1433 + 0.05,
    )
    assert c.veredito == AVALIAR_TROCA
    assert c.vantagem_reais > 0


def test_titulo_vencido_nao_tem_o_que_comparar():
    c = comparar_carregar_vs_vender(
        [_avaliacao_padrao()], vencimento=date(2026, 9, 4),
        data_avaliacao=date(2026, 9, 4), taxa_mercado_resgate=0.1433,
        taxa_alternativa=0.20,
    )
    assert c.veredito == MANTER
    assert c.du_restante == 0


def test_sem_lote_avaliavel_nao_ha_veredito():
    av = avaliar_lote(
        _lote(valor_bruto_extrato=None), nome_titulo="Tesouro Prefixado",
        vencimento=date(2031, 1, 1), data_avaliacao=date(2026, 9, 4),
        taxa_mercado_resgate=0.1433, pu_mercado=None,
    )
    c = comparar_carregar_vs_vender(
        [av], vencimento=date(2031, 1, 1), data_avaliacao=date(2026, 9, 4),
        taxa_mercado_resgate=0.1433, taxa_alternativa=0.20,
    )
    assert c.veredito == SEM_BASE


def test_indice_omitido_inverte_o_veredito_de_titulo_indexado():
    """O defeito que o parâmetro `taxa_indice` existe para impedir.

    Um Tesouro Selic é publicado a 0,08% ao ano — isso é o ágio, não o
    rendimento. Sem o índice, as duas pernas capitalizam só o ágio: a
    alternativa com 0,50 pp a mais parece render 1,8% acima de carregar.
    Com a Selic projetada nos dois lados, carregar ganha por 8,5% — o IR
    diferido vale muito mais quando a taxa cheia é de dois dígitos.

    As duas pernas precisam ser do **mesmo indexador**: comparar um ágio de
    Selic com uma taxa cheia de prefixado é somar grandezas diferentes, e é
    quem monta o seletor de alternativas que garante isso.
    """
    sem_indice = comparar_carregar_vs_vender(
        [_avaliacao_padrao()], vencimento=date(2031, 1, 1),
        data_avaliacao=date(2026, 9, 4), taxa_mercado_resgate=0.0008,
        taxa_alternativa=0.0058,
    )
    com_indice = comparar_carregar_vs_vender(
        [_avaliacao_padrao()], vencimento=date(2031, 1, 1),
        data_avaliacao=date(2026, 9, 4), taxa_mercado_resgate=0.0008,
        taxa_alternativa=0.0058, taxa_indice=0.1438,
    )
    assert sem_indice.veredito == AVALIAR_TROCA
    assert com_indice.veredito == VENDA_DESVANTAJOSA
    assert com_indice.taxa_indice == 0.1438


def test_indice_zero_e_indice_ausente_dao_o_mesmo_resultado():
    """Prefixado passa 0,0 e o resto do app pode passar None; não podem divergir."""
    args = dict(vencimento=date(2031, 1, 1), data_avaliacao=date(2026, 9, 4),
                taxa_mercado_resgate=0.1433, taxa_alternativa=0.16)
    zero = comparar_carregar_vs_vender([_avaliacao_padrao()], taxa_indice=0.0, **args)
    nulo = comparar_carregar_vs_vender([_avaliacao_padrao()], taxa_indice=None, **args)
    assert zero.veredito == nulo.veredito
    assert zero.vantagem_reais == pytest.approx(nulo.vantagem_reais)
