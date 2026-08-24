"""Regressões da classificação exibida no universo de FIIs."""
from core.fii_taxonomy import categoria_fii


def test_categoria_fii_usa_tipo_normalizado_em_vez_do_segmento_do_provedor():
    # "Alimentação" é atividade/ocupação e não categoria de FII de tijolo.
    assert categoria_fii("tijolo") == "Tijolo"
    assert categoria_fii("papel") == "Papel/CRI"
    assert categoria_fii("fof") == "Fundo de Fundos"
    assert categoria_fii("hibrido") == "Híbrido"


def test_categoria_fii_nao_inventa_enquadramento_para_tipo_ausente_ou_invalido():
    assert categoria_fii(None) == "Não classificado"
    assert categoria_fii("desconhecido") == "Não classificado"
