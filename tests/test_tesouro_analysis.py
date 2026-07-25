import pytest

from core.tesouro_analysis import (
    aliquota_ir_tesouro,
    analise_suficiencia_tesouro,
    ganho_liquido_estimado,
    ganho_mtm_por_precos,
    retorno_mercado_sobre_custo,
    tesouro_meta,
)


def test_taxa_compra_igual_taxa_mercado_mtm_zero_apesar_do_carrego():
    valor_face = 1_000.0
    taxa = 0.10
    preco_compra = valor_face / (1 + taxa) ** 5
    preco_curva_hoje = valor_face / (1 + taxa) ** 3

    retorno_acumulado = retorno_mercado_sobre_custo(preco_curva_hoje, preco_compra)
    mtm = ganho_mtm_por_precos(preco_curva_hoje, preco_curva_hoje)

    assert retorno_acumulado == pytest.approx(0.21)
    assert mtm == pytest.approx(0.0)


def test_mtm_fica_indisponivel_sem_preco_na_curva_da_taxa_compra():
    assert ganho_mtm_por_precos(750.0, None) is None
    analise = analise_suficiencia_tesouro("Prefixado", None)
    assert analise["label"] == "MTM INDISPONÍVEL"
    assert "sinal de venda" in analise["msg"]


def test_selic_nunca_usa_retorno_acumulado_como_sinal_de_timing():
    analise = analise_suficiencia_tesouro("Selic", None)
    assert analise["label"] == "SEM SINAL DE TIMING"
    assert "não deve ser interpretado" in analise["msg"]


def test_educa_usa_ano_como_data_de_conversao():
    meta = tesouro_meta("TEDUCA2034")
    assert meta.tipo == "Educa+"
    assert meta.ano_referencia == 2034
    assert meta.papel_ano == "conversao"
    assert analise_suficiencia_tesouro(meta.tipo, None)["label"] == "REVISÃO DO OBJETIVO"


@pytest.mark.parametrize(
    ("dias", "esperada"),
    [(0, 0.225), (180, 0.225), (181, 0.20), (360, 0.20),
     (361, 0.175), (720, 0.175), (721, 0.15)],
)
def test_aliquota_ir_regressiva_por_lote(dias, esperada):
    assert aliquota_ir_tesouro(dias) == esperada


def test_ganho_liquido_indisponivel_sem_prazo_do_lote():
    assert ganho_liquido_estimado(1_200.0, 1_000.0, None) is None


def test_ganho_liquido_aplica_ir_somente_sobre_ganho_positivo():
    assert ganho_liquido_estimado(1_200.0, 1_000.0, 800) == pytest.approx(170.0)
    assert ganho_liquido_estimado(900.0, 1_000.0, 800) == pytest.approx(-100.0)
